"""Preview renders: a quick low-resolution pass before full quality."""

from app.renders import preview_resolution
from tests.test_api import new_project, new_shot
from tests.test_storyboard import patch, three_shots, wait_batch


def test_preview_resolution_halves_each_side_on_a_valid_grid():
    assert preview_resolution("960x544") == "480x288"
    assert preview_resolution("1280x720") == "640x352"
    assert preview_resolution("832x480") == "416x256"
    assert preview_resolution("512x288") == "256x256"  # never below 256 a side
    assert preview_resolution("320x240") == "256x240"  # nor larger than the shot itself


def test_a_preview_render_is_low_resolution_and_marked(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    r = h.post(f"/api/shots/{shot['id']}/approve-and-render", {"preview": True})
    assert r.status_code == 200, r.text
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "ready" and p, what="preview ready")
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]["settings"]
    assert gen["resolution"] == preview_resolution(proj["shots"][0]["resolution"])
    assert gen["video_length"] == proj["shots"][0]["frames"]  # same length, prompt and seed: only smaller
    assert proj["jobs"][-1]["preview"] is True and proj["takes"][-1]["preview"] is True
    # the edit uses it for now (a rough cut), says so, and prefers a full-quality take once there is one
    tl = h.c.get(f"/api/projects/{pid}/edit").json()
    assert tl["segments"][0]["preview"] is True and any("preview take" in i for i in tl["issues"])
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code == 200  # no body: full quality
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "ready" and len(p["takes"]) == 2 and p,
                      what="full take")
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]["settings"]
    assert gen["resolution"] == proj["shots"][0]["resolution"] and proj["takes"][-1]["preview"] is False
    tl = h.c.get(f"/api/projects/{pid}/edit").json()
    assert tl["segments"][0]["take_id"] == proj["takes"][-1]["id"] and not any("preview" in i for i in tl["issues"])


def test_a_preview_batch_renders_every_shot_as_a_preview(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    r = h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids, "preview": True})
    assert r.status_code == 201, r.text
    wait_batch(h, pid, lambda b: b["status"] == "done", "done")
    proj = h.project(pid)
    assert len(proj["takes"]) == 3 and all(t["preview"] for t in proj["takes"])
    sizes = {a["settings"]["resolution"] for n, a in h.wangp.calls if n == "wangp_generate"}
    assert sizes == {preview_resolution(s["resolution"]) for s in proj["shots"]}


def test_history_lists_the_takes_each_version_rendered(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render", {"preview": True})
    h.wait_for(lambda: h.project(pid)["shots"][0]["status"] == "ready", what="take 1")
    patch(h, f"/api/shots/{shot['id']}", {"prompt": "Grey fog over a ruined trench at dawn, wide"})
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    h.wait_for(lambda: len(h.project(pid)["takes"]) == 2 and h.project(pid)["shots"][0]["status"] == "ready", what="take 2")
    revs = h.c.get(f"/api/shots/{shot['id']}/revisions").json()
    takes = [t["id"] for t in h.project(pid)["takes"]]
    assert [[t["id"] for t in r["takes"]] for r in revs] == [[takes[0]], [takes[1]]]
    assert revs[0]["takes"][0]["preview"] is True and revs[1]["takes"][0]["preview"] is False
    checks = h.c.get(f"/api/projects/{pid}/checks").json()["shots"]
    assert isinstance(checks, dict)
