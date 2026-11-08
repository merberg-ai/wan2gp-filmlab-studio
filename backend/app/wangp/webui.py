"""WanGP's own web page and its model downloads, for the cockpit.

The web page: WanGP serves it on a port of its own (its --server-port; FilmLab talks to its MCP server on another),
and nothing FilmLab can ask reports it. So FilmLab looks on WanGP's host: FILMLAB_WANGP_WEBUI when set, else
the usual ports, and keeps the first that answers as WanGP (its Gradio /config names "WanGP" and "DeepBeepMeep").

Model downloads: WanGP downloads a model's files the first time something renders with it and tracks the bytes
itself, but its job status only carries a status line. When that line says it is downloading, FilmLab watches
WanGP's model folder (ckpts) grow: so much so far, at that speed. There's no total, so no percentage.
"""

import asyncio
import glob
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("filmlab.wangp.webui")

PORTS = (7860, 8888, 7861, 7862, 7863, 8080, 8889, 7870, 8000, 8001, 9000)
DETECT_TTL_S, MISS_TTL_S = 600, 60
MARKERS = ("DeepBeepMeep", "Wan<SUP")
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
CKPTS_GLOBS = ("/mnt/[a-z]/[Ww]an2[Gg][Pp]*/app/ckpts", "/mnt/[a-z]/[Ww]an2[Gg][Pp]*/ckpts",
               "/mnt/[a-z]/*/[Ww]an2[Gg][Pp]*/app/ckpts", "/mnt/[a-z]/*/[Ww]an*.git/app/ckpts",
               "~/[Ww]an2[Gg][Pp]*/app/ckpts", "~/[Ww]an2[Gg][Pp]*/ckpts", "C:/[Ww]an2[Gg][Pp]*/app/ckpts",
               "D:/[Ww]an2[Gg][Pp]*/app/ckpts")


def downloading(status_text: str | None) -> bool:
    return "download" in (status_text or "").lower()


class WanGPWebUI:
    def __init__(self, mcp_url: str, override: str | None = None, ckpts: str | None = None, transport=None):
        u = urlparse(mcp_url)
        self.host = u.hostname or "127.0.0.1"
        self.mcp_port = u.port
        self.override = (override or os.environ.get("FILMLAB_WANGP_WEBUI") or "").strip()
        self.transport = transport
        self._found: tuple[float, dict | None] | None = None
        self._lock = asyncio.Lock()
        self.ckpts = self._find_ckpts(ckpts or os.environ.get("FILMLAB_WANGP_CKPTS"))
        self._watch: dict[str, tuple[float, int, float, int]] = {}  # job -> (started, start bytes, last t, last bytes)

    # --- the web page --------------------------------------------------------------------------------

    async def locate(self) -> dict | None:
        """{"url": full URL, or None when only "port" is known, "port", "host_is_local"}: host_is_local means the
        cockpit should use its own address (WanGP runs on the machine FilmLab's server runs on)."""
        if self.override:
            return {"url": self.override, "port": urlparse(self.override).port, "host_is_local": False}
        async with self._lock:
            if self._found and time.monotonic() - self._found[0] < (DETECT_TTL_S if self._found[1] else MISS_TTL_S):
                return self._found[1]
            found = await self._probe()
            self._found = (time.monotonic(), found)
            return found

    async def _probe(self) -> dict | None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=0.8), transport=self.transport) as c:
            async def is_wangp(port: int) -> bool:
                try:
                    async with c.stream("GET", f"http://{self.host}:{port}/config") as r:
                        if r.status_code != 200:
                            return False
                        head = b""
                        async for chunk in r.aiter_bytes():
                            head += chunk
                            if len(head) > 16384:
                                break
                        text = head.decode("utf-8", "ignore")
                        return any(m in text for m in MARKERS)
                except (httpx.HTTPError, OSError):
                    return False
            ports = [p for p in PORTS if p != self.mcp_port]
            hits = await asyncio.gather(*(is_wangp(p) for p in ports))
        port = next((p for p, ok in zip(ports, hits) if ok), None)
        if port is None:
            log.info("WanGP's web page wasn't found on %s (tried %s)", self.host, ports)
            return None
        local = self.host in LOOPBACK
        return {"url": None if local else f"http://{self.host}:{port}/", "port": port, "host_is_local": local}

    # --- model downloads -----------------------------------------------------------------------------

    @staticmethod
    def _find_ckpts(given: str | None) -> Path | None:
        if given:
            p = Path(given).expanduser()
            return p if p.is_dir() else None
        for pattern in CKPTS_GLOBS:
            for hit in sorted(glob.glob(os.path.expanduser(pattern))):
                if Path(hit).is_dir():
                    return Path(hit)
        return None

    def _size(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.ckpts):
            for f in files:
                try:
                    total += os.stat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total

    async def download_progress(self, job_id: str) -> dict | None:
        """While a job downloads: {"bytes": arrived since it started, "speed": bytes/s lately}; None when FilmLab
        can't see WanGP's model folder."""
        if not self.ckpts:
            return None
        size = await asyncio.to_thread(self._size)
        t = time.monotonic()
        started, base, last_t, last = self._watch.get(job_id) or (t, size, t, size)
        speed = (size - last) / (t - last_t) if t > last_t else 0.0
        self._watch[job_id] = (started, base, t, size)
        return {"bytes": max(0, size - base), "speed": max(0.0, speed)}

    def forget(self, job_id: str):
        self._watch.pop(job_id, None)
