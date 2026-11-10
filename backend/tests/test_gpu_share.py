"""One GPU for WanGP and local Ollama models (gpu_share.py): they take turns."""

import asyncio

from tests.test_edit import rendered_shot, small_project


def put(h, url, body):
    return h.c.put(url, json=body, headers={"x-csrf-token": h.csrf})


def test_local_models_are_unloaded_before_a_render(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid)
    assert h.c.get("/api/system/health").json()["settings"]["gpu_share"] == "unload"  # the default
    h.vision.loaded = [{"name": "qwen3.5:9b", "size_vram": 6_600_000_000},
                       {"name": "gemma4:31b-cloud", "size_vram": 0}]  # cloud: not on this GPU, left alone
    checks = h.c.get(f"/api/shots/{s['id']}/checks").json()["issues"]
    assert not any("qwen3.5:9b" in i["message"] for i in checks)  # nothing to warn about: it will be unloaded
    r = h.post(f"/api/shots/{s['id']}/approve-and-render")
    assert r.status_code == 200, r.text
    assert h.vision.unloaded == ["qwen3.5:9b"] and h.vision.loaded == [{"name": "gemma4:31b-cloud", "size_vram": 0}]
    console = h.c.get("/api/console").json()
    rows = console.get("entries", console) if isinstance(console, dict) else console
    assert any("Unloaded qwen3.5:9b (6.6 GB) from the GPU before WanGP" in e["message"] for e in rows)
    # A model that won't unload: the render still goes (after the wait), with a warning.
    h.vision.loaded, h.vision.sticky = [{"name": "llava:latest", "size_vram": 4_000_000_000}], True
    gs = h.studio.gpu_share
    import app.gpu_share as g
    g.UNLOAD_WAIT_S = 1
    assert h.c.portal.call(gs.unload_local) == ["llava:latest"]
    rows = h.c.get("/api/console").json()
    rows = rows.get("entries", rows) if isinstance(rows, dict) else rows
    assert any("still loaded: llava:latest" in e["message"] for e in rows)
    g.UNLOAD_WAIT_S = 30


def test_a_local_director_waits_for_renders_and_holds_new_ones(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid)
    gs = h.studio.gpu_share
    assert gs.local("ollama/qwen3.5:9b") and not gs.local("ollama/gemma4:31b-cloud") and not gs.local(None)
    assert put(h, "/api/director/settings", {"model": "ollama/qwen3.5:9b"}).status_code == 200
    # WanGP has work: the turn waits (the chat says why), then runs once it's done.
    job = h.c.portal.call(h.studio.db.fetchone, "SELECT id FROM render_jobs WHERE project_id = ?", (pid,))["id"]
    h.c.portal.call(h.studio.db.execute, "UPDATE render_jobs SET status = 'running' WHERE id = ?", (job,))
    sent = len(h.gateway.requests)
    assert h.post(f"/api/projects/{pid}/chat", {"text": "hello"}).status_code == 202
    h.wait_for(lambda: any("Waiting for the GPU" in (m.get("content") or "")
                           for m in h.c.get(f"/api/projects/{pid}/chat").json()), what="waiting note")
    assert len(h.gateway.requests) == sent  # nothing sent to the model yet
    h.c.portal.call(h.studio.db.execute, "UPDATE render_jobs SET status = 'done' WHERE id = ?", (job,))
    h.wait_for(lambda: len(h.gateway.requests) > sent, timeout=15, what="the turn after the render")
    h.wait_for(lambda: not h.studio.chat.busy(pid), what="turn end")

    # During a local-model turn a job for WanGP is held until the turn ends.
    async def scenario():
        held = None
        async with gs.llm("ollama/qwen3.5:9b", "test"):
            task = asyncio.create_task(gs.before_wangp("job:x"))
            await asyncio.sleep(0.3)
            held = not task.done() and "job:x" in gs.waiting
        await asyncio.wait_for(task, 3)
        return held
    assert h.c.portal.call(scenario) is True

    # A cloud model is never held back.
    assert put(h, "/api/director/settings", {"model": "ollama/gemma4:31b-cloud"}).status_code == 200
    h.c.portal.call(h.studio.db.execute, "UPDATE render_jobs SET status = 'running' WHERE id = ?", (job,))
    sent = len(h.gateway.requests)
    h.post(f"/api/projects/{pid}/chat", {"text": "hello again"})
    h.wait_for(lambda: len(h.gateway.requests) > sent, timeout=10, what="cloud turn during a render")
    h.c.portal.call(h.studio.db.execute, "UPDATE render_jobs SET status = 'done' WHERE id = ?", (job,))
