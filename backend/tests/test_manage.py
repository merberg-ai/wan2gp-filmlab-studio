"""Project settings and recoverable deletion (projects, shots, library assets and looks)."""

import json

from tests.test_api import new_project, new_shot
from tests.test_library import asset, default_variant, images, refs_setup, set_refs, upload  # noqa: F401

LTX = "LTX-2 2.3 Distilled 1.0"


def rendered(h, pid, shot_id):
    h.post(f"/api/shots/{shot_id}/approve-and-render")
    h.wait_for(lambda: [t for t in h.project(pid)["takes"] if t["shot_id"] == shot_id], what="take")
    return [t for t in h.project(pid)["takes"] if t["shot_id"] == shot_id]


def media_files(h, pid):
    root = h.studio.trash.media_root / pid
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def delete(h, url):
    return h.c.delete(url, headers={"x-csrf-token": h.csrf})


def patch(h, url, body):
    return h.c.patch(url, json=body, headers={"x-csrf-token": h.csrf})


def test_project_settings_and_shot_defaults(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    r = patch(h, f"/api/projects/{pid}", {"title": "  The Dead March  ", "logline": "Skeletons march",
                                          "default_resolution": "720x1280", "default_frames": 240})
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["title"], p["logline"], p["default_resolution"], p["default_frames"]) == \
        ("The Dead March", "Skeletons march", "720x1280", 241)  # snapped to 8n+1, like a shot
    body = {"title": "Wide", "prompt": "Fog over the trench at dusk"}
    s = h.post(f"/api/projects/{pid}/shots", body).json()
    assert (s["resolution"], s["frames"]) == ("720x1280", 241)
    assert patch(h, f"/api/projects/{pid}", {"default_resolution": "9999x9999"}).status_code == 422
    assert patch(h, f"/api/projects/{pid}", {"title": " "}).status_code == 422
    # The director drafts with the same defaults and sees them.
    h.post(f"/api/projects/{pid}/chat", {"text": "Please draft the opening shot"})
    proj = h.wait_for(lambda: (p := h.project(pid)) and not p["director_busy"] and len(p["shots"]) == 2 and p, what="draft")
    assert proj["shots"][1]["resolution"] == "720x1280"
    view = h.c.portal.call(h.studio.tools.run, pid, "get_project", "{}")
    assert view["default_resolution"] == "720x1280" and view["default_frames"] == 241


def test_project_delete_restore_and_delete_forever(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template=LTX)
    rendered(h, pid, s["id"])
    files = media_files(h, pid)
    assert files
    assert delete(h, f"/api/projects/{pid}").status_code == 200
    assert pid not in [p["id"] for p in h.c.get("/api/projects").json()]
    assert h.c.get(f"/api/projects/{pid}").status_code == 404
    assert h.post(f"/api/projects/{pid}/chat", {"text": "hi"}).status_code == 404
    trash = h.c.get("/api/trash").json()
    assert [p["id"] for p in trash["projects"]] == [pid] and trash["shots"] == []
    assert all(f.exists() for f in files)  # recoverable: nothing removed yet
    assert h.post(f"/api/trash/project/{pid}/restore").status_code == 200
    assert len(h.project(pid)["takes"]) == 1
    # Delete forever: only from Recently deleted, then rows and files go.
    assert delete(h, f"/api/trash/project/{pid}").status_code == 409
    delete(h, f"/api/projects/{pid}")
    r = delete(h, f"/api/trash/project/{pid}")
    assert r.status_code == 200 and r.json()["files_removed"] >= 1
    assert not any(f.exists() for f in files) and not (h.studio.trash.media_root / pid).exists()
    count = lambda t, col="project_id": h.c.portal.call(h.studio.db.fetchone,
                                                         f"SELECT COUNT(*) AS n FROM {t} WHERE {col} = ?", (pid,))["n"]
    assert [count(t) for t in ("shots", "render_jobs", "media", "chat_messages")] == [0, 0, 0, 0]
    assert count("projects", "id") == 0


def test_project_with_a_running_render_cant_be_deleted(make_harness):
    h = make_harness("slow").login()
    pid = new_project(h)
    s = new_shot(h, pid, template=LTX)
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["jobs"] and h.project(pid)["jobs"][0]["status"] == "running", what="running")
    r = delete(h, f"/api/projects/{pid}")
    assert r.status_code == 409 and "still running" in r.json()["detail"]
    r = delete(h, f"/api/shots/{s['id']}")
    assert r.status_code == 409 and "rendering" in r.json()["detail"]


