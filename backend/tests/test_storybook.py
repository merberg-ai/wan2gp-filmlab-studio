"""The Storybook: the book document, frames, the director's story text, and the server-drawn exports."""

import io
import zipfile

from tests.test_edit import edit, patch, rendered_shot, small_project


def book(h, pid):
    r = h.c.get(f"/api/projects/{pid}/storybook")
    assert r.status_code == 200, r.text
    return r.json()


def patch_book(h, pid, body):
    return h.c.patch(f"/api/projects/{pid}/storybook", headers={"x-csrf-token": h.csrf}, json=body)


def wait_export(h, pid, eid):
    return h.wait_for(lambda: (e := next(x for x in book(h, pid)["exports"] if x["id"] == eid))
                      and e["status"] not in ("queued", "running") and e, timeout=60, what="storybook export")


def test_book_follows_storyboard_and_pins_takes(make_harness):
    h = make_harness().login()
    pid = small_project(h, "The Fog")
    a = rendered_shot(h, pid, title="Wait")
    b = rendered_shot(h, pid, title="March")
    bk = book(h, pid)
    assert [p["shot_id"] for p in bk["panels"]] == [a["id"], b["id"]]
    assert bk["doc"]["layout"] == "picture" and bk["doc"]["cover"]["title"] == "The Fog"
    assert [pg["shot_ids"] for pg in bk["pages"]] == [[a["id"]], [b["id"]]]  # one picture a page
    first = bk["panels"][0]
    assert first["take_id"] and first["t"] > 0 and first["new_take_id"] is None  # the middle of the take
    # A frame of the take, cached; a crop too.
    r = h.c.get(f"/api/storybook/frame/{first['take_id']}?t=0.5")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert h.c.get(f"/api/storybook/frame/{first['take_id']}?t=0.5&crop=0.1,0.1,0.5,0.5").status_code == 200
    assert h.c.get(f"/api/storybook/frame/{first['take_id']}?crop=0,0,0.001,0.001").status_code == 404
    # A new take of a shot is offered, never swapped in.
    h.post(f"/api/shots/{a['id']}/approve-and-render")
    h.wait_for(lambda: len([t for t in h.project(pid)["takes"] if t["shot_id"] == a["id"]]) == 2, what="second take")
    p = book(h, pid)["panels"][0]
    assert p["take_id"] == first["take_id"] and p["new_take_id"] and p["new_take_id"] != first["take_id"]
    # Comic: panels share pages; skip and break_before; per-shot edits merge; the history restores.
    r = patch_book(h, pid, {"layout": "comic", "per_page": 3, "cover": {"author": "Jim"},
                            "panels": {a["id"]: {"take_id": p["new_take_id"], "t": None, "narration": "  Dawn.  "}}})
    assert r.status_code == 200, r.text
    bk = r.json()
    assert bk["exports"] == [] and "writer" in bk  # every change answers like GET (the page reads exports)
    assert bk["pages"] == [{"chapter": None, "shot_ids": [a["id"], b["id"]]}]
    assert bk["panels"][0]["narration"] == "Dawn." and bk["panels"][0]["new_take_id"] is None
    assert bk["doc"]["cover"]["author"] == "Jim"
    bk = patch_book(h, pid, {"panels": {b["id"]: {"break_before": True}}}).json()
    assert [pg["shot_ids"] for pg in bk["pages"]] == [[a["id"]], [b["id"]]]
    assert patch_book(h, pid, {"per_page": 9}).status_code == 422
    assert patch_book(h, pid, {"panels": {a["id"]: {"colour": "red"}}}).status_code == 422
    bk = patch_book(h, pid, {"panels": {b["id"]: {"skip": True}}}).json()
    assert [pg["shot_ids"] for pg in bk["pages"]] == [[a["id"]]]
    before_break = bk["revisions"][2]  # newest first: skip, break, comic
    bk = h.post(f"/api/projects/{pid}/storybook/revisions/{before_break['id']}/restore").json()
    assert [pg["shot_ids"] for pg in bk["pages"]] == [[a["id"], b["id"]]]


def test_director_writes_the_story_for_review(make_harness):
    h = make_harness().login()
    pid = small_project(h, "The Fog")
    a = rendered_shot(h, pid, title="Wait")
    b = rendered_shot(h, pid, title="March")
    r = h.post(f"/api/projects/{pid}/storybook/write", {"note": "for children"})
    assert r.status_code == 202, r.text
    plan = h.wait_for(lambda: (w := book(h, pid)["writer"]) and w["status"] == "ready" and w, what="story proposal")
    brief = next(m["content"] for rq in h.gateway.requests for m in rq["messages"]
                 if m["role"] == "user" and "Write the story" in m["content"])
    assert "for children" in brief and f"shot_id {a['id']}" in brief and "picture book" in brief
    assert plan["title"] == "The Long Wait" and [i["shot_id"] for i in plan["items"]] == [a["id"], b["id"]]
    assert book(h, pid)["panels"][0]["narration"] == ""  # proposed only
    texts = {i["shot_id"]: i["text"] for i in plan["items"]} | {b["id"]: "They marched."}
    r = h.post(f"/api/projects/{pid}/storybook/write/apply", {"texts": texts, "title": plan["title"]})
    assert r.status_code == 200, r.text
    bk = book(h, pid)
    assert bk["panels"][0]["narration"].startswith("Page 1:") and bk["panels"][1]["narration"] == "They marched."
    assert bk["doc"]["cover"]["title"] == "The Long Wait" and bk["writer"] is None
    # Outside a request to write, the tool refuses.
    out = h.c.portal.call(h.studio.tools.run, pid, "propose_story", '{"pages": [{"shot_id": "x", "text": "y"}]}')
    assert "error" in out


def test_export_draws_pdf_pages_and_cbz(make_harness):
    h = make_harness().login()
    pid = small_project(h, "The Fog")
    assert h.post(f"/api/projects/{pid}/storybook/exports").status_code == 409  # no pages yet
    a = rendered_shot(h, pid, title="Wait")
    rendered_shot(h, pid, title="March")
    patch(h, f"/api/shots/{a['id']}", {"sound": {"lines": [{"speaker": "Hale", "text": "Hold the line, lads."}]}})
    for layout in ("picture", "comic"):
        patch_book(h, pid, {"layout": layout, "cover": {"subtitle": "A trench tale"},
                            "panels": {a["id"]: {"narration": "The fog came in slow and grey, and nobody spoke."}}})
        r = h.post(f"/api/projects/{pid}/storybook/exports")
        assert r.status_code == 201, r.text
        e = wait_export(h, pid, r.json()["id"])
        assert e["status"] == "done", e
        assert e["pages"] == (3 if layout == "picture" else 2) and e["layout"] == layout  # cover + pages
        pdf = h.c.get(f"/api/storybook-exports/{e['id']}/file/pdf?download=1")
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
        assert "attachment" in pdf.headers["content-disposition"]
        png = h.c.get(f"/api/storybook-exports/{e['id']}/file/page-2")
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
        z = zipfile.ZipFile(io.BytesIO(h.c.get(f"/api/storybook-exports/{e['id']}/file/cbz").content))
        assert len(z.namelist()) == e["pages"] and z.namelist()[0] == "page-001.jpg"
    assert h.c.get(f"/api/storybook-exports/{e['id']}/file/nope").status_code == 404
    assert h.c.delete(f"/api/storybook-exports/{e['id']}", headers={"x-csrf-token": h.csrf}).status_code == 200
    assert all(x["id"] != e["id"] for x in book(h, pid)["exports"])
    assert edit(h, pid)["segments"]  # the edit is untouched by the book
