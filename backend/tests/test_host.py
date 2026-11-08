"""Startup & WanGP: the Windows helper's shared folder (host.py), and FilmLab noticing WanGP stop and come back."""

import json
import time
from datetime import datetime, timezone

import app.host as host_mod
import app.main as main_mod
from app.host import Host, HostError
from tests.test_api import new_project, new_shot


def write_status(folder, age_s=0, **wangp):
    at = datetime.fromtimestamp(time.time() - age_s, timezone.utc).isoformat()
    (folder / "host-status.json").write_text("\ufeff" + json.dumps({  # PowerShell 5 writes a BOM
        "helper_pid": 1, "updated_at": at,
        "wangp": {"found": True, "path": "D:\\wan2gp\\app", "running": True, "mcp_port": 8000, "webui_port": 8888} | wangp,
        "settings": {"wangp_autostart": True, "wangp_restart": False, "start_at_logon": False}}), encoding="utf-8")


def test_the_helpers_status_and_requests(tmp_path, monkeypatch):
    assert Host("").status() == {"available": False, "helper_running": False}
    h = Host(str(tmp_path))
    assert h.status() == {"available": True, "helper_running": False}  # no status yet
    write_status(tmp_path)
    s = h.status()
    assert s["helper_running"] and s["wangp"]["webui_port"] == 8888
    write_status(tmp_path, age_s=120)
    assert h.status()["helper_running"] is False  # stale: the helper isn't running
    name = h.request("start-wangp")
    req = json.loads((tmp_path / "requests" / f"{name}.json").read_text())
    assert req["action"] == "start-wangp" and not list((tmp_path / "requests").glob("*.tmp"))
    try:
        h.request("run-anything", command="calc.exe")
        raise AssertionError("an unknown action must be refused")
    except HostError:
        pass
    # settings: only the known ones go through; a folder must hold wgp.py
    wan = tmp_path / "Wan2GP" / "app"
    wan.mkdir(parents=True)
    monkeypatch.setattr(host_mod, "wsl_path", lambda p: wan if "Wan2GP" in p else tmp_path / "nowhere")
    try:
        h.request("settings", wangp_path="D:\\Wan2GP\\app")
        raise AssertionError("a folder without wgp.py must be refused")
    except HostError as exc:
        assert "isn't a WanGP folder" in str(exc)
    (wan / "wgp.py").write_text("")
    name = h.request("settings", wangp_path="D:\\Wan2GP\\app\\", start_at_logon=1, launcher="evil.cmd")
    req = json.loads((tmp_path / "requests" / f"{name}.json").read_text())
    assert req["wangp_path"] == "D:\\Wan2GP\\app" and req["start_at_logon"] is True and "launcher" not in req
    assert host_mod.wsl_path.__name__ == "<lambda>"
    assert str(host_mod.Host.__init__)  # (module intact)


def test_wsl_path():
    assert str(host_mod.wsl_path("D:\\wan2gp\\app")) == "/mnt/d/wan2gp/app"
    assert host_mod.wsl_path("not a path") is None


def test_settings_api(make_harness, tmp_path):
    h = make_harness().login()
    r = h.c.get("/api/host").json()
    assert r["available"] is False and r["wangp_connected"] is True
    assert h.post("/api/host/wangp/start").status_code == 409  # no helper folder: says how to set it up
    h.studio.host = Host(str(tmp_path))
    write_status(tmp_path, running=False)
    r = h.c.get("/api/host").json()
    assert r["helper_running"] and r["wangp"]["running"] is False
    assert h.post("/api/host/wangp/start").status_code == 202
    r = h.c.patch("/api/host/settings", json={"wangp_restart": True, "start_at_logon": False},
                  headers={"x-csrf-token": h.csrf})
    assert r.status_code == 202, r.text
    reqs = [json.loads(p.read_text()) for p in (tmp_path / "requests").glob("*.json")]
    assert {q["action"] for q in reqs} == {"start-wangp", "settings"}
    assert {"wangp_restart": True, "start_at_logon": False}.items() <= next(q for q in reqs if q["action"] == "settings").items()


def test_wangp_stopping_pauses_new_work_and_its_return_carries_on(make_harness, monkeypatch):
    monkeypatch.setattr(main_mod, "WANGP_WATCH_S", 0.05)
    h = make_harness().login()
    pid = new_project(h)
    a, b, c = (new_shot(h, pid, title=t, template="LTX-2 2.3 Distilled 1.0") for t in ("A", "B", "C"))
    r = h.post(f"/api/projects/{pid}/batches", {"shot_ids": [a["id"], b["id"]]})
    assert r.status_code == 201, r.text
    h.wangp.down_polls = 10_000  # WanGP stops answering while the first shot renders
    h.wait_for(lambda: not h.c.get("/api/host").json()["wangp_connected"], what="WanGP noticed down")
    assert h.c.get("/api/health").json()["wangp"]["connected"] is False
    r = h.post(f"/api/shots/{c['id']}/approve-and-render")
    assert r.status_code in (409, 503) and "WanGP" in r.text
    bt = h.wait_for(lambda: (x := h.project(pid)["batch"]) and x["status"] == "paused" and x, what="batch waits")
    assert "WanGP isn't running" in bt["pause_reason"]
    assert bt["id"]
    h.wangp.down_polls = 0  # WanGP is back
    h.wait_for(lambda: h.c.get("/api/host").json()["wangp_connected"], what="WanGP back")
    h.wait_for(lambda: h.project(pid)["batch"]["status"] == "done", what="batch carries on and finishes")
    events = [e["payload"]["connected"] for e in h.c.portal.call(h.studio.bus.replay, 0) if e["type"] == "wangp.status"]
    assert events[-2:] == [False, True]
