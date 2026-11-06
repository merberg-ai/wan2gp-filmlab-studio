"""F8: permission tests over every route, so a new route can't ship without auth or CSRF."""

import re

from fastapi.routing import APIRoute, APIWebSocketRoute

# Routes that answer without a session, and why.
PUBLIC = {
    ("POST", "/api/auth/login"),  # the login itself (throttled)
}


def all_routes(routes):
    for r in routes:
        sub = getattr(r, "original_router", None)  # FastAPI 0.16x wraps included routers
        if sub is not None:
            yield from all_routes(sub.routes)
        else:
            yield r


def api_routes(app):
    for r in all_routes(app.routes):
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                yield m, r.path


def fill(path):
    return re.sub(r"\{[^}]+\}", "x0", path)


def test_every_route_needs_a_session(make_harness):
    h = make_harness()
    routes = sorted(set(api_routes(h.c.app)))
    assert len(routes) > 100, len(routes)
    open_ = []
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        r = h.c.request(method, fill(path), json={})
        if r.status_code != 401:
            open_.append(f"{method} {path} -> {r.status_code}")
    assert not open_, "routes answering without a session:\n" + "\n".join(open_)


def test_every_write_needs_the_csrf_token(make_harness):
    h = make_harness().login()
    bad = []
    for method, path in sorted(set(api_routes(h.c.app))):
        if method == "GET" or (method, path) in PUBLIC:
            continue
        for headers in ({}, {"x-csrf-token": "wrong"}):
            r = h.c.request(method, fill(path), json={}, headers=headers)
            if r.status_code != 403:
                bad.append(f"{method} {path} {headers or 'no token'} -> {r.status_code}")
    assert not bad, "writes accepted without a valid CSRF token:\n" + "\n".join(bad)


def test_websockets_and_static_files(make_harness):
    h = make_harness()
    ws = [r.path for r in all_routes(h.c.app.routes) if isinstance(r, APIWebSocketRoute)]
    assert ws == ["/ws"], ws  # covered by test_websocket_rejects_unauthenticated_and_foreign_origin
    # Media is only served through authenticated routes.
    for path in ("/media/x", "/data/filmlab.sqlite", "/api/../data/filmlab.sqlite"):
        assert h.c.get(path).status_code in (401, 404), path


def test_director_tools_stay_inside_their_project(make_harness):
    """Every tool that takes an id refuses one from another project, and nothing in that project changes."""
    import json

    from tests.test_api import new_project, new_shot
    from tests.test_autonomy import take_shot, takes_of
    h = make_harness().login()
    mine, other = new_project(h, "Mine"), new_project(h, "Other")
    new_shot(h, mine, title="Mine 1")
    sc = h.post(f"/api/projects/{other}/scenes", {"title": "Their scene"}).json()
    theirs = take_shot(h, other, title="Theirs", scene_id=sc["id"])
    take = takes_of(h, other, theirs["id"])[0]["id"]
    before = h.project(other)
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, mine, name, json.dumps(args))
    sid = theirs["id"]
    calls = {
        "apply_preset": {"shot_id": sid, "preset_id": "minimax-beat", "rationale": "x"},
        "assign_refs": {"shot_id": sid, "refs": [], "rationale": "x"},
        "update_shot": {"shot_id": sid, "title": "pwned", "rationale": "x"},
        "move_shot": {"shot_id": sid, "index": 0},
        "validate_shot": {"shot_id": sid},
        "request_render": {"shot_id": sid},
        "propose_redo": {"shot_id": sid, "reason": "looks wrong to me"},
        "get_render_status": {"shot_id": sid},
        "inspect_take": {"take_id": take},
        "update_scene": {"scene_id": sc["id"], "title": "pwned"},
        "draft_shot": {"scene_id": sc["id"], "title": "x", "prompt": "a street", "rationale": "x"},
        "list_assets": {"shot_id": sid},
    }
    leaked = {n: r for n, a in calls.items() if "error" not in (r := run(n, **a))}
    assert not leaked, leaked
    r = run("request_storyboard_render", shot_ids=[sid])
    assert "error" in r or sid not in json.dumps(r), r
    assert "isn't in this project" in run("suggest_edit", change={"clips": {sid: {"in": 1}}}, note="x")["error"]
    own = h.project(mine)["shots"][0]["id"]
    assert "unknown clip field" in run("suggest_edit", change={"clips": {own: {"take_id": take}}}, note="x")["error"]
    r = run("plan_storyboard", scenes=[{"scene_id": sc["id"], "shots": [
        {"title": "x", "prompt": "a street", "seconds": 3, "rationale": "x"}]}])
    assert "error" in r, r
    after = h.project(other)
    assert [(s["id"], s["title"], s["position"], s["status"]) for s in after["shots"]] == \
           [(s["id"], s["title"], s["position"], s["status"]) for s in before["shots"]]
    assert [x["title"] for x in after.get("scenes", [])] == [x["title"] for x in before.get("scenes", [])]
    # The model can't name another project: project_id isn't an argument of any tool.
    assert "unknown argument" in run("get_project", project_id=other)["error"]
    assert "unknown tool" in run("set_autonomy", level="A3")["error"]


def test_oversized_images_are_refused_and_backups_hold_no_sessions(make_harness, tmp_path):
    import sqlite3

    from tests.fakes import make_test_image
    from tests.test_library import asset, default_variant, upload
    h = make_harness().login()
    a = asset(h, name="Huge")
    big = make_test_image(tmp_path / "big.png", size="9000x16", source="color")
    assert "at most 8192 pixels" in upload(h, default_variant(a), big, status=422)["detail"]
    r = h.post("/api/backups", {"note": "security test"})
    assert r.status_code == 201, r.text
    db = h.studio.settings.backup_dir / r.json()["name"] / "filmlab.sqlite"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM owner").fetchone()[0] == 1  # a restore can still log in
    finally:
        c.close()
    assert h.c.get("/api/projects").status_code == 200  # the live session is untouched
    for bad in ("/home/../etc", "/mnt/c/Windows/Temp", "relative/dir", "/etc/filmlab"):
        r = h.c.put("/api/backups/settings", json={"copy_dir": bad}, headers={"x-csrf-token": h.csrf})
        assert r.status_code == 422, (bad, r.text)


def test_lan_selftest_results_are_kept_with_the_client_address(make_harness):
    from tests.test_autonomy import take_shot
    from tests.test_api import new_project
    h = make_harness(client_addr="192.168.1.15", allowed_nets=("127.0.0.1/32", "192.168.1.0/24")).login()
    take_shot(h, new_project(h))
    info = h.c.get("/api/selftest").json()
    assert info["client"] == "192.168.1.15" and info["sample_video"] and info["results"] == []
    r = h.c.get(f"/api/media/{info['sample_video']}", headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    body = {"checks": [{"name": "session", "ok": True}, {"name": "websocket", "ok": False, "detail": "closed 4401"}],
            "user_agent": "Android Chrome", "screen": "412x915"}
    rec = h.post("/api/selftest", body).json()
    assert rec["client"] == "192.168.1.15" and rec["passed"] is False
    assert h.post("/api/selftest", body | {"checks": [{"name": "session", "ok": True}]}).json()["passed"] is True
    assert [x["passed"] for x in h.c.get("/api/selftest").json()["results"]] == [True, False]
    assert h.c.post("/api/selftest", json=body).status_code == 403  # CSRF as everywhere
