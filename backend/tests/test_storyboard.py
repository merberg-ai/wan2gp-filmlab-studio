"""F4: brief and planning, scenes and order, locks, revisions, batch rendering and its recovery."""

import json

from tests.test_api import new_project, new_shot
from tests.test_library import REF2VA, FL2VA, TURBO, images, refs_setup, set_refs  # noqa: F401

LTX = "LTX-2 2.3 Distilled 1.0"


def run(h, pid, name, **args):
    return h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))


def files_of(h, variant_id):
    v = h.c.portal.call(h.studio.library.variant, variant_id)
    a = h.c.get(f"/api/library/assets/{v['asset_id']}").json()
    return [f["file_id"] for x in a["variants"] if x["id"] == variant_id for f in x["files"]]


def plan(scenes, **extra):
    return {"scenes": scenes, **extra}


def shot(title, prompt, seconds=4, refs=(), **extra):
    return {"title": title, "prompt": prompt, "seconds": seconds, "refs": list(refs), "rationale": f"why {title}", **extra}


def patch(h, url, body):
    return h.c.patch(url, json=body, headers={"x-csrf-token": h.csrf})


def wait_batch(h, pid, pred, what):
    return h.wait_for(lambda: (b := h.project(pid)["batch"]) and pred(b) and b, timeout=20, what=what)


# --- planning ---------------------------------------------------------------------------

def test_invalid_plan_reports_every_error_and_writes_nothing(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, _ = refs_setup(h, images)
    hale = files_of(h, hv)[0]
    bad = plan([{"title": "One", "shots": [
        {"title": "No why", "prompt": "Fog over the trench at dusk", "seconds": 4},                     # no rationale
        shot("Ghost ref", "<Picture 1> stands in the fog at dusk", refs=["nope"]),                  # unknown image
        shot("Two pictures", "<Picture 1> meets <Picture 2> in the trench", refs=[hale]),          # names a missing picture
        shot("Too long", "A very long held shot of the trench", seconds=50),                        # over 20 s
        shot("Bad cut", "Fog over the trench at dusk", transition="wipe")]},                       # unknown transition
        {"title": "", "shots": []}])                                                     # untitled (named for it), empty
    out = run(h, pid, "plan_storyboard", **bad)
    errs = "\n".join(out["errors"])
    assert "the plan wasn't written" in out["error"]
    for needle in ("shots[0]: rationale is required", "shots[1]: refs[0] 'nope' isn't a library image",
                   "names <Picture 2> but the shot has only 1 reference", "shots[3]: seconds must be 0.5-20",
                   "shots[4]: transition must be one of", "scenes[1].shots must be"):
        assert needle in errs, needle
    assert "title is required" not in errs and any("Scene 2" in f for f in out["fixed_by_filmlab"])
    proj = h.project(pid)
    assert proj["shots"] == [] and proj["scenes"] == []


def test_plan_writes_scenes_shots_refs_and_rationales_in_order(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, trench, hv, tv = refs_setup(h, images)
    hale, trench_img = files_of(h, hv)[0], files_of(h, tv)[0]
    out = run(h, pid, "plan_storyboard", logline="Hale waits", scenes=[
        {"title": "The wait", "goal": "establish", "location_asset_id": trench["id"], "shots": [
            shot("Waits", "<Picture 1> waits in <Picture 2> at dusk, fog", 5, [hale, trench_img]),
            shot("Fog", "Fog drifts over an empty trench at dusk", 3, transition="dissolve")]},
        {"title": "The march", "shots": [shot("March", "Soldiers march away into the fog, drums", 4.2)]}])
    assert len(out["created_shots"]) == 3 and out["created_scenes"] == 2, out
    proj = h.project(pid)
    assert proj["logline"] == "Hale waits"
    assert [s["title"] for s in proj["scenes"]] == ["The wait", "The march"]
    assert proj["scenes"][0]["location_asset_id"] == trench["id"]
    shots = proj["shots"]
    assert [(s["position"], s["title"]) for s in shots] == [(1, "Waits"), (2, "Fog"), (3, "March")]
    assert [s["scene_id"] for s in shots] == [proj["scenes"][0]["id"]] * 2 + [proj["scenes"][1]["id"]]
    assert shots[0]["template"] == REF2VA and shots[1]["template"] == FL2VA  # follows the defaults
    assert [r["asset_name"] for r in shots[0]["refs"]] == ["Corporal Hale", "The Trench"]
    assert shots[1]["transition"] == "dissolve" and shots[2]["frames"] == 105  # 4.2 s snapped to the nearest 8n+1
    assert all(s["status"] == "draft" and s["created_by"] == "director" for s in shots)
    assert {a["name"] for a in proj["cast"]} == {"Corporal Hale", "The Trench"}
    revs = h.c.get(f"/api/shots/{shots[0]['id']}/revisions").json()
    assert [(r["kind"], r["actor"], r["rationale"]) for r in revs] == [("plan", "director", "why Waits")]
    assert not [n for n, _ in h.wangp.calls if n == "wangp_generate"]


def test_replan_keeps_locked_and_rendered_shots(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    first = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "shots": [
        shot("Keep rendered", "Fog over the trench at dusk", template=None), shot("Keep locked", "Rain on the trench at dawn"),
        shot("Replace me", "Smoke over the trench at noon")]}])
    rendered, locked, draft = first["created_shots"]
    patch(h, f"/api/shots/{rendered}", {"template": LTX})
    h.post(f"/api/shots/{rendered}/approve-and-render")
    h.wait_for(lambda: next(s for s in h.project(pid)["shots"] if s["id"] == rendered)["status"] == "ready", what="ready")
    h.post(f"/api/shots/{locked}/lock", {"locked": True})
    out = run(h, pid, "plan_storyboard", replace_drafts=True, scenes=[{"title": "B", "shots": [
        shot("New", "Snow on the trench at night")]}])
    assert out["replaced"] == ["shot 3 “Replace me”"]
    assert any("Keep locked" in k and "locked" in k for k in out["kept"])
    assert any("Keep rendered" in k and "has takes" in k for k in out["kept"])
    titles = [s["title"] for s in h.project(pid)["shots"]]
    assert titles == ["Keep rendered", "Keep locked", "New"]
    assert [s["title"] for s in h.c.get("/api/trash").json()["shots"]] == ["Replace me"]  # recoverable
    # The director can't change the locked shot.
    assert "locked by the owner" in run(h, pid, "update_shot", shot_id=locked, prompt="different words here",
                                        rationale="try")["error"]
    assert "locked by the owner" in run(h, pid, "assign_refs", shot_id=locked, refs=[])["error"]
    assert h.post(f"/api/shots/{locked}/enhance").status_code == 409


