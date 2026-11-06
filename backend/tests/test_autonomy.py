"""F6: take inspection, redo proposals, bounded redo runs, caps, owner controls and the GPU hold."""

import json

import pytest

from tests.test_api import new_project, new_shot
from tests.test_edit import LTX, patch, small_project

FLAGGED = {"setting_matches": False, "setting_seen": "a sunny park", "characters": [], "unexpected_people": False,
           "artifacts": [], "prompt_match": 2, "issues": ["daylight instead of night"], "summary": "A sunny park."}


def take_shot(h, pid, title="Street", takes=1, **fields):
    """A shot 1 s long (the fake clip is 1.04 s, so the length check passes) with N rendered takes."""
    s = new_shot(h, pid, title=title, template=LTX, frames=25, **fields)
    for n in range(takes):
        assert h.post(f"/api/shots/{s['id']}/approve-and-render").status_code == 200
        h.wait_for(lambda: len(takes_of(h, pid, s["id"])) == n + 1, what="take")
    return s


def takes_of(h, pid, sid):
    return [t for t in h.project(pid)["takes"] if t["shot_id"] == sid]


def shot_of(h, pid, sid):
    return next(x for x in h.project(pid)["shots"] if x["id"] == sid)


def inspection(h, take_id, timeout=30):
    return h.wait_for(lambda: (i := h.c.get(f"/api/takes/{take_id}/inspection").json()["inspection"])
                      and i["status"] != "running" and i, timeout=timeout, what="inspection")


def redo(h, pid):
    return h.c.get(f"/api/projects/{pid}/redo").json()


def a3(h, pid):
    assert patch(h, f"/api/projects/{pid}", {"autonomy": "A3"}).status_code == 200


def run_ends(h, pid, timeout=60):
    return h.wait_for(lambda: (r := redo(h, pid)["run"]) and r["status"] not in ("running", "paused") and r,
                      timeout=timeout, what="run end")


# --- inspection ---------------------------------------------------------------------------

