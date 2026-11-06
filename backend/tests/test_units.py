import httpx
import pytest

from app.db import Database
from app.events import EventBus
from app.media import seconds_to_frames, snap_frames
from app.wangp.client import ToolNotAllowed, WanGPClient


def test_snap_frames():
    assert snap_frames(121) == 121
    assert snap_frames(120) == 121
    assert snap_frames(124) == 121
    assert snap_frames(126) == 129
    assert snap_frames(5) == 17
    assert snap_frames(10_000) == 481
    assert seconds_to_frames(5) == 121
    assert seconds_to_frames(10) == 241


@pytest.mark.parametrize("tool,args", [
    ("wangp_python", {"action": "run", "arguments": {"code": "import os"}}),
    ("wangp_io", {"action": "write_text", "arguments": {}}),
    ("wangp_plugins", {}),
    ("wangp_postprocess", {"media": "x"}),
    ("wangp_session", {"action": "upload_chunk", "arguments": {}}),
    ("wangp_session", {"action": "cancel_queue_task", "arguments": {}}),
    ("wangp_toolbox", {"action": "media_settings", "arguments": {"path": "C:/Users/secret.txt"}}),
    ("wangp_toolbox", {"action": "media_settings", "arguments": {"media_id": "visual:1", "path": "x"}}),
    ("wangp_toolbox", {"action": "extract_image", "arguments": {"media_id": "visual:1"}}),
])
async def test_allowlist_blocks_before_network(tool, args):
    def explode(request):
        raise AssertionError("no request may be sent for a disallowed tool")
    client = WanGPClient("http://wangp.test/mcp", http=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
    with pytest.raises(ToolNotAllowed):
        await client.call(tool, args)


async def test_event_ids_monotonic_and_replay(tmp_path):
    bus = EventBus(Database(tmp_path / "db.sqlite"))
    q = bus.subscribe()
    first = await bus.publish("a", {"n": 1}, "p")
    second = await bus.publish("b", {"n": 2}, "p")
    bus.publish_ephemeral("chat.delta", {"text": "x"}, "p")
    assert second["event_id"] > first["event_id"]
    assert [e["type"] for e in await bus.replay(first["event_id"])] == ["b"]
    live = [q.get_nowait() for _ in range(3)]
    assert [e["event_id"] for e in live] == [first["event_id"], second["event_id"], None]
    assert live[0]["schema"] == "filmlab.event.v1"


async def test_overflow_tells_client_to_resync(tmp_path):
    bus = EventBus(Database(tmp_path / "db.sqlite"))
    q = bus.subscribe()
    for i in range(1001):
        bus.publish_ephemeral("x", {"i": i})
    assert q.qsize() == 1 and q.get_nowait()["type"] == "stream.overflow"


def test_error_translation():
    from app.wangp import errors
    assert errors.classify('[{"message": "CUDA out of memory. Tried to allocate 2.00 GiB"}]') == "gpu_oom"
    assert errors.classify("Generation was cancelled") == "cancelled"
    assert errors.classify("WanGP restarted and no output was found; render again to retry") == "lost_on_restart"
    assert errors.classify("model ltx2 is not installed") == "model_missing"
    assert errors.classify("something odd") == "wangp_error"
    assert errors.classify(None) is None
    assert errors.describe("gpu_oom")["hint"].startswith("Try a shorter clip")


def test_queue_view_places_our_task_and_names_the_running_owner():
    from app.renders import queue_view, summarize_queue
    queue = {"tasks": [
        {"position": 1, "status": "running", "client_id": None, "mcp_agent_id": None},
        {"position": 2, "status": "queued", "client_id": "mcp_other_1", "mcp_agent_id": "ctxB"},
        {"position": 3, "status": "queued", "client_id": "mcp_job7_1", "mcp_agent_id": "ctx1"},
    ]}
    assert queue_view(queue, "mcp_job7_", "ctx1") == {"ahead": 2, "total": 3, "running": "ui"}
    assert queue_view(queue, "mcp_gone_", "ctx1")["ahead"] is None
    assert summarize_queue(queue, "ctx1") == {"total": 3, "filmlab": 1, "other": 2, "running": 1}