def test_plan_with_director_sends_the_brief_and_library(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    assert h.post(f"/api/projects/{pid}/plan", {}).status_code == 422  # no brief yet
    p = patch(h, f"/api/projects/{pid}", {"brief": "Hale waits in the trench; the dead march past.",
                                          "target_seconds": 12}).json()
    assert p["brief"].startswith("Hale waits") and p["target_seconds"] == 12
    assert h.post(f"/api/projects/{pid}/plan", {}).status_code == 202
    proj = h.wait_for(lambda: (x := h.project(pid)) and not x["director_busy"] and x["shots"] and x, what="planned")
    brief = [m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"]
    assert "My brief: “Hale waits in the trench" in brief and "Target length: 12 s" in brief
    assert files_of(h, hv)[0] in brief and files_of(h, tv)[0] in brief and "Don't set replace_drafts" in brief
    assert [s["title"] for s in proj["shots"]] == ["Hale waits", "Fog", "March"]
    assert [r["asset_name"] for r in proj["shots"][0]["refs"]] == ["Corporal Hale", "The Trench"]
    # A re-plan asks for replace_drafts and lists the current storyboard.
    h.post(f"/api/shots/{proj['shots'][0]['id']}/lock", {"locked": True})
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True}).status_code == 202
    proj = h.wait_for(lambda: (x := h.project(pid)) and not x["director_busy"] and len(x["scenes"]) == 4 and x,
                      what="replanned")
    brief = [m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"]
    assert "Set replace_drafts: true" in brief and "locked" in brief and "Current storyboard:" in brief
    assert [s["title"] for s in proj["shots"]] == ["Hale waits", "Hale waits", "Fog", "March"]
    assert proj["shots"][0]["locked"] is True


# --- storyboard editing -------------------------------------------------------------------

def test_scenes_order_and_moves(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    a = h.post(f"/api/projects/{pid}/scenes", {"title": "A"}).json()
    b = h.post(f"/api/projects/{pid}/scenes", {"title": "B", "mood": "grim"}).json()
    s1 = h.post(f"/api/projects/{pid}/shots", {"title": "a1", "prompt": "Fog over the trench", "scene_id": a["id"]}).json()
    s2 = h.post(f"/api/projects/{pid}/shots", {"title": "b1", "prompt": "Rain over the trench", "scene_id": b["id"]}).json()
    s3 = h.post(f"/api/projects/{pid}/shots", {"title": "a2", "prompt": "Smoke over the trench", "scene_id": a["id"]}).json()
    loose = new_shot(h, pid, title="loose")
    titles = lambda: [(s["position"], s["title"]) for s in h.project(pid)["shots"]]
    assert titles() == [(1, "a1"), (2, "a2"), (3, "b1"), (4, "loose")]  # scene order, then Unsorted
    h.post(f"/api/shots/{s3['id']}/move", {"scene_id": b["id"], "index": 0})  # a2 to the front of B
    shots = h.project(pid)["shots"]
    assert [x["title"] for x in shots] == ["a1", "a2", "b1", "loose"]
    assert next(x for x in shots if x["id"] == s3["id"])["scene_id"] == b["id"]
    h.post(f"/api/scenes/{b['id']}/move", {"index": 0})
    assert [s["title"] for s in h.project(pid)["scenes"]] == ["B", "A"]
    h.post(f"/api/shots/{loose['id']}/move", {"scene_id": a["id"], "index": 0})
    assert run(h, pid, "move_shot", shot_id=s1["id"])["shot"]["scene_id"] is None  # director moves to Unsorted
    r = h.c.delete(f"/api/scenes/{a['id']}", headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200 and r.json()["shots_deleted"] == 1
    assert [s["title"] for s in h.project(pid)["scenes"]] == ["B"]
    h.post(f"/api/trash/shot/{loose['id']}/restore")
    restored = next(s for s in h.project(pid)["shots"] if s["id"] == loose["id"])
    assert restored["scene_id"] is None
    assert patch(h, f"/api/scenes/{b['id']}", {"title": ""}).status_code == 422
    assert s2


def test_move_shot_between_scenes_keeps_one_sequence(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    a = h.post(f"/api/projects/{pid}/scenes", {"title": "A"}).json()
    b = h.post(f"/api/projects/{pid}/scenes", {"title": "B"}).json()
    mk = lambda t, sc: h.post(f"/api/projects/{pid}/shots", {"title": t, "prompt": f"{t} over the trench", "scene_id": sc}).json()
    a1, a2, b1, b2 = mk("a1", a["id"]), mk("a2", a["id"]), mk("b1", b["id"]), mk("b2", b["id"])
    seq = lambda: [s["title"] for s in h.project(pid)["shots"]]
    h.post(f"/api/shots/{a2['id']}/move", {"scene_id": b["id"], "index": 1})
    assert seq() == ["a1", "b1", "a2", "b2"]
    h.post(f"/api/shots/{b2['id']}/move", {"scene_id": a["id"], "index": 0})
    assert seq() == ["b2", "a1", "b1", "a2"]
    h.post(f"/api/shots/{a1['id']}/move", {"scene_id": b["id"]})  # no index: to the end of B
    assert seq() == ["b2", "b1", "a2", "a1"]
    assert [s["position"] for s in h.project(pid)["shots"]] == [1, 2, 3, 4]
    c = h.post(f"/api/projects/{pid}/scenes", {"title": "C"}).json()
    h.post(f"/api/scenes/{c['id']}/move", {"index": 0})
    h.post(f"/api/shots/{a2['id']}/move", {"scene_id": c["id"]})  # into an empty scene that comes first
    assert seq() == ["a2", "b2", "b1", "a1"] and [s["position"] for s in h.project(pid)["shots"]] == [1, 2, 3, 4]
    assert b1


def test_revisions_record_who_and_why_and_restore(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, template=None, prompt="Fog over the trench at dusk")
    patch(h, f"/api/shots/{s['id']}", {"prompt": "Fog and rain over the trench", "transition": "fade"})
    set_refs(h, s["id"], hv, tv)
    assert "rationale is required" in run(h, pid, "update_shot", shot_id=s["id"], prompt="<Picture 1> in the rain")["error"]
    run(h, pid, "update_shot", shot_id=s["id"], prompt="<Picture 1> waits in <Picture 2>, rain",
        rationale="name the references")
    revs = h.c.get(f"/api/shots/{s['id']}/revisions").json()
    assert [(r["kind"], r["actor"]) for r in revs] == [("create", "owner"), ("update", "owner"), ("refs", "owner"),
                                                       ("update", "director")]
    assert revs[1]["changed"] == ["prompt", "transition"] and revs[2]["changed"] == ["refs", "template"] or \
        revs[2]["changed"] == ["refs"]
    assert revs[3]["rationale"] == "name the references"
    assert h.project(pid)["shots"][0]["rationale"] == "name the references"
    # Restore the owner's first edit: prompt, transition and no references come back; logged as a revert.
    r = h.post(f"/api/revisions/{revs[1]['id']}/restore")
    assert r.status_code == 200, r.text
    shot = h.project(pid)["shots"][0]
    assert (shot["prompt"], shot["transition"], shot["refs"]) == ("Fog and rain over the trench", "fade", [])
    revs = h.c.get(f"/api/shots/{s['id']}/revisions").json()
    assert revs[-1]["kind"] == "revert" and "restored the version" in revs[-1]["rationale"]
    # No revision when nothing changed; locked shots can't be restored.
    patch(h, f"/api/shots/{s['id']}", {"prompt": "Fog and rain over the trench"})
    assert len(h.c.get(f"/api/shots/{s['id']}/revisions").json()) == len(revs)
    h.post(f"/api/shots/{s['id']}/lock", {"locked": True})
    assert h.post(f"/api/revisions/{revs[0]['id']}/restore").status_code == 422


# --- batches ----------------------------------------------------------------------------

def three_shots(h, pid, template=LTX):
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "shots": [
        shot("one", "Fog over the trench at dusk", 2), shot("two", "Rain over the trench at dawn", 2)]},
        {"title": "B", "shots": [shot("three", "Smoke over the trench at noon", 2)]}])
    ids = out["created_shots"]
    for sid in ids:
        patch(h, f"/api/shots/{sid}", {"template": template})
    return ids


def test_batch_renders_in_storyboard_order_one_at_a_time(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    review = h.c.get(f"/api/projects/{pid}/batch-review").json()
    assert [r["shot_id"] for r in review["shots"]] == ids and all(r["default"] for r in review["shots"])
    r = h.post(f"/api/projects/{pid}/batches", {"shot_ids": list(reversed(ids))})
    assert r.status_code == 201, r.text
    assert [i["shot_id"] for i in r.json()["items"]] == ids  # storyboard order, whatever order was sent
    assert h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids}).status_code == 409  # one open batch per project
    b = wait_batch(h, pid, lambda b: b["status"] == "done", "batch done")
    assert [i["status"] for i in b["items"]] == ["done"] * 3
    jobs = sorted(h.project(pid)["jobs"], key=lambda j: j["submitted_at"])
    assert [j["shot_id"] for j in jobs] == ids
    for prev, nxt in zip(jobs, jobs[1:]):  # never two at once
        assert nxt["submitted_at"] >= prev["finished_at"]
    assert all(s["status"] == "ready" for s in h.project(pid)["shots"])
    review = h.c.get(f"/api/projects/{pid}/batch-review").json()
    assert not any(r["default"] for r in review["shots"])  # rendered shots aren't picked again by default
    assert any(review["rates"].values())  # past renders now give a grounded estimate


