"""Live GPU load and VRAM for the cockpit's stats bars (read-only).

WanGP's MCP server doesn't expose the stats its own UI shows, so FilmLab reads the GPU itself with nvidia-smi
(the same read Health uses). Sampling runs only while at least one cockpit is connected to /ws; each sample is
broadcast as an ephemeral `system.gpu` event and never stored.
"""

import asyncio
import logging
import subprocess
from typing import Callable

from .events import EventBus

log = logging.getLogger("filmlab.gpu")
INTERVAL_S = 2.0
QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"


def _num(x: str) -> float | None:
    try:
        return float(x)
    except ValueError:  # "[N/A]" on GPUs that don't report it
        return None


def nvidia_smi() -> dict | None:
    """One sample of GPU 0, or None when there's no NVIDIA GPU or nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        name, load, used, total, temp, power = [x.strip() for x in out[0].split(",")]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    used_mb, total_mb = _num(used), _num(total)
    if used_mb is None or not total_mb:
        return None
    return {"gpu": name, "load": _num(load), "vram_used_gb": round(used_mb / 1024, 1),
            "vram_total_gb": round(total_mb / 1024, 1), "temp_c": _num(temp), "power_w": _num(power)}


class GpuMonitor:
    def __init__(self, bus: EventBus, query: Callable[[], dict | None] = nvidia_smi, interval: float | None = None):
        self.bus, self.query, self.interval = bus, query, interval
        self.latest: dict | None = None
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            if self.bus.has_subscribers():
                try:
                    sample = await asyncio.to_thread(self.query)
                except Exception:  # noqa: BLE001 - stats are cosmetic; never let them kill the loop
                    log.exception("GPU sample failed")
                    sample = None
                self.latest = sample
                self.bus.publish_ephemeral("system.gpu", sample or {"unavailable": True})
            await asyncio.sleep(self.interval or INTERVAL_S)

    async def shutdown(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