def test_new_takes_are_inspected_with_technical_and_vision_checks(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = take_shot(h, pid)
    t = takes_of(h, pid, s["id"])[0]
    i = inspection(h, t["id"])
    assert i["status"] == "done" and i["verdict"] == "ok", i
    assert i["technical"]["duration_s"] == pytest.approx(1.04, abs=0.05) and i["technical"]["flags"] == []
    assert i["vision"]["prompt_match"] == 4 and i["model"] == "gemma4:31b-cloud"
    asked = h.vision.requests[-1]
    assert len(asked["messages"][0]["images"]) == 4 and asked["format"]["type"] == "object"
    assert "Yellow-green fog" in asked["messages"][0]["content"]
    r = h.c.get(f"/api/takes/{t['id']}/sheet")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    # A vision answer that disagrees flags the take, with the reason in plain words.
    h.vision.answer = FLAGGED
    assert h.post(f"/api/takes/{t['id']}/inspect").status_code == 202
    i = h.wait_for(lambda: (x := inspection(h, t["id"])) and x["verdict"] == "flagged" and x, what="flagged")
    assert "matches the prompt poorly (2/5)" in i["summary"]
    # Vision down: the technical part still stands, and the take isn't failed for it.
    h.vision.fail = True
    h.post(f"/api/takes/{t['id']}/inspect")
    i = h.wait_for(lambda: (x := inspection(h, t["id"])) and x["vision"] and "error" in x["vision"] and x, what="no vision")
    assert i["status"] == "done" and i["verdict"] == "ok" and "didn't run" in i["vision"]["error"]


def test_inspection_uses_the_scene_setting_and_flags_a_mismatch(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    sc = h.post(f"/api/projects/{pid}/scenes", {"title": "Rain", "time_place": "Night, suburban street"}).json()
    s = take_shot(h, pid, scene_id=sc["id"])
    i = inspection(h, takes_of(h, pid, s["id"])[0]["id"])
    assert "Night, suburban street" in h.vision.requests[-1]["messages"][0]["content"]
    h.vision.answer = FLAGGED
    t = takes_of(h, pid, s["id"])[0]["id"]
    h.post(f"/api/takes/{t}/inspect")
    i = h.wait_for(lambda: (x := inspection(h, t)) and x["verdict"] == "flagged" and x, what="flagged")
    assert "setting doesn't match (looks like: a sunny park)" in i["summary"]


def test_auto_inspection_can_be_turned_off(make_harness):
    h = make_harness().login()
    assert h.c.put("/api/settings/autonomy", json={"auto_inspect": False}, headers={"x-csrf-token": h.csrf}).json()[
        "auto_inspect"] is False
    pid = small_project(h)
    s = take_shot(h, pid)
    t = takes_of(h, pid, s["id"])[0]["id"]
    assert h.c.get(f"/api/takes/{t}/inspection").json()["inspection"] is None
    assert h.c.put("/api/settings/autonomy", json={"vision_model": "bad name; rm"},
                   headers={"x-csrf-token": h.csrf}).status_code == 422


# --- proposals outside a run ------------------------------------------------------------------

def test_director_redo_proposal_waits_for_the_owner(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = take_shot(h, pid)
    run = lambda name, args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    t = takes_of(h, pid, s["id"])[0]["id"]
    got = run("inspect_take", {"take_id": t})
    assert got["status"] == "done" and got["verdict"] == "ok"
    assert "error" in run("inspect_take", {"take_id": "nope"})
    res = run("propose_redo", {"shot_id": s["id"], "reason": "the fog is too thin", "prompt": "Thick fog over a trench at dusk"})
    assert res["run_id"] is None and "Waiting for the owner" in res["message"]
    assert len(takes_of(h, pid, s["id"])) == 1  # nothing rendered
    props = redo(h, pid)["proposals"]
    assert [p["status"] for p in props] == ["pending"] and props[0]["changes"]["prompt"].startswith("Thick fog")
    # Accept: the change lands with the director's reason in the shot's history, and one take renders.
    assert h.post(f"/api/redo-proposals/{props[0]['id']}/accept").status_code == 200
    h.wait_for(lambda: len(takes_of(h, pid, s["id"])) == 2, what="redo take")
    h.wait_for(lambda: redo(h, pid)["proposals"][0]["status"] == "done", what="proposal done")
    hist = h.c.get(f"/api/shots/{s['id']}/revisions").json()
    assert any("redo: the fog is too thin" in (r.get("rationale") or "") for r in hist)
    assert shot_of(h, pid, s["id"])["prompt"] == "Thick fog over a trench at dusk"
    # Reject and lock.
    res = run("propose_redo", {"shot_id": s["id"], "reason": "try another seed", "seed": -1})
    pid2 = res["proposal_id"]
    assert h.post(f"/api/redo-proposals/{pid2}/reject").status_code == 200
    assert h.post(f"/api/redo-proposals/{pid2}/accept").status_code == 409
    assert "reason is required" in run("propose_redo", {"shot_id": s["id"], "reason": ""})["error"]
    h.post(f"/api/shots/{s['id']}/lock", {"locked": True})
    assert "locked" in run("propose_redo", {"shot_id": s["id"], "reason": "again please"})["error"]


# --- runs -----------------------------------------------------------------------------------

def test_a_run_needs_a3_and_owner_controls(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = take_shot(h, pid)
    r = h.post(f"/api/projects/{pid}/runs", {})
    assert r.status_code == 409 and "A3" in r.json()["detail"]
    a3(h, pid)
    r = h.post(f"/api/projects/{pid}/runs", {})
    assert r.status_code == 409 and "no shot needs a redo" in r.json()["detail"]  # its take looks right
    # The director has no way to start a run, change limits or the autonomy level.
    from app.director.tools import TOOL_NAMES
    assert not {n for n in TOOL_NAMES if "run" in n and n not in ("get_render_status",)} - {"request_render",
                                                                                           "request_storyboard_render"}
    assert patch(h, f"/api/projects/{pid}", {"autonomy": "A9"}).status_code == 422
    assert h.post(f"/api/projects/{pid}/runs", {"shot_ids": [s["id"]], "caps": {"max_retries": 99}}).status_code == 409


def test_run_fixes_a_flagged_shot_and_leaves_the_rest_alone(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    h.vision.answers = [FLAGGED]  # the first take inspected is flagged; everything after looks right
    bad = take_shot(h, pid, "Lost the setting")
    inspection(h, takes_of(h, pid, bad["id"])[0]["id"])
    good = take_shot(h, pid, "Fine")
    locked = take_shot(h, pid, "Locked")
    h.post(f"/api/takes/{takes_of(h, pid, locked['id'])[0]['id']}/review", {"verdict": "reject"})
    h.post(f"/api/shots/{locked['id']}/lock", {"locked": True})
    chosen = take_shot(h, pid, "Owner's pick", takes=2)
    t0 = takes_of(h, pid, chosen["id"])[0]["id"]
    h.post(f"/api/shots/{chosen['id']}/select-take", {"take_id": t0})
    a3(h, pid)
    state = redo(h, pid)
    assert [x["shot_id"] for x in state["needs_redo"]] == [bad["id"]]  # locked/picked/fine shots aren't candidates
    before = {sid: len(takes_of(h, pid, sid)) for sid in (good["id"], locked["id"], chosen["id"])}
    r = h.post(f"/api/projects/{pid}/runs", {"caps": {"max_retries": 2, "max_renders": 3}})
    assert r.status_code == 201, r.text
    end = run_ends(h, pid)
    assert end["status"] == "done", end
    assert end["used_renders"] == 1 and end["shot_state"][bad["id"]]["state"] == "fixed", json.dumps(
        [end["shot_state"], [e["text"] for e in end["log"]], len(h.vision.requests),
         [(t["id"][:8], t["created_at"], (inspection(h, t["id"]) or {}).get("summary")) for t in takes_of(h, pid, bad["id"])],
         h.c.portal.call(h.studio.db.fetchall, "SELECT take_id, result_take_id, status FROM redo_proposals")])
    assert len(takes_of(h, pid, bad["id"])) == 2
    assert {sid: len(takes_of(h, pid, sid)) for sid in before} == before  # nothing else rendered
    assert shot_of(h, pid, chosen["id"])["selected_take_id"] == t0  # the owner's pick stands
    p = redo(h, pid)["proposals"][0]
    assert p["status"] == "done" and p["outcome"].startswith("looks right") and "restated the rainy night" in p["reason"]
    log = " | ".join(e["text"] for e in end["log"])
    assert "asked the director" in log and "redo 1 rendering" in log and "1 shot(s) fixed" in end["stop_reason"]


def test_run_gives_up_after_max_retries_and_respects_render_cap(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    h.vision.answer = FLAGGED  # every take stays flagged
    a = take_shot(h, pid, "A")
    b = take_shot(h, pid, "B")
    for s in (a, b):
        inspection(h, takes_of(h, pid, s["id"])[0]["id"])
    a3(h, pid)
    h.post(f"/api/projects/{pid}/runs", {"caps": {"max_retries": 2, "max_renders": 3}})
    end = run_ends(h, pid)
    assert end["status"] == "stopped" and "render limit reached (3 renders)" in end["stop_reason"], end
    assert end["used_renders"] == 3
    assert end["shot_state"][a["id"]]["state"] == "given_up" and end["shot_state"][a["id"]]["attempts"] == 2
    assert len(takes_of(h, pid, a["id"])) == 3 and len(takes_of(h, pid, b["id"])) == 2


def test_run_stops_before_a_render_that_would_cross_the_gpu_budget(make_harness, monkeypatch):
    h = make_harness().login()
    pid = small_project(h)
    h.vision.answer = FLAGGED
    s = take_shot(h, pid)
    inspection(h, takes_of(h, pid, s["id"])[0]["id"])

    async def big(rows):
        return {"seconds": 90, "shots": len(rows), "covered": len(rows), "based_on": 3}
    monkeypatch.setattr(h.studio.storyboard, "estimate", big)
    a3(h, pid)
    h.post(f"/api/projects/{pid}/runs", {"caps": {"gpu_minutes": 1}})
    end = run_ends(h, pid)
    assert end["status"] == "stopped" and "GPU budget reached" in end["stop_reason"] and end["used_renders"] == 0
    assert len(takes_of(h, pid, s["id"])) == 1
    # The director's unrendered proposal is now the owner's to accept or reject.
    p = h.wait_for(lambda: (x := redo(h, pid)["proposals"]) and x[0], what="proposal")
    assert p["status"] == "pending" and p["run_id"] is None


def test_director_that_proposes_nothing_and_stop_and_pause(make_harness, monkeypatch):
    import app.runs as runs_mod
    monkeypatch.setattr(runs_mod, "ASK_TIMEOUT_S", 1)
    h = make_harness().login()
    pid = small_project(h)
    h.vision.answer = FLAGGED
    s = take_shot(h, pid)
    inspection(h, takes_of(h, pid, s["id"])[0]["id"])
    a3(h, pid)
    h.gateway.no_redo = True
    h.post(f"/api/projects/{pid}/runs", {})
    end = run_ends(h, pid, timeout=90)
    assert end["status"] == "done" and end["shot_state"][s["id"]]["note"] == "the director proposed no redo in time"
    # Stop while a redo renders: the render is cancelled, nothing else starts.
    h.gateway.no_redo = False
    h.wangp.scenario = "slow"
    run = h.post(f"/api/projects/{pid}/runs", {}).json()
    h.wait_for(lambda: (p := redo(h, pid)["proposals"]) and p[0]["status"] == "rendering", timeout=30, what="rendering")
    assert h.post(f"/api/runs/{run['id']}/pause").json()["status"] == "paused"
    assert h.post(f"/api/runs/{run['id']}/resume").json()["status"] == "running"
    stopped = h.post(f"/api/runs/{run['id']}/stop").json()
    assert stopped["status"] == "stopped" and stopped["stop_reason"] == "stopped by the owner"
    h.wait_for(lambda: h.project(pid)["shots"][0]["status"] in ("ready", "failed"), timeout=30, what="cancelled")
    assert any(a == "render.cancel" for a in [r["action"] for r in h.c.portal.call(
        h.studio.db.fetchall, "SELECT action FROM audit WHERE action = 'render.cancel'")])


def test_gpu_hold_blocks_new_gpu_work_and_stops_runs(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = take_shot(h, pid)
    assert h.post("/api/gpu-hold", {"on": True}).json()["on"] is True
    r = h.post(f"/api/shots/{s['id']}/approve-and-render")
    assert r.status_code in (409, 422) and "on hold" in r.text
    a3(h, pid)
    assert "on hold" in h.post(f"/api/projects/{pid}/runs", {}).json()["detail"]
    assert h.c.get("/api/gpu-hold").json()["on"] is True
    h.post("/api/gpu-hold", {"on": False})
    assert h.post(f"/api/shots/{s['id']}/approve-and-render").status_code == 200


def test_run_survives_a_backend_restart(make_harness):
    h1 = make_harness().login()
    pid = small_project(h1)
    h1.vision.answers = [FLAGGED]
    s = take_shot(h1, pid)
    inspection(h1, takes_of(h1, pid, s["id"])[0]["id"])
    a3(h1, pid)
    h1.gateway.no_redo = True  # the run waits for a proposal when the backend stops
    run = h1.post(f"/api/projects/{pid}/runs", {}).json()
    h1.wait_for(lambda: redo(h1, pid)["run"]["shot_state"].get(s["id"], {}).get("state") == "asked", what="asked")
    h1.close()
    h2 = make_harness().login()
    h2.c.portal.call(h2.studio.runs.propose, pid, s["id"], "new seed after the restart", None, -1)
    end = run_ends(h2, pid)
    assert end["id"] == run["id"] and end["status"] == "done" and end["used_renders"] == 1


def test_vision_answers_in_code_fences_and_lengths_on_the_model_grid():
    from app.inspect_takes import parse_answer, technical_flags
    assert parse_answer('```json\n{"prompt_match": 5}\n```') == {"prompt_match": 5}
    assert parse_answer('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        parse_answer("I can't see the frames")
    base = {"black_s": 0, "frozen_s": 0, "has_audio": True, "mean_db": -20, "max_db": -3}
    assert technical_flags(base | {"duration_s": 5.27}, 5.0, True) == []  # MiniMax's grid
    assert technical_flags(base | {"duration_s": 6.68}, 6.0, True) == []
    assert technical_flags(base | {"duration_s": 2.1}, 5.0, True) == ["length 2.10 s instead of about 5.00 s"]


def test_loose_vision_answers_are_normalised():
    from app.inspect_takes import normalise_answer, vision_flags
    a = normalise_answer({"setting_matches": "yes", "characters": {"Anthony": True, "Jason": False}, "artifacts": "none",
                          "issues": "None.", "prompt_match": "4", "unexpected_people": "no", "summary": "ok"})
    assert a["setting_matches"] is True and a["artifacts"] == [] and a["issues"] == [] and a["prompt_match"] == 4
    assert a["characters"] == [{"name": "Anthony", "visible": True}, {"name": "Jason", "visible": False}]
    assert vision_flags(a, True) == ["not visible: Jason"]
    b = normalise_answer({"characters": ["Anthony", "Jason"], "artifacts": "warped hand", "prompt_match": "great"})
    assert [c["visible"] for c in b["characters"]] == [True, True] and b["artifacts"] == ["warped hand"]
    assert b["prompt_match"] is None and vision_flags(b, False) == []


# --- FL-01: where the vision check's frames go is the owner's explicit choice -------------------------------

def put_vision(h, **body):
    return h.c.put("/api/settings/autonomy", json=body, headers={"x-csrf-token": h.csrf})


def test_new_install_inspects_locally_with_the_vision_check_off(make_harness):
    """A new install sends no frames anywhere until the owner chooses; the technical checks still run."""
    h = make_harness(vision=None).login()
    s = h.c.get("/api/settings/autonomy").json()
    assert (s["vision"], s["vision_model_kind"]) == ("off", "cloud")  # the suggested model is a cloud one
    pid = small_project(h)
    shot = take_shot(h, pid)
    t = takes_of(h, pid, shot["id"])[0]
    i = inspection(h, t["id"])
    assert i["status"] == "done" and i["technical"]["duration_s"] > 0 and i["model"] is None
    assert "vision check is off" in i["vision"]["skipped"] and "vision check is off" in i["summary"]
    assert h.vision.requests == []  # no frames went to any vision model
    assert h.project(pid)["inspections"][t["id"]]["vision_skipped"]


def test_local_or_cloud_must_match_the_model_and_never_switch_silently(make_harness):
    h = make_harness(vision=None).login()
    r = put_vision(h, vision="local")  # the default model is a cloud one: refused, not quietly sent to the cloud
    assert r.status_code == 422 and "cloud model" in r.text and "would leave this PC" in r.text
    assert put_vision(h, vision="cloud", vision_model="llava:latest").status_code == 422
    assert h.c.get("/api/settings/autonomy").json()["vision"] == "off"  # refused changes change nothing
    s = put_vision(h, vision="local", vision_model="llava:latest").json()
    assert (s["vision"], s["vision_model"], s["vision_model_kind"]) == ("local", "llava:latest", "local")
    pid = small_project(h)
    shot = take_shot(h, pid)
    t = takes_of(h, pid, shot["id"])[0]
    i = inspection(h, t["id"])
    assert i["model"] == "llava:latest" and h.vision.requests[-1]["model"] == "llava:latest"
    # Back to cloud only by choosing it with a cloud model.
    s = put_vision(h, vision="cloud", vision_model="gemma4:31b-cloud").json()
    assert (s["vision"], s["vision_model_kind"]) == ("cloud", "cloud")
    assert put_vision(h, vision_model="llava:latest").status_code == 422  # a local model under "cloud"


def test_installs_that_already_used_vision_keep_their_choice(make_harness):
    """Before the explicit setting existed, every take went to the configured model: keep that, don't turn it off."""
    from app.inspect_takes import is_cloud_model
    h = make_harness().login()
    pid = small_project(h)
    shot = take_shot(h, pid)
    assert inspection(h, takes_of(h, pid, shot["id"])[0]["id"])["model"] == "gemma4:31b-cloud"  # vision has run
    # settings as saved before the explicit choice existed
    h.c.portal.call(h.studio.db.kv_set, "autonomy.settings", {"auto_inspect": True, "vision_model": "gemma4:31b-cloud"})
    assert h.c.get("/api/settings/autonomy").json()["vision"] == "cloud"
    assert is_cloud_model("gemma4:31b-cloud") and is_cloud_model("gpt-oss:120b-cloud") and is_cloud_model("x:cloud")
    assert not is_cloud_model("llava:latest") and not is_cloud_model("qwen2.5vl:7b") and not is_cloud_model("cloudy:7b")