def test_batch_failure_continues_and_retry(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    patch(h, f"/api/shots/{ids[1]}", {"template": TURBO})  # WanGP refuses it (missing LoRA)
    h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids})
    b = wait_batch(h, pid, lambda b: b["status"] == "done", "batch done")
    assert [i["status"] for i in b["items"]] == ["done", "failed", "done"]
    assert "LoRA" in b["items"][1]["error"]
    assert h.project(pid)["shots"][1]["status"] == "draft"  # refused before submit: left as it was
    patch(h, f"/api/shots/{ids[1]}", {"template": LTX})
    r = h.post(f"/api/batches/{b['id']}/items/2/retry")
    assert r.status_code == 200, r.text
    b = wait_batch(h, pid, lambda b: b["status"] == "done" and b["items"][1]["status"] == "done", "retried")
    assert len(h.project(pid)["takes"]) == 3


def test_batch_pause_resume_and_cancel(make_harness):
    h = make_harness("slow").login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    b = h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids}).json()
    wait_batch(h, pid, lambda b: b["items"][0]["status"] == "rendering", "first rendering")
    assert h.post(f"/api/batches/{b['id']}/pause").status_code == 200
    h.wangp.scenario = "normal"  # let the current render finish
    b = wait_batch(h, pid, lambda b: b["items"][0]["status"] == "done", "first done")
    assert b["status"] == "paused" and [i["status"] for i in b["items"][1:]] == ["pending", "pending"]
    h.wangp.scenario = "slow"
    h.post(f"/api/batches/{b['id']}/resume")
    wait_batch(h, pid, lambda b: b["items"][1]["status"] == "rendering", "second rendering")
    assert h.post(f"/api/batches/{b['id']}/cancel").status_code == 200
    b = wait_batch(h, pid, lambda b: b["items"][1]["status"] == "cancelled", "cancelled")
    assert b["status"] == "cancelled" and b["items"][2]["status"] == "cancelled"
    assert h.post(f"/api/batches/{b['id']}/items/3/retry").status_code == 409