def test_shot_delete_restore_and_delete_forever(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    keep, gone = new_shot(h, pid, template=LTX), new_shot(h, pid, title="Test", template=LTX)
    rendered(h, pid, keep["id"])
    takes = rendered(h, pid, gone["id"])
    h.c.portal.call(h.studio.tools.run, pid, "request_render", json.dumps({"shot_id": keep["id"]}))  # ready: ignored
    assert delete(h, f"/api/shots/{gone['id']}").status_code == 200
    proj = h.project(pid)
    assert [x["id"] for x in proj["shots"]] == [keep["id"]] and all(t["shot_id"] == keep["id"] for t in proj["takes"])
    view = h.c.portal.call(h.studio.tools.run, pid, "get_project", "{}")
    assert [x["id"] for x in view["shots"]] == [keep["id"]]  # the director doesn't see it either
    assert "error" in h.c.portal.call(h.studio.tools.run, pid, "update_shot",
                                      json.dumps({"shot_id": gone["id"], "prompt": "back from the dead, please"}))
    assert [x["id"] for x in h.c.get("/api/trash").json()["shots"]] == [gone["id"]]
    h.post(f"/api/trash/shot/{gone['id']}/restore")
    assert len(h.project(pid)["shots"]) == 2
    delete(h, f"/api/shots/{gone['id']}")
    before = media_files(h, pid)
    r = delete(h, f"/api/trash/shot/{gone['id']}")
    assert r.status_code == 200 and r.json()["files_removed"] >= 1
    after = media_files(h, pid)
    assert len(after) < len(before) and all(f.exists() for f in after)
    assert len(h.project(pid)["takes"]) == 1 and takes  # the other shot's take is untouched
    # A shot in a deleted project can't be restored on its own.
    other = new_shot(h, pid, title="Other", template=LTX)
    delete(h, f"/api/shots/{other['id']}")
    delete(h, f"/api/projects/{pid}")
    r = h.post(f"/api/trash/shot/{other['id']}/restore")
    assert r.status_code == 409 and "restore the project first" in r.json()["detail"]


def test_asset_delete_takes_it_off_shots_and_delete_forever_keeps_shared_files(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None)
    set_refs(h, s["id"], hv, tv)
    rendered(h, pid, s["id"])
    other = asset(h, "prop", "Bugle")
    upload(h, default_variant(other), images[1])  # same picture as The Trench: stored once, shared
    usage = h.c.get(f"/api/library/assets/{trench['id']}/usage").json()
    assert [(u["shot_id"], u["project_title"]) for u in usage] == [(s["id"], "The Dead March")]
    r = delete(h, f"/api/library/assets/{trench['id']}")
    assert r.status_code == 200 and [x["shot_id"] for x in r.json()["removed_from_shots"]] == [s["id"]]
    proj = h.project(pid)
    assert [x["asset_name"] for x in proj["shots"][0]["refs"]] == ["Corporal Hale"]
    assert proj["takes"][0]["provenance"]["references"][1]["asset_name"] == "The Trench"  # the take's record stays
    assert "The Trench" not in [a["name"] for a in h.c.get("/api/library").json()]
    assert "The Trench" not in [a["name"] for a in proj["cast"]]
    assert h.c.get(f"/api/library/assets/{trench['id']}").status_code in (404, 422)
    run = lambda name, **a: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(a))
    assert "error" in run("assign_refs", shot_id=s["id"], refs=[{"variant_id": tv}])
    # Restore brings the asset back, not its old place on the shot.
    h.post(f"/api/trash/asset/{trench['id']}/restore")
    assert "The Trench" in [a["name"] for a in h.c.get("/api/library").json()]
    assert len(h.project(pid)["shots"][0]["refs"]) == 1
    delete(h, f"/api/library/assets/{trench['id']}")
    shared = h.c.get(f"/api/library/assets/{other['id']}").json()["variants"][0]["files"][0]
    path = h.studio.library.path(h.c.portal.call(h.studio.library.file, shared["file_id"]))
    assert delete(h, f"/api/trash/asset/{trench['id']}").status_code == 200
    assert path.exists()  # still used by the Bugle
    delete(h, f"/api/library/assets/{other['id']}")
    assert delete(h, f"/api/trash/asset/{other['id']}").json()["files_removed"] == 1
    assert not path.exists()
    assert h.c.get("/api/trash").json()["assets"] == []


def test_look_delete_rules(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, _, hv, _ = refs_setup(h, images)
    only = h.c.get(f"/api/library/assets/{hale['id']}").json()["variants"]
    assert len(only) == 1
    r = delete(h, f"/api/library/variants/{hv}")
    assert r.status_code == 409 and "only look" in r.json()["detail"]
    night = h.post(f"/api/library/assets/{hale['id']}/variants", {"name": "night"}).json()
    upload(h, night["id"], images[2])
    s = new_shot(h, pid, template=None)
    set_refs(h, s["id"], hv, night["id"])
    h.post(f"/api/library/variants/{night['id']}/lock", {"locked": True})
    r = delete(h, f"/api/library/variants/{night['id']}")
    assert r.status_code == 409 and "unlock it" in r.json()["detail"]
    h.post(f"/api/library/variants/{night['id']}/lock", {"locked": False})
    r = delete(h, f"/api/library/variants/{night['id']}")
    assert r.status_code == 200 and len(r.json()["removed_from_shots"]) == 1
    assert [v["name"] for v in h.c.get(f"/api/library/assets/{hale['id']}").json()["variants"]] == ["default"]
    assert [x["position"] for x in h.project(pid)["shots"][0]["refs"]] == [1]
    trash = h.c.get("/api/trash").json()
    assert [v["id"] for v in trash["variants"]] == [night["id"]]
    assert delete(h, f"/api/trash/variant/{night['id']}").json()["files_removed"] == 1
    assert h.post("/api/trash/widget/x/restore").status_code == 409
