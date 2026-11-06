import asyncio
import subprocess

from app import gpu_stats
from app.gpu_stats import GpuMonitor, nvidia_smi


class Run:
    def __init__(self, stdout):
        self.stdout = stdout


def test_nvidia_smi_parses_a_sample(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Run("NVIDIA GeForce RTX 4080 SUPER, 87, 14540, 16376, 66, 285.40\n"))
    assert nvidia_smi() == {"gpu": "NVIDIA GeForce RTX 4080 SUPER", "load": 87.0, "vram_used_gb": 14.2,
                            "vram_total_gb": 16.0, "temp_c": 66.0, "power_w": 285.4}


def test_nvidia_smi_tolerates_missing_fields_and_no_gpu(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Run("Some GPU, [N/A], 1000, 8192, [N/A], [N/A]\n"))
    s = nvidia_smi()
    assert s["load"] is None and s["temp_c"] is None and s["vram_total_gb"] == 8.0

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(subprocess, "run", missing)
    assert nvidia_smi() is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Run(""))
    assert nvidia_smi() is None


class Bus:
    def __init__(self, subscribed):
        self.subscribed, self.events = subscribed, []

    def has_subscribers(self):
        return self.subscribed

    def publish_ephemeral(self, type_, payload, project_id=None):
        self.events.append((type_, payload))


def test_samples_only_while_a_cockpit_is_connected():
    async def run():
        calls = []
        idle = Bus(False)
        m = GpuMonitor(idle, query=lambda: calls.append(1) or {"load": 1}, interval=0.01)
        m.start()
        await asyncio.sleep(0.08)
        assert not calls and not idle.events
        idle.subscribed = True
        await asyncio.sleep(0.08)
        await m.shutdown()
        assert calls and idle.events[0] == ("system.gpu", {"load": 1}) and m.latest == {"load": 1}

        def broken():
            raise RuntimeError("boom")
        b = Bus(True)
        m = GpuMonitor(b, query=broken, interval=0.01)
        m.start()
        await asyncio.sleep(0.05)
        await m.shutdown()
        assert b.events and b.events[0] == ("system.gpu", {"unavailable": True})
    asyncio.run(run())


def test_cockpit_receives_gpu_samples_over_ws(make_harness, monkeypatch):
    monkeypatch.setattr(gpu_stats, "INTERVAL_S", 0.05)
    h = make_harness().login()
    with h.c.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:19300"}) as ws:
        for _ in range(50):
            ev = ws.receive_json()
            if ev["type"] == "system.gpu":
                break
        assert ev["type"] == "system.gpu" and ev["event_id"] is None
        assert ev["payload"]["vram_total_gb"] == 16.0 and ev["payload"]["load"] == 37.0