def test_batch_survives_a_backend_restart(make_harness):
    h1 = make_harness("slow").login()
    pid = new_project(h1)
    ids = three_shots(h1, pid)[:2]
    h1.post(f"/api/projects/{pid}/batches", {"shot_ids": ids})
    wait_batch(h1, pid, lambda b: b["items"][0]["status"] == "rendering", "rendering")
    h1.close()
    h2 = make_harness("normal")
    h2.wangp.jobs, h2.wangp.by_request = h1.wangp.jobs, h1.wangp.by_request  # the same WanGP instance
    h2.login()
    b = wait_batch(h2, pid, lambda b: b["status"] == "done", "done after restart")
    assert [i["status"] for i in b["items"]] == ["done", "done"]
    generated = [a["settings"]["prompt"] for n, a in h1.wangp.calls + h2.wangp.calls if n == "wangp_generate"]
    assert len(generated) == 2  # the first render was followed, not resubmitted


def test_locked_shots_and_director_batch_request(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    h.post(f"/api/shots/{ids[0]}/lock", {"locked": True})
    r = h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids})
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    review = {x["shot_id"]: x for x in h.c.get(f"/api/projects/{pid}/batch-review").json()["shots"]}
    assert review[ids[0]]["locked"] and not review[ids[0]]["default"]
    out = run(h, pid, "request_storyboard_render", shot_ids=[ids[2], ids[1]], note="these two are ready")
    assert out["status"] == "pending"
    req = h.project(pid)["batch_requests"][0]
    assert req["shot_ids"] == [ids[1], ids[2]] and req["note"] == "these two are ready"
    assert not [n for n, _ in h.wangp.calls if n == "wangp_generate"]  # the director started nothing
    h.post(f"/api/projects/{pid}/batches", {"shot_ids": req["shot_ids"], "request_id": req["id"]})
    wait_batch(h, pid, lambda b: b["status"] == "done", "done")
    assert h.project(pid)["batch_requests"] == []


