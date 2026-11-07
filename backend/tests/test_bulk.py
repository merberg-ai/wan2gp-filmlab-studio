"""Storyboard bulk actions: several shots or whole scenes at once, and undoing a bulk delete."""

from tests.test_api import new_project
from tests.test_storyboard import three_shots


def test_bulk_delete_of_a_scene_and_a_shot_and_undo(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)  # scene A: one, two · scene B: three
    proj = h.project(pid)
    scene_b = next(sc["id"] for sc in proj["scenes"] if sc["title"] == "B")
    h.post(f"/api/shots/{ids[1]}/lock", {"locked": True})  # "two" is locked: it stays
    r = h.post(f"/api/projects/{pid}/bulk", {"action": "delete", "shot_ids": ids[:2], "scene_ids": [scene_b]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["done"] == [ids[0], ids[2]] and [x["shot_id"] for x in out["skipped"]] == [ids[1]]
    assert out["scenes_deleted"] == [scene_b] and out["undo"]
    proj = h.project(pid)
    assert [s["id"] for s in proj["shots"]] == [ids[1]] and [sc["title"] for sc in proj["scenes"]] == ["A"]
    # undo puts every shot back in its scene and its place
    r = h.post(f"/api/projects/{pid}/bulk/undo", {"token": out["undo"]})
    assert r.status_code == 200, r.text
    proj = h.project(pid)
    assert [s["id"] for s in proj["shots"]] == ids and [s["position"] for s in proj["shots"]] == [1, 2, 3]
    assert [sc["title"] for sc in proj["scenes"]] == ["A", "B"]
    assert {s["id"]: s["scene_id"] for s in proj["shots"]}[ids[2]] == scene_b
    assert h.post(f"/api/projects/{pid}/bulk/undo", {"token": out["undo"]}).status_code == 409  # only once


def test_bulk_lock_and_set_skip_what_they_cant_change(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    assert h.post(f"/api/projects/{pid}/bulk", {"action": "lock", "shot_ids": ids[:1]}).json()["done"] == ids[:1]
    out = h.post(f"/api/projects/{pid}/bulk", {"action": "set", "shot_ids": ids,
                                                "fields": {"resolution": "832x480", "frames": 97}}).json()
    assert out["done"] == ids[1:] and out["skipped"][0]["reason"] == "it's locked"
    shots = {s["id"]: s for s in h.project(pid)["shots"]}
    assert [shots[i]["resolution"] for i in ids] == [shots[ids[0]]["resolution"], "832x480", "832x480"]
    assert shots[ids[1]]["frames"] == 97
    revs = h.c.get(f"/api/shots/{ids[1]}/revisions").json()
    assert revs[-1]["rationale"] == "changed with several shots at once" and "resolution" in revs[-1]["changed"]
    bad = h.post(f"/api/projects/{pid}/bulk", {"action": "set", "shot_ids": ids, "fields": {"resolution": "big"}})
    assert bad.status_code in (409, 422)
    assert h.post(f"/api/projects/{pid}/bulk", {"action": "explode", "shot_ids": ids}).status_code in (409, 422)


def test_clearing_the_storyboard_leaves_no_renders_batch_or_redo_behind(make_harness):
    """Live: after deleting every shot and re-planning, the old renders and the finished batch still showed."""
    from tests.test_storyboard import wait_batch
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids})
    wait_batch(h, pid, lambda b: b["status"] == "done", "rendered")
    proj = h.project(pid)
    assert len(proj["jobs"]) == 3 and len(proj["takes"]) == 3
    h.c.portal.call(h.studio.runs.propose, pid, ids[0], "the light is wrong, warmer", None, None)
    out = h.post(f"/api/projects/{pid}/bulk", {"action": "delete", "shot_ids": ids,
                                                "scene_ids": [sc["id"] for sc in proj["scenes"]]}).json()
    assert out["done"] == ids
    proj = h.project(pid)
    assert proj["shots"] == proj["scenes"] == proj["jobs"] == proj["takes"] == [] and proj["batch"] is None
    assert not h.c.portal.call(h.studio.runs.proposals, pid)
    pending = h.c.portal.call(h.studio.db.fetchall, "SELECT id FROM redo_proposals WHERE status = 'pending'")
    assert not pending
    # Undo brings it all back: the shots, their renders and the batch
    h.post(f"/api/projects/{pid}/bulk/undo", {"token": out["undo"]})
    proj = h.project(pid)
    assert len(proj["shots"]) == 3 and len(proj["jobs"]) == 3 and proj["batch"]["status"] == "done"
