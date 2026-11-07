"""F3: reference library, shot references, capability checks, image generation, model settings."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_api import new_project, new_shot

REF2VA = "MiniMax H3 Ref2VA Pruned PDD 8 Steps"
FL2VA = "MiniMax H3 FL2VA Pruned PDD 8 Steps"
TURBO = "MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps"


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    """Three distinct PNGs (different content, so different hashes)."""
    d = tmp_path_factory.mktemp("refs")
    out = []
    for i, src in enumerate(("testsrc2", "smptebars", "rgbtestsrc")):
        p = d / f"ref{i}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}=size=128x96", "-frames:v", "1",
                        str(p)], check=True)
        out.append(p.read_bytes())
    return out


def asset(h, kind="character", name="Corporal Hale", **extra):
    r = h.post("/api/library/assets", {"kind": kind, "name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def upload(h, variant_id, data, caption="front", status=201):
    r = h.c.post(f"/api/library/variants/{variant_id}/files", params={"caption": caption, "source_note": "owner photo"},
                 content=data, headers={"x-csrf-token": h.csrf, "content-type": "application/octet-stream"})
    assert r.status_code == status, r.text
    return r.json()


def model_issues(issues):
    """Issues about the model, without the prompt hints (<Picture N> naming, setting, speech)."""
    return [i for i in issues if not any(k in i["message"] for k in ("<Picture", "Enhance can rewrite", "Sound isn't set"))]


def default_variant(a):
    return a["variants"][0]["id"]


def test_upload_dedupes_and_rejects_non_images(make_harness, images):
    h = make_harness().login()
    hale = asset(h)
    v = default_variant(hale)
    first = upload(h, v, images[0])
    assert first["deduplicated"] is False
    again = upload(h, v, images[0])
    assert again["already_attached"] and again["file_id"] == first["file_id"]
    trench = asset(h, "location", "The Trench")
    other = upload(h, default_variant(trench), images[0], caption="same picture, other asset")
    assert other["deduplicated"] is True and other["file_id"] == first["file_id"]
    upload(h, v, b"definitely not an image", status=422)
    upload(h, v, b"", status=422)
    thumb = h.c.get(f"/api/library/files/{first['file_id']}", params={"thumb": 1})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    full = h.c.get(f"/api/library/files/{first['file_id']}")
    assert full.content == images[0]  # originals are kept byte for byte
    files = h.c.get(f"/api/library/assets/{hale['id']}").json()["variants"][0]["files"]
    assert [f["caption"] for f in files] == ["front"] and files[0]["source"] == "upload"


def test_library_needs_owner(make_harness):
    h = make_harness()
    assert h.c.get("/api/library").status_code == 401
    assert h.c.post("/api/library/assets", json={"kind": "prop", "name": "Bugle"}).status_code in (401, 403)


def test_search_finds_names_tags_and_captions(make_harness, images):
    h = make_harness().login()
    hale = asset(h, tags="soldier skeleton")
    upload(h, default_variant(hale), images[0], caption="gas mask profile")
    asset(h, "prop", "Broken bugle")
    found = lambda q: [a["name"] for a in h.c.get("/api/library", params={"q": q}).json()]
    assert found("skel") == ["Corporal Hale"]
    assert found("gas mask") == ["Corporal Hale"]
    assert found("bugle") == ["Broken bugle"]
    assert [a["name"] for a in h.c.get("/api/library", params={"kind": "prop"}).json()] == ["Broken bugle"]


def test_locked_variant_is_immutable_until_new_version(make_harness, images):
    h = make_harness().login()
    hale = asset(h)
    v = default_variant(hale)
    upload(h, v, images[0])
    assert h.post(f"/api/library/variants/{v}/lock", {"locked": True}).json()["locked"] == 1
    upload(h, v, images[1], status=422)
    assert h.c.patch(f"/api/library/variants/{v}", json={"name": "x"},
                     headers={"x-csrf-token": h.csrf}).status_code == 422
    v2 = h.post(f"/api/library/variants/{v}/new-version").json()
    assert v2["version"] == 2 and v2["locked"] == 0 and v2["parent_id"] == v
    upload(h, v2["id"], images[1], caption="new costume")
    variants = h.c.get(f"/api/library/assets/{hale['id']}").json()["variants"]
    assert [(x["version"], len(x["files"])) for x in variants] == [(1, 1), (2, 2)]


def refs_setup(h, images):
    hale, trench = asset(h), asset(h, "location", "The Trench")
    hv, tv = default_variant(hale), default_variant(trench)
    upload(h, hv, images[0])
    upload(h, tv, images[1], caption="dawn")
    return hale, trench, hv, tv


def set_refs(h, shot_id, *variant_ids, status=200):
    r = h.c.put(f"/api/shots/{shot_id}/refs", json={"refs": [{"variant_id": v} for v in variant_ids]},
                headers={"x-csrf-token": h.csrf})
    assert r.status_code == status, r.text
    return r.json()


def test_shot_refs_follow_defaults_upload_once_and_land_in_provenance(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s4 = new_shot(h, pid, template=None)
    assert s4["template"] == FL2VA and s4["template_explicit"] == 0  # no refs yet: MiniMax H3 without references
    legend = set_refs(h, s4["id"], hv, tv)
    assert h.project(pid)["shots"][0]["template"] == REF2VA  # switches as soon as references are set
    assert [(r["position"], r["asset_name"]) for r in legend] == [(1, "Corporal Hale"), (2, "The Trench")]
    checks = h.c.get(f"/api/shots/{s4['id']}/checks").json()
    assert checks["template"] == REF2VA and model_issues(checks["issues"]) == []
    assert h.post(f"/api/shots/{s4['id']}/approve-and-render").status_code == 200
    proj = h.wait_for(lambda: (p := h.project(pid))["takes"] and p, what="take")
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]
    assert gen["settings"]["model_type"] == "minimax_h3_ref2va_pruned_pdd"
    ref_uploads = [u["media_id"] for u in h.wangp.uploads][1:]  # the first upload is the capability probe image
    assert gen["settings"]["image_refs"] == ref_uploads and len(ref_uploads) == 2
    prov = proj["takes"][0]["provenance"]["references"]
    assert [(r["position"], r["asset_name"], r["version"]) for r in prov] == [(1, "Corporal Hale", 1), (2, "The Trench", 1)]
    assert all(len(r["sha256"]) == 64 and r["wangp_media_id"].startswith("visual:") for r in prov)
    assert proj["shots"][0]["template"] == REF2VA and [r["asset_name"] for r in proj["shots"][0]["refs"]] == \
        ["Corporal Hale", "The Trench"]
    assert {a["name"] for a in proj["cast"]} == {"Corporal Hale", "The Trench"}

    s5 = new_shot(h, pid, title="Second", template=None)
    set_refs(h, s5["id"], hv, tv)
    before = len(h.wangp.uploads)
    h.post(f"/api/shots/{s5['id']}/approve-and-render")
    h.wait_for(lambda: len(h.project(pid)["takes"]) == 2, what="second take")
    assert len(h.wangp.uploads) == before  # cached gallery ids were reused


def test_limitations_are_reported_before_render(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    # Reference template with a missing LoRA: blocked, nothing submitted.
    turbo = new_shot(h, pid, template=TURBO)
    set_refs(h, turbo["id"], hv)
    r = h.post(f"/api/shots/{turbo['id']}/approve-and-render")
    assert r.status_code == 409 and "LoRA" in r.json()["detail"]
    # A refused render leaves the shot as it was (still a draft, still editable), with nothing queued.
    shot = next(x for x in h.project(pid)["shots"] if x["id"] == turbo["id"])
    assert shot["status"] == "draft"
    assert h.c.patch(f"/api/shots/{turbo['id']}", json={"title": "still editable"},
                     headers={"x-csrf-token": h.csrf}).status_code == 200
    # A template without reference support, given references: blocked.
    i2v = new_shot(h, pid, template="Enhanced Lightning v2")
    set_refs(h, i2v["id"], hv)
    issues = h.c.get(f"/api/shots/{i2v['id']}/checks").json()["issues"]
    assert issues[0]["level"] == "block" and "takes no reference" in issues[0]["message"]
    assert h.post(f"/api/shots/{i2v['id']}/approve-and-render").status_code == 409
    # A reference-only template without references: blocked.
    ref_only = new_shot(h, pid, template=REF2VA)
    assert "needs at least one reference" in h.post(f"/api/shots/{ref_only['id']}/approve-and-render").json()["detail"]
    # Several refs on a single-reference template: a warning, not a block.
    ltx = new_shot(h, pid, template="LTX-2 2.3 Distilled 1.0")
    set_refs(h, ltx["id"], hv, tv)
    issues = model_issues(h.c.get(f"/api/shots/{ltx['id']}/checks").json()["issues"])
    assert [i["level"] for i in issues] == ["warn"] and "only Picture 1" in issues[0]["message"]
    # Files not downloaded: a warning.
    plain = new_shot(h, pid, template=None)
    assert [i["level"] for i in model_issues(h.c.get(f"/api/shots/{plain['id']}/checks").json()["issues"])] == ["warn"]
    assert not [n for n, a in h.wangp.calls if n == "wangp_generate"]


def test_lost_reference_upload_is_uploaded_again(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, _ = refs_setup(h, images)
    s = new_shot(h, pid, template=None)
    set_refs(h, s["id"], hv)
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["takes"], what="first take")
    h.wangp.known_media.clear()  # WanGP's gallery was cleared
    assert h.post(f"/api/shots/{s['id']}/approve-and-render").status_code == 200
    h.wait_for(lambda: len(h.project(pid)["takes"]) == 2, what="take after re-upload")
    rids = [a["request_id"] for n, a in h.wangp.calls if n == "wangp_generate"]
    assert rids[-1].endswith("-r1")  # the retry used a fresh request id


def test_shot_refs_rules(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale = asset(h)
    empty = default_variant(hale)
    s = new_shot(h, pid)
    assert "has no images" in set_refs(h, s["id"], empty, status=422)["detail"]
    upload(h, empty, images[0])
    assert h.c.put(f"/api/shots/{s['id']}/refs", json={"refs": [{"variant_id": empty, "file_id": "nope"}]},
                   headers={"x-csrf-token": h.csrf}).status_code == 422
    assert len(set_refs(h, s["id"], empty)) == 1
    assert set_refs(h, s["id"]) == []  # clearing is allowed


def test_image_generation_candidates_keep_and_discard(make_harness, images):
    h = make_harness().login()
    trench = asset(h, "location", "The Trench")
    v = default_variant(trench)
    r = h.post(f"/api/library/variants/{v}/generate", {"prompt": "A flooded WWI trench at dawn, yellow-green gas"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["template"] == "Z Image Turbo" and job["count"] == 2
    view = h.wait_for(lambda: (a := h.c.get(f"/api/library/assets/{trench['id']}").json())["images"]["jobs"][0]["status"]
                      == "done" and a, what="image job done")
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]
    assert isinstance(gen["settings"], list) and len(gen["settings"]) == 2 and gen["request_id"].startswith("filmlab-img-")
    cands = view["images"]["candidates"]
    # The fake returns the same bytes for both candidates: they dedupe to one library file, two candidate rows.
    assert len(cands) == 2 and cands[0]["seed"] == 424242
    assert h.post(f"/api/library/candidates/{cands[0]['id']}/keep", {"caption": "dawn, wide"}).status_code == 200
    assert h.post(f"/api/library/candidates/{cands[1]['id']}/discard").status_code == 200
    a = h.c.get(f"/api/library/assets/{trench['id']}").json()
    f = a["variants"][0]["files"][0]
    assert f["source"] == "generated" and f["caption"] == "dawn, wide" and "Z Image Turbo" in f["source_note"]
    assert f["provenance"]["prompt"].startswith("A flooded") and f["provenance"]["seed"] == 424242
    assert a["images"]["candidates"] == []


def test_image_generation_from_references_uses_the_edit_model(make_harness, images):
    h = make_harness().login()
    hale = asset(h)
    v = default_variant(hale)
    front = upload(h, v, images[0])
    r = h.post(f"/api/library/variants/{v}/generate",
               {"prompt": "Same soldier, side view", "input_file_ids": [front["file_id"]], "count": 1})
    assert r.status_code == 201, r.text
    assert r.json()["template"] == "Flux Klein 9B"
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]
    assert gen["settings"][0]["image_refs"] == [h.wangp.uploads[-1]["media_id"]]
    # Text-only model given references: refused before anything is queued.
    r = h.post(f"/api/library/variants/{v}/generate",
               {"prompt": "x side", "template": "Z Image Turbo", "input_file_ids": [front["file_id"]]})
    assert r.status_code == 409 and "takes no reference" in r.json()["detail"]


def test_settings_and_project_overrides(make_harness):
    h = make_harness().login()
    caps = h.c.get("/api/capabilities").json()
    rows = {(t["task"], t["template"]): t for t in caps["templates"]}
    assert rows[("gen_video_with_refs", TURBO)]["valid"] is False and "LoRA" in rows[("gen_video_with_refs", TURBO)]["reason"]
    assert rows[("gen_video", FL2VA)]["installed"] is False
    assert rows[("gen_video_with_refs", REF2VA)]["refs"] == "multiple"
    assert caps["settings"]["video_refs"] == REF2VA and caps["settings"]["video_plain"] == FL2VA
    put = lambda body: h.c.put("/api/settings/templates", json=body, headers={"x-csrf-token": h.csrf})
    assert put({"video_refs": TURBO}).status_code == 422
    assert put({"video_plain": "LTX-2 2.3 Distilled 1.0"}).json()["video_plain"] == "LTX-2 2.3 Distilled 1.0"
    pid = new_project(h)
    assert new_shot(h, pid, template=None)["template"] == "LTX-2 2.3 Distilled 1.0"
    r = h.c.patch(f"/api/projects/{pid}/models", json={"video_plain_template": FL2VA}, headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200 and r.json()["video_plain_template"] == FL2VA
    assert new_shot(h, pid, title="b", template=None)["template"] == FL2VA


def test_director_assigns_refs_and_only_requests_images(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, trench, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None)
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    assets = run("list_assets", query="hale")["assets"]
    assert [a["name"] for a in assets] == ["Corporal Hale"]
    looks = run("get_asset", asset_id=assets[0]["id"])["variants"]
    assert looks[0]["variant_id"] == hv and looks[0]["images"]
    res = run("assign_refs", shot_id=s["id"], refs=[{"variant_id": hv}, {"variant_id": tv}])
    assert res["references"][0].startswith("<Picture 1> = Corporal Hale")
    tpl = run("list_video_templates")
    assert tpl["defaults"]["with_references"] == REF2VA
    req = run("request_image", variant_id=tv, prompt="The trench at night")
    assert req["status"] == "pending"
    assert not [n for n, a in h.wangp.calls if n == "wangp_generate"]  # nothing generated by the director
    pending = h.c.get(f"/api/library/assets/{trench['id']}").json()["image_requests"]
    assert len(pending) == 1 and pending[0]["requested_by"] == "director"
    assert h.post(f"/api/library/image-requests/{pending[0]['id']}/approve").status_code == 201
    # A rendered shot can be changed for its next take (and stays ready); a rendering one can't.
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["shots"][0]["status"] == "ready", what="ready")
    assert len(run("assign_refs", shot_id=s["id"], refs=[{"variant_id": hv}])["references"]) == 1
    assert run("update_shot", shot_id=s["id"], prompt="<Picture 1> in close-up, rain", rationale="tighter")["shot"]["status"] == "ready"
    h.c.portal.call(h.studio.db.execute, "UPDATE shots SET status = 'rendering' WHERE id = ?", (s["id"],))
    assert "approved or rendering" in run("assign_refs", shot_id=s["id"], refs=[])["error"]
    assert "approved or rendering" in run("update_shot", shot_id=s["id"], prompt="something else entirely", rationale="x")["error"]


def test_enhance_asks_director_to_rewrite_prompt_around_pictures(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    bare = new_shot(h, pid, template=None)
    s = new_shot(h, pid, template=None)
    set_refs(h, s["id"], hv, tv)
    assert h.post(f"/api/shots/{s['id']}/enhance").status_code == 202
    shot = h.wait_for(lambda: (p := h.project(pid)) and not p["director_busy"]
                      and next(x for x in p["shots"] if x["id"] == s["id"]), what="enhanced")
    assert shot["prompt"].startswith("<Picture 1> crouches in <Picture 2>")
    brief = h.c.get(f"/api/projects/{pid}/chat").json()[0]["content"]
    assert "<Picture 1> = Corporal Hale" in brief and "<Picture 2> = The Trench" in brief and "don't request a render" in brief
    from app.director.enhance import _legend
    many = [{"position": i, "asset_id": "h", "asset_name": "Hale", "kind": "character", "variant_name": "default",
             "version": 1} for i in (1, 2)] + [{"position": 3, "asset_id": "t", "asset_name": "Trench", "kind": "location",
                                               "variant_name": "default", "version": 1}]
    legend = _legend(many)
    assert legend[0].startswith("- <Picture 1>, <Picture 2> = Hale") and "SAME character" in legend[0]
    assert legend[1] == "- <Picture 3> = Trench (location) · default v1"
    jason = dict(many[0], asset_id="j", asset_name="Jason", asset_description="Hockey mask,  machete", position=4)
    assert _legend([jason])[0] == "- <Picture 4> = Jason (character) · default v1: Hockey mask, machete"
    from app.director.enhance import enhance_brief
    shot = {"id": "s", "position": 1, "title": "t", "prompt": "old enhanced prompt", "frames": 121, "resolution": "1280x720"}
    assert "Every character must be clearly visible" in enhance_brief(shot, many + [jason], [])
    assert "Every character must be clearly visible" not in enhance_brief(shot, many, [])
    assert "from scratch" in enhance_brief(shot, many, []) and "My idea for it: “old enhanced prompt”" in enhance_brief(shot, many, [])
    assert not [n for n, _ in h.wangp.calls if n == "wangp_generate"]
    # A shot without references: the brief asks the director to pick them from the library (cast listed).
    h.studio.chat._active.add(pid)
    assert h.post(f"/api/shots/{bare['id']}/enhance").status_code == 409  # director busy
    h.studio.chat._active.discard(pid)
    assert h.post(f"/api/shots/{bare['id']}/enhance").status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    brief = [m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"]
    assert "no references yet" in brief and "Corporal Hale (character)" in brief and "assign_refs" in brief
    # A rendered shot can be enhanced for its next take; it stays ready with its takes.
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: next(x for x in h.project(pid)["shots"] if x["id"] == s["id"])["status"] == "ready", what="ready")
    assert h.post(f"/api/shots/{s['id']}/enhance").status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    proj = h.project(pid)
    assert next(x for x in proj["shots"] if x["id"] == s["id"])["status"] == "ready"
    assert [t for t in proj["takes"] if t["shot_id"] == s["id"]]
    h.c.portal.call(h.studio.db.execute, "UPDATE shots SET status = 'rendering' WHERE id = ?", (s["id"],))
    assert h.post(f"/api/shots/{s['id']}/enhance").status_code == 409


def test_director_refs_accept_shorthand_and_tool_bugs_dont_crash_the_turn(make_harness, images, monkeypatch):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None)
    run = lambda name, args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    assert run("assign_refs", {"shot_id": s["id"], "refs": [hv, tv]})["references"][1].startswith("<Picture 2> = The Trench")
    assert len(run("assign_refs", {"shot_id": s["id"], "refs": json.dumps([{"variant_id": tv}])})["references"]) == 1
    assert "variant_id" in run("assign_refs", {"shot_id": s["id"], "refs": [42]})["error"]
    # Image ids alone (what models tend to send) resolve to the look that holds them.
    files = {v["variant_id"]: v["images"][0]["file_id"]
             for a in run("list_assets", {})["assets"] for v in run("get_asset", {"asset_id": a["id"]})["variants"]}
    res = run("assign_refs", {"shot_id": s["id"], "refs": [files[hv], {"file_id": files[tv]}]})
    assert [r.split(" = ")[1].split(" ·")[0] for r in res["references"]] == ["Corporal Hale", "The Trench"]
    assert "neither a variant_id nor an image" in run("assign_refs", {"shot_id": s["id"], "refs": ["nope"]})["error"]

    async def boom(*a, **k):
        raise KeyError("x")
    monkeypatch.setattr(h.studio.library, "search", boom)
    assert "internal error in list_assets" in run("list_assets", {})["error"]


def test_prompt_picture_names_are_checked_against_refs(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None)
    checks = lambda: [i["message"] for i in h.c.get(f"/api/shots/{s['id']}/checks").json()["issues"]]
    set_refs(h, s["id"], hv)
    assert any("doesn't name its references" in m for m in checks())
    h.c.patch(f"/api/shots/{s['id']}", json={"prompt": "<Picture 1> walks into <Picture 2> at dusk"},
              headers={"x-csrf-token": h.csrf})
    assert any("names <Picture 2> but the shot has only 1 reference" in m for m in checks())
    set_refs(h, s["id"], hv, tv)
    assert not [m for m in checks() if "Picture" in m]


def test_enhance_always_starts_from_the_owners_idea(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None, prompt="the trench is on fire")
    assert s["idea"] == "the trench is on fire"
    set_refs(h, s["id"], hv, tv)
    briefs = []
    for _ in range(2):  # the fake director rewrites the prompt each time
        assert h.post(f"/api/shots/{s['id']}/enhance").status_code == 202
        h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
        briefs.append([m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"])
    shot = h.project(pid)["shots"][0]
    assert shot["prompt"].startswith("<Picture 1>") and shot["idea"] == "the trench is on fire"
    assert all("My idea for it: “the trench is on fire”" in b for b in briefs)
    # The director can't touch the idea; only the owner changes it, explicitly (a prompt edit leaves it).
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    run("update_shot", shot_id=s["id"], prompt="<Picture 1> walks away in the rain", idea="hijacked", rationale="r")
    assert h.project(pid)["shots"][0]["idea"] == "the trench is on fire"
    h.c.patch(f"/api/shots/{s['id']}", json={"prompt": "Hale walks away from the fire, slowly"},
              headers={"x-csrf-token": h.csrf})
    assert h.project(pid)["shots"][0]["idea"] == "the trench is on fire"
    h.c.patch(f"/api/shots/{s['id']}", json={"idea": "Hale and Jason face off"}, headers={"x-csrf-token": h.csrf})
    shot = h.project(pid)["shots"][0]
    assert shot["idea"] == "Hale and Jason face off" and shot["prompt"] == "Hale walks away from the fire, slowly"


def test_setting_and_speech_checks():
    from app.capabilities import _setting_issues, _speech_issues
    setting = "Night, Suburban Street"
    assert not _setting_issues("Anthony walks down a suburban street on a rainy night.", setting)
    miss = _setting_issues("Close-up of Anthony glancing over his shoulder; street lighting.", setting)
    assert miss and "night, suburban" in miss[0]["message"]
    assert not _setting_issues("anything", "")
    assert _speech_issues("Sound of rain and sharp breathing.")
    assert not _speech_issues("Sound of rain. No dialogue, no voices.")
    assert not _speech_issues("Nobody speaks; only the rain.")
    assert not _speech_issues('He whispers “Who is there?” into the dark.')


def test_enhance_brief_carries_scene_and_rules():
    from app.director.enhance import SETTING_RULE, SOUND_RULE, enhance_brief
    shot = {"id": "s1", "position": 2, "title": "Intuition", "idea": "he senses something", "prompt": "x",
            "frames": 121, "resolution": "832x480"}
    scene = {"title": "The Rainy Night", "time_place": "Night, Suburban Street", "mood": "Oppressive, rainy"}
    b = enhance_brief(shot, [], [], scene, "Anthony walks home on a dark rainy night.")
    assert "Night, Suburban Street" in b and "mood: Oppressive" in b and "dark rainy night" in b
    assert SETTING_RULE in b and SOUND_RULE in b


def test_usage_lists_the_shots_and_a_look_swaps_everywhere(make_harness, images):
    h = make_harness().login()
    hale, trench, hv, tv = refs_setup(h, images)
    pid = new_project(h)
    a, b, c = (new_shot(h, pid, title=t) for t in ("one", "two", "three"))
    for shot in (a, b, c):
        set_refs(h, shot["id"], hv, tv)
    h.post(f"/api/shots/{c['id']}/lock", {"locked": True})
    v2 = h.post(f"/api/library/variants/{hv}/new-version").json()
    upload(h, v2["id"], images[2], caption="winter coat")
    usage = h.c.get(f"/api/library/assets/{hale['id']}/usage").json()
    assert [u["title"] for u in usage] == ["one", "two", "three"] and usage[0]["variant_id"] == hv
    out = h.post(f"/api/library/assets/{hale['id']}/swap-look", {"to_variant_id": v2["id"]}).json()
    assert [x["title"] for x in out["changed"]] == ["one", "two"] and out["skipped"][0]["reason"] == "it's locked"
    refs = h.c.get(f"/api/shots/{a['id']}/refs").json()
    assert [r["variant_id"] for r in refs] == [v2["id"], tv]  # same place, the trench untouched
    assert h.c.get(f"/api/shots/{c['id']}/refs").json()[0]["variant_id"] == hv
    revs = h.c.get(f"/api/shots/{a['id']}/revisions").json()
    assert "Corporal Hale" in revs[-1]["rationale"] and "refs" in revs[-1]["changed"]
    bad = h.post(f"/api/library/assets/{hale['id']}/swap-look", {"to_variant_id": tv})
    assert bad.status_code == 422