def test_director_tool_arguments_are_checked_not_silently_dropped(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, prompt="Fog over the trench at dusk")
    # A wrapper object (what the live model sent) is unpacked and applied.
    out = run(h, pid, "update_shot", shot_id=s["id"], rationale="richer", update={"prompt": "Fog and rain over the trench"})
    assert out["shot"]["prompt"] == "Fog and rain over the trench"
    assert h.project(pid)["shots"][0]["prompt"] == "Fog and rain over the trench"
    # A misnamed argument is an error, not a no-op.
    out = run(h, pid, "update_shot", shot_id=s["id"], rationale="x", new_prompt="Smoke over the trench at noon")
    assert "unknown argument(s) new_prompt" in out["error"] and "prompt" in out["error"]
    assert "nothing to change" in run(h, pid, "update_shot", shot_id=s["id"], rationale="x")["error"]
    out = run(h, pid, "update_shot", shot_id=s["id"], rationale="same", prompt="Fog and rain over the trench")
    assert "nothing changed" in out["note"]


def test_turns_that_save_nothing_say_so(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, prompt="Fog over the trench at dusk")
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk."})
    h.gateway.lazy = True
    for url in (f"/api/projects/{pid}/plan", f"/api/shots/{s['id']}/enhance"):
        assert h.post(url, {}).status_code == 202
        h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
        last = h.c.get(f"/api/projects/{pid}/chat").json()[-1]
        assert last["role"] == "error" and "nothing was written" in last["content"], last
    assert h.project(pid)["shots"][0]["prompt"] == "Fog over the trench at dusk"
    h.gateway.lazy = False
    # A director that describes the plan once and calls the tool when reminded gets its plan written.
    h.gateway.lazy_once = True
    assert h.post(f"/api/projects/{pid}/plan", {}).status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert any(m["role"] == "note" and "plan_storyboard didn't succeed" in m["content"] for m in chat)
    assert chat[-1]["role"] != "error" and len(h.project(pid)["shots"]) > 1, chat[-1]
    h.gateway.lazy_once = False
    h.post(f"/api/shots/{s['id']}/enhance", {})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    assert h.c.get(f"/api/projects/{pid}/chat").json()[-1]["role"] == "assistant"


