import json
from pathlib import Path

import httpx

from app import renders
from tests.conftest import PASSWORD


def new_project(h, title="The Dead March"):
    r = h.post("/api/projects", {"title": title, "logline": "test"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def new_shot(h, pid, **fields):
    body = {"title": "Wide", "prompt": "Yellow-green fog over a ruined trench at dusk", "frames": 121} | fields
    r = h.post(f"/api/projects/{pid}/shots", body)
    assert r.status_code == 201, r.text
    return r.json()


# --- auth ------------------------------------------------------------------

def test_requires_login(make_harness):
    h = make_harness()
    assert h.c.get("/api/projects").status_code == 401
    assert h.c.post("/api/auth/login", json={"username": "owner", "password": "nope"}).status_code == 401


def test_csrf_required_for_mutations(make_harness):
    h = make_harness().login()
    assert h.c.post("/api/projects", json={"title": "x"}).status_code == 403
    assert h.post("/api/projects", {"title": "x"}).status_code == 201


def test_websocket_rejects_unauthenticated_and_foreign_origin(make_harness):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    h = make_harness()
    with pytest.raises(WebSocketDisconnect):
        with h.c.websocket_connect("/ws") as ws:
            ws.receive_json()
    h.login()
    with pytest.raises(WebSocketDisconnect):
        with h.c.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()


LAN = ("127.0.0.1/32", "::1/128", "192.168.1.0/24")  # an install with LAN access set up (the default is loopback only)


def test_clients_outside_allowed_networks_are_refused(make_harness):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    h = make_harness(client_addr="10.20.30.40", allowed_nets=LAN)
    assert h.c.get("/api/auth/me").status_code == 403
    assert h.c.post("/api/auth/login", json={"username": "owner", "password": "x" * 12}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with h.c.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:19300"}) as ws:
            ws.receive_json()
    lan = make_harness(client_addr="192.168.1.50", allowed_nets=LAN)
    assert lan.c.get("/api/auth/me").status_code == 401  # allowed network, still needs login


def test_failed_logins_are_throttled_per_client(make_harness, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "LOGIN_FAIL_DELAY_S", 0)
    h = make_harness(client_addr="192.168.1.50", login_max_failures=3, allowed_nets=LAN)
    bad = {"username": "owner", "password": "wrong-password-123"}
    assert [h.c.post("/api/auth/login", json=bad).status_code for _ in range(4)] == [401, 401, 401, 429]
    assert h.c.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 429
    other = make_harness(client_addr="192.168.1.51", allowed_nets=LAN)
    other.login()  # a different client is not locked out


def test_lan_bound_websocket_requires_origin(make_harness):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    h = make_harness(host="0.0.0.0").login()
    with pytest.raises(WebSocketDisconnect):
        with h.c.websocket_connect("/ws") as ws:
            ws.receive_json()
    with h.c.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:19300"}) as ws:
        assert ws.receive_json()["type"] == "stream.ready"


def test_websocket_replays_after_event_id(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    last = h.project(pid)["last_event_id"]
    new_shot(h, pid)
    with h.c.websocket_connect(f"/ws?after_event_id={last}", headers={"origin": "http://127.0.0.1:19300"}) as ws:
        first = ws.receive_json()
        assert first["type"] == "shot.updated" and first["event_id"] > last
        assert ws.receive_json()["type"] == "stream.ready"


# --- shots -----------------------------------------------------------------

def test_shot_validation_and_frame_snapping(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    assert new_shot(h, pid, frames=123)["frames"] == 121
    assert h.post(f"/api/projects/{pid}/shots", {"title": "x", "prompt": "short"}).status_code == 422
    assert h.post(f"/api/projects/{pid}/shots", {"title": "x", "prompt": "long enough prompt",
                                                 "resolution": "4000x4000"}).status_code == 422
    assert h.post(f"/api/projects/{pid}/shots", {"title": "x", "prompt": "long enough prompt",
                                                 "template": "Nope"}).status_code == 422


def test_validate_does_not_submit(make_harness):
    h = make_harness().login()
    shot = new_shot(h, new_project(h))
    r = h.post(f"/api/shots/{shot['id']}/validate")
    assert r.status_code == 200 and r.json()["valid"] is True
    assert r.json()["canvas"] == {"width": 1280, "height": 704}
    assert not [c for c in h.wangp.calls if c[0] == "wangp_generate"]


# --- renders ---------------------------------------------------------------

def test_render_happy_path_ingests_take(make_harness, settings, video):
    h = make_harness().login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    r = h.post(f"/api/shots/{shot['id']}/approve-and-render")
    assert r.status_code == 200, r.text
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "ready" and p, what="shot ready")
    take = proj["takes"][0]
    assert take["width"] == 320 and take["has_audio"] == 1
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][0]
    assert gen["wait"] is False and gen["request_id"].startswith("filmlab-") and gen["agent_context"] == "ctx1"
    assert gen["settings"]["prompt"].startswith("Yellow-green") and gen["settings"]["video_length"] == 121
    media = h.c.get(f"/api/media/{take['media_id']}")
    assert media.status_code == 200 and media.content == video
    assert h.c.get(f"/api/media/{take['thumb_media_id']}").headers["content-type"] == "image/jpeg"
    job = proj["jobs"][0]  # when WanGP started on it, for the cockpit's ETA
    assert job["submitted_at"] <= job["started_at"] <= job["finished_at"]
    assert "render_rates" in proj
    done = h.c.portal.call(h.studio.bus.replay, 0)
    done = [e for e in done if e["type"] == "render.done"][-1]["payload"]  # names for the finish notification
    assert done["shot_title"] == proj["shots"][0]["title"] and done["project_title"] and done["shot_position"] == 1


def test_render_requires_owner_approval_path_only_once(make_harness):
    h = make_harness("slow").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code == 200
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code in (409, 422)


def test_cancel(make_harness):
    h = make_harness("slow").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    job = h.post(f"/api/shots/{shot['id']}/approve-and-render").json()
    h.wait_for(lambda: h.project(pid)["jobs"][0]["status"] == "running", what="running")
    assert h.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    h.wait_for(lambda: h.project(pid)["jobs"][0]["status"] == "cancelled", what="cancelled")
    assert h.project(pid)["shots"][0]["status"] == "approved"


def test_wangp_restart_is_reconciled_from_queue_and_gallery(make_harness):
    h = make_harness("restart").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    proj = h.wait_for(lambda: (p := h.project(pid))["jobs"][0]["status"] == "done" and p, what="reconciled")
    assert proj["shots"][0]["status"] == "ready" and len(proj["takes"]) == 1
    # survived WanGP being unreachable and an empty queue before the task was restored
    assert sum(n == "wangp_session" and a["action"] == "list_queue" for n, a in h.wangp.calls) >= 5
    assert sum(n == "wangp_generate" for n, _ in h.wangp.calls) == 1
    assert any(n == "wangp_list_gallery" for n, _ in h.wangp.calls)


def test_interrupted_job_without_restore_fails_after_grace(make_harness, monkeypatch):
    monkeypatch.setattr(renders, "RESTART_GRACE_S", 0.2)
    h = make_harness("lost").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    proj = h.wait_for(lambda: (p := h.project(pid))["jobs"][0]["status"] == "interrupted" and p, what="interrupted")
    assert "no output was found" in proj["jobs"][0]["error"]
    assert proj["shots"][0]["status"] == "failed" and not proj["takes"]


def test_backend_restart_resumes_job(make_harness):
    h1 = make_harness("slow").login()
    pid = new_project(h1)
    shot = new_shot(h1, pid)
    h1.post(f"/api/shots/{shot['id']}/approve-and-render")
    h1.wait_for(lambda: h1.project(pid)["jobs"][0]["status"] == "running", what="running")
    h1.close()

    h2 = make_harness("normal")
    h2.wangp.jobs, h2.wangp.by_request = h1.wangp.jobs, h1.wangp.by_request  # same WanGP instance
    h2.login()
    proj = h2.wait_for(lambda: (p := h2.project(pid))["shots"][0]["status"] == "ready" and p, what="resumed")
    assert len(proj["takes"]) == 1
    assert not [c for c in h2.wangp.calls if c[0] == "wangp_generate"], "must not resubmit"


# --- director --------------------------------------------------------------

def test_director_drafts_but_cannot_render(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    assert h.post(f"/api/projects/{pid}/chat", {"text": "Please draft the opening shot"}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"] and not p["director_busy"] and p, what="draft")
    shot = proj["shots"][0]
    assert shot["created_by"] == "director" and shot["status"] == "draft" and shot["frames"] == 121

    h.gateway.shot_id_for_render = shot["id"]
    h.post(f"/api/projects/{pid}/chat", {"text": "render it now"})
    proj = h.wait_for(lambda: (p := h.project(pid))["approvals"] and not p["director_busy"] and p, what="approval")
    assert proj["shots"][0]["status"] == "pending_approval"
    assert proj["jobs"] == [] and not [c for c in h.wangp.calls if c[0] == "wangp_generate"]

    history = h.c.get(f"/api/projects/{pid}/chat").json()
    assert [m["role"] for m in history][-1] == "assistant"
    steps = [s for m in history for s in m["steps"]]  # the director's steps, shown in the chat
    assert [s["name"] for s in steps] == ["draft_shot", "request_render"] and all(s["ok"] for s in steps)
    assert "tool_call_id" not in history[0] and all(m["role"] != "tool" for m in history)
    follow_up = h.gateway.requests[-1]
    assert follow_up["messages"][0]["role"] == "assistant" and follow_up["messages"][1]["role"] == "tool"
    assert follow_up["user"] == f"filmlab:project:{pid}"
    assert {t["function"]["name"] for t in follow_up["tools"]} >= {"draft_shot", "request_render"}


def test_director_tools_are_project_scoped(make_harness):
    h = make_harness().login()
    other = new_project(h, "Other")
    foreign = new_shot(h, other)
    pid = new_project(h)
    out = h.c.portal.call(h.studio.tools.run, pid, "request_render", json.dumps({"shot_id": foreign["id"]}))
    assert "error" in out
    out = h.c.portal.call(h.studio.tools.run, pid, "wangp_python", "{}")
    assert out == {"error": "unknown tool wangp_python"}


def test_director_busy_rejects_second_message(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    h.studio.chat._active.add(pid)
    assert h.post(f"/api/projects/{pid}/chat", {"text": "hi"}).status_code == 409


# --- F2: contention, provenance, errors, stalls, cancel, retry ---------------

def test_queue_contention_is_visible_and_ui_task_untouched(make_harness):
    h = make_harness("contended").login()
    h.wangp.ui_polls = 10_000  # hold the owner's UI task until the test releases it
    pid = new_project(h)
    shot = new_shot(h, pid)
    assert h.c.get("/api/queue").json() == {"total": 1, "filmlab": 0, "other": 1, "running": 1}
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    job = h.wait_for(lambda: next((j for j in h.project(pid)["jobs"] if j["queue_ahead"] == 1), None),
                     what="queue position shown")
    assert job["status"] == "queued" and job["queue_running"] == "ui"
    assert h.c.get("/api/health").json()["wangp"]["queue_other"] == 1
    h.wangp.ui_polls = 1  # the UI task finishes; ours runs
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "ready" and p, what="ready")
    assert proj["jobs"][0]["queue_ahead"] is None
    assert h.wangp.cancelled_queue_ids == []
    assert not any(n == "wangp_session" and a["action"] == "cancel_queue_task" for n, a in h.wangp.calls)


def test_take_records_provenance(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    proj = h.wait_for(lambda: (p := h.project(pid))["takes"] and p, what="take")
    prov = proj["takes"][0]["provenance"]
    assert prov["seed"] == 424242 and prov["steps"] == 8 and prov["generation_s"] == 102
    assert prov["wangp_version"].startswith("WanGP v19.02") and prov["model_type"] == "ltx2_22B_distilled"
    assert prov["request_id"].startswith("filmlab-") and prov["wangp_job_id"] == "job1"
    assert prov["requested"]["video_length"] == 121 and len(prov["sha256"]) == 64
    assert prov["mcp_server"] == {"name": "WanGP", "version": "fake"}


def test_wangp_failure_is_translated(make_harness):
    h = make_harness("oom").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "failed" and p, what="failed")
    job = proj["jobs"][0]
    assert job["status"] == "failed" and "CUDA out of memory" in job["error"]
    assert job["error_info"]["code"] == "gpu_oom" and "shorter clip" in job["error_info"]["hint"]


def test_stalled_job_is_flagged_not_cancelled(make_harness, monkeypatch):
    monkeypatch.setattr(renders, "STALL_S", 0.2)
    h = make_harness("stall").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    job = h.wait_for(lambda: next((j for j in h.project(pid)["jobs"] if j["stalled"]), None), what="stalled")
    assert job["status"] == "running" and job["phase"] == "inference"
    assert not any(a.get("action") == "cancel_job" for n, a in h.wangp.calls if n == "wangp_session")


def test_cancel_of_restored_task_removes_only_filmlabs_queue_task(make_harness):
    h = make_harness("restart_hold").login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    job = h.wait_for(lambda: next((j for j in h.project(pid)["jobs"]
                                   if j["status"] == "restored" and j["phase"] == "restored"), None), what="restored")
    assert h.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    proj = h.wait_for(lambda: (p := h.project(pid))["jobs"][0]["status"] == "cancelled" and p, what="cancelled")
    assert h.wangp.cancelled_queue_ids == ["q1"]  # FilmLab's restored task, never the UI task ui1
    assert [t["queue_id"] for t in h.wangp.queue] == ["ui1"]
    assert proj["shots"][0]["status"] == "approved" and not proj["takes"]


def test_render_new_take_uses_a_new_request_id(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    shot = new_shot(h, pid)
    h.post(f"/api/shots/{shot['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["shots"][0]["status"] == "ready", what="first take")
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code == 200
    proj = h.wait_for(lambda: (p := h.project(pid))["shots"][0]["status"] == "ready" and len(p["takes"]) == 2 and p,
                      what="second take")
    rids = [a["request_id"] for n, a in h.wangp.calls if n == "wangp_generate"]
    assert len(rids) == 2 and len(set(rids)) == 2 and len(proj["jobs"]) == 2


def test_submit_network_failure_resubmits_with_same_request_id(make_harness):
    h = make_harness().login()
    real = h.wangp.wangp_generate

    def lost_reply(a):  # WanGP accepts the job, but the reply never reaches FilmLab
        real(a)
        h.wangp.wangp_generate = real
        raise httpx.ReadError("connection reset")
    h.wangp.wangp_generate = lost_reply
    pid = new_project(h)
    shot = new_shot(h, pid)
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code == 200
    h.wait_for(lambda: h.project(pid)["shots"][0]["status"] == "ready", timeout=30, what="ready after resubmit")
    rids = [a["request_id"] for n, a in h.wangp.calls if n == "wangp_generate"]
    assert len(rids) == 2 and rids[0] == rids[1] and len(h.wangp.jobs) == 1


def test_refused_render_restores_a_pending_request(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template="LTX-2 2.3 Distilled 1.0")
    h.c.portal.call(h.studio.tools.run, pid, "request_render", json.dumps({"shot_id": s["id"]}))
    h.c.portal.call(h.studio.db.execute, "INSERT INTO render_jobs (id, project_id, shot_id, request_id, settings, status, "
                    "submitted_at) VALUES ('busy', ?, ?, 'r', '{}', 'running', '2000-01-01')", (pid, s["id"]))
    r = h.post(f"/api/shots/{s['id']}/approve-and-render")
    assert r.status_code == 409 and "already has an active render" in r.json()["detail"]
    proj = h.project(pid)
    assert proj["shots"][0]["status"] == "pending_approval" and len(proj["approvals"]) == 1