def test_a_closing_backend_submits_nothing_new(make_harness):
    h1 = make_harness("slow").login()
    pid = new_project(h1)
    ids = three_shots(h1, pid)[:2]
    h1.post(f"/api/projects/{pid}/batches", {"shot_ids": ids})
    wait_batch(h1, pid, lambda b: b["items"][0]["status"] == "rendering", "rendering")
    h1.studio.batches.closing = True  # shutdown has begun
    h1.wangp.scenario = "normal"       # the render finishes during the shutdown
    b = wait_batch(h1, pid, lambda b: b["items"][0]["status"] == "done", "first done")
    assert b["items"][1]["status"] == "pending"  # not submitted by the process on its way out
    h1.close()
    h2 = make_harness("normal")
    h2.wangp.jobs, h2.wangp.by_request = h1.wangp.jobs, h1.wangp.by_request
    h2.login()
    b = wait_batch(h2, pid, lambda b: b["status"] == "done", "done after restart")  # the next process carries on
    assert [i["status"] for i in b["items"]] == ["done", "done"]


def test_plan_wrapped_under_the_tool_name_is_unwrapped(make_harness):
    """gemma sometimes sends {"plan_storyboard": {"scenes": ...}}; OpenClaw used to reject that as
    'missing required scenes' before FilmLab saw it. Now it reaches FilmLab, which unwraps it."""
    from app.director.tools import TOOLS
    assert all("required" not in t["function"]["parameters"] for t in TOOLS)
    plan_tool = next(t for t in TOOLS if t["function"]["name"] == "plan_storyboard")
    assert plan_tool["function"]["parameters"]["properties"]["scenes"]["description"].startswith("Required.")
    h = make_harness().login()
    pid = new_project(h)
    out = run(h, pid, "plan_storyboard", plan_storyboard=plan(
        [{"title": "Boot", "shots": [shot("Switch", "A scientist flips a switch in a cluttered lab", 5)]}]))
    assert len(out["created_shots"]) == 1, out
    missing = run(h, pid, "plan_storyboard", replace_drafts=False)
    assert "error" in missing and "scenes" in str(missing), missing


def test_empty_director_reply_says_so_plainly(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk."})
    h.gateway.empty = True
    assert h.post(f"/api/projects/{pid}/plan", {}).status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    last = h.c.get(f"/api/projects/{pid}/chat").json()[-1]
    assert last["role"] == "error" and "came back empty" in last["content"] and "api_error" not in last["content"], last
    # it tried once more in a fresh conversation first (nothing had been saved), then gave up
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert sum("started a fresh director conversation" in m["content"] for m in chat) == 1
    assert h.gateway.requests[-1]["user"].endswith(":1")


def test_empty_reply_is_retried_once_in_a_fresh_conversation(make_harness):
    """OpenClaw's session tends to stay stuck after an empty reply; FilmLab starts a fresh one and resends."""
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk."})
    h.gateway.empty = 1
    assert h.post(f"/api/projects/{pid}/plan", {}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"] and not p["director_busy"] and p, what="planned")
    assert len(proj["shots"]) == 3
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert not [m for m in chat if m["role"] == "error"]
    assert any("started a fresh director conversation" in m["content"] for m in chat if m["role"] == "note")


def test_plan_with_scene_fields_outside_the_scene_and_a_location_by_name(make_harness):
    """gemma wrote time_place beside "scenes", left the scene untitled and named the location instead of
    giving its id; FilmLab repairs those and says what it changed."""
    h = make_harness().login()
    pid = new_project(h)
    from tests.test_library import asset
    loc = asset(h, "location", "jim_lab")
    args = plan([{"goal": "Wake the android", "location_asset_id": "Jim lab",
                  "shots": [shot("Switch", "A scientist flips a switch in a cluttered lab", 5)]}])
    out = run(h, pid, "plan_storyboard", **args, time_place="Evening, Jim's lab")
    assert "error" not in out, out
    scene = h.project(pid)["scenes"][0]
    assert (scene["title"], scene["time_place"], scene["location_asset_id"]) == ("Scene 1", "Evening, Jim's lab", loc["id"])
    assert any("time_place" in f for f in out["fixed_by_filmlab"]) and any("title" in f for f in out["fixed_by_filmlab"])


def test_a_long_plan_comes_in_parts_and_later_parts_never_replace(make_harness):
    """One plan_storyboard call for a whole long film is more than the model writes at once (it comes back empty);
    FilmLab asks for it a scene or two at a time, and only the first part may replace drafts."""
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk, then marches away."})
    h.gateway.more_parts = 2
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["scenes"] and not p["director_busy"] and p, what="planned")
    titles = [s["title"] for s in proj["scenes"]]
    assert titles[-2:] == ["Later scene 1", "Later scene 2"], titles
    assert len(proj["shots"]) == 5  # the first part's three shots survive the later parts' replace_drafts
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert chat[-1]["content"].startswith("All planned") and not [m for m in chat if m["role"] == "error"]
    asks = [m for m in h.gateway.requests if m["messages"][0]["content"].startswith("Part ")]
    assert len(asks) == 3 and "“Later scene 1”" in asks[-1]["messages"][0]["content"]


def test_plan_progress_and_stop_keep_the_saved_parts(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk, then marches away.",
                                      "target_seconds": 30})
    h.gateway.hang_next_part = True  # part 1 is saved, then the director takes its time over part 2
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["planning"] and p["planning"]["parts"] == 1 and p, what="part 1")
    assert proj["director_busy"] and proj["planning"]["seconds"] == 12 and proj["planning"]["scenes"] == 2
    events = [e for e in h.c.portal.call(h.studio.bus.replay, 0) if e["type"] == "plan.progress"]
    assert events and events[-1]["payload"]["parts"] == 1
    assert h.post(f"/api/projects/{pid}/chat/stop").json() == {"stopped": True}
    proj = h.project(pid)
    assert not proj["director_busy"] and proj["planning"] is None and len(proj["shots"]) == 3  # part 1 stays
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert chat[-1]["role"] == "note" and chat[-1]["content"].startswith("Stopped by you after 1 part")
    assert h.post(f"/api/projects/{pid}/chat/stop").json() == {"stopped": False}
    h.gateway.hang_next_part = False
    assert h.post(f"/api/projects/{pid}/chat", {"text": "hello"}).status_code in (200, 202)  # the chat works again


def test_an_empty_reply_mid_plan_carries_on_from_the_saved_parts(make_harness):
    """Live: gemma's tool call for the next part is sometimes dropped (an empty reply) after parts are saved.
    FilmLab starts a fresh conversation once and asks for the rest, as Plan more does, never re-planning saved parts."""
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A soldier waits in the trench at dusk, then marches away."})
    h.gateway.empty_next_part = 1
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["scenes"] and not p["director_busy"] and p, what="planned")
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert not [m for m in chat if m["role"] == "error"], chat[-1]
    assert any("asked it to plan the rest" in m["content"] for m in chat if m["role"] == "note")
    assert len(proj["shots"]) == 6  # part 1 kept, the resumed part added after it (it may not replace)
    briefs = [r for r in h.gateway.requests if "Plan the storyboard" in (r["messages"][0].get("content") or "")]
    assert len(briefs) == 2  # the owner's request, then the resume (never the owner's message again)
    resumed = briefs[-1]
    assert resumed["user"].endswith(":1") and "Current storyboard:" in resumed["messages"][0]["content"]
    assert "Don't set replace_drafts" in resumed["messages"][0]["content"]
    # It retries once per plan: failing again after that ends with the plain error and the saved parts kept.
    pid2 = new_project(h)
    patch(h, f"/api/projects/{pid2}", {"brief": "A soldier waits in the trench at dusk, then marches away."})
    h.gateway.empty_next_part = 2
    assert h.post(f"/api/projects/{pid2}/plan", {}).status_code == 202
    h.wait_for(lambda: h.project(pid2)["shots"] and not h.project(pid2)["director_busy"], what="idle")
    last = h.c.get(f"/api/projects/{pid2}/chat").json()[-1]
    assert last["role"] == "error" and "after 2 part(s)" in last["content"] and "Plan more" in last["content"], last
    assert len(h.project(pid2)["shots"]) == 6


def test_a_batch_can_wait_for_its_start_time_and_waiting_shots_can_move(make_harness, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import app.batches as batches_mod
    monkeypatch.setattr(batches_mod, "SCHEDULE_CHECK_S", 0.05)
    h = make_harness("slow").login()
    pid = new_project(h)
    ids = three_shots(h, pid)
    later = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat().replace("+00:00", "Z")
    b = h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids, "start_at": later}).json()
    assert b["status"] == "paused" and b["start_at"] == later and "waits to start" in b["pause_reason"]
    assert not [n for n, _ in h.wangp.calls if n == "wangp_generate"]  # nothing submitted yet
    # waiting shots can be reordered: the third goes before the second
    b = h.post(f"/api/batches/{b['id']}/items/3/move", {"direction": "up"}).json()
    assert [i["shot_id"] for i in b["items"]] == [ids[0], ids[2], ids[1]]
    assert h.post(f"/api/batches/{b['id']}/items/1/move", {"direction": "sideways"}).status_code in (409, 422)
    # when the time comes it starts by itself, in the new order
    h.c.portal.call(h.studio.db.execute, "UPDATE batches SET start_at = ? WHERE id = ?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"), b["id"]))
    h.c.portal.call(h.studio.batches.resume_all)
    b = wait_batch(h, pid, lambda x: x["status"] == "running" and x["items"][0]["status"] == "rendering", "started")
    assert b["start_at"] is None and b["pause_reason"] is None
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"]
    assert gen and gen[0]["settings"]["prompt"].startswith("Fog")
    bad = h.post(f"/api/projects/{pid}/batches", {"shot_ids": ids, "start_at": "tomorrow"})
    assert bad.status_code in (409, 422)
