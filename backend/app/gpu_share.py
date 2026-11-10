"""Sharing one GPU between WanGP and local Ollama models (Health → GPU sharing = "unload", the default).

WanGP releases its model after every generation (its "U" unload policy), but Ollama keeps a model in VRAM for minutes
after each request, and a local model loading in the middle of a render can run either of them out of memory. With
"unload", FilmLab takes turns:
- before any job goes to WanGP (renders, batches, redo runs, images, voices, music), FilmLab asks Ollama to unload its
  local models (keep_alive 0) and waits until they're gone;
- a director turn or take inspection on a LOCAL model waits while WanGP has FilmLab work queued or running;
- a job submitted during such a turn waits for the turn to end, so the model isn't pulled out from under it.
Cloud models (name ending in -cloud / :cloud) never touch this GPU and are never held back.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from .inspect_takes import is_cloud_model

log = logging.getLogger("filmlab.gpu")
ACTIVE = ("submitting", "queued", "running", "cancelling", "restored")
UNLOAD_WAIT_S = 30
POLL_S = 3


class GpuShare:
    def __init__(self, studio, transport: httpx.AsyncBaseTransport | None = None):
        self.s = studio
        self._transport = transport
        self.llm_turns = 0          # local-model turns using the GPU right now
        self._llm_idle = asyncio.Event()
        self._llm_idle.set()
        self.waiting: dict[str, str] = {}  # what's waiting for the GPU, for the Health page: key -> why

    async def mode(self) -> str:
        return (await self.s.health.settings())["gpu_share"]

    @staticmethod
    def local(model: str | None) -> bool:
        """A model that runs on this PC's GPU (Ollama local), not in Ollama's cloud. None/'' = unknown: not local."""
        name = str(model or "").removeprefix("ollama/")
        return bool(name) and not is_cloud_model(name)

    async def wangp_busy(self) -> bool:
        marks = ", ".join("?" * len(ACTIVE))
        for table in ("render_jobs", "image_jobs"):
            if await self.s.db.fetchone(f"SELECT 1 FROM {table} WHERE status IN ({marks}) LIMIT 1", ACTIVE):
                return True
        return False

    def llm_busy(self) -> bool:
        return self.llm_turns > 0

    # --- the LLM side -------------------------------------------------------------------

    @asynccontextmanager
    async def llm(self, model: str | None, key: str, on_wait=None):
        """Around a local-model LLM call: wait while WanGP has work, then hold WanGP submissions until it ends.
        on_wait: async (message) -> None, called once if it has to wait (the chat says why)."""
        if not self.local(model) or await self.mode() != "unload":
            yield
            return
        told = False
        try:
            while await self.wangp_busy():
                if not told:
                    told = True
                    msg = (f"Waiting for the GPU: WanGP is rendering, and {str(model).removeprefix('ollama/')} runs on "
                           "the same GPU. This starts when the render queue is empty (Stop cancels it).")
                    self.waiting[key] = msg
                    if on_wait:
                        await on_wait(msg)
                await asyncio.sleep(POLL_S)
        finally:
            self.waiting.pop(key, None)
        self.llm_turns += 1
        self._llm_idle.clear()
        try:
            yield
        finally:
            self.llm_turns -= 1
            if self.llm_turns == 0:
                self._llm_idle.set()

    # --- the WanGP side -----------------------------------------------------------------

    async def before_wangp(self, key: str, on_wait=None):
        """Right before a job goes to WanGP: let a running local-model turn finish, then unload Ollama's local models."""
        if await self.mode() != "unload":
            return
        if self.llm_busy():
            msg = "waiting for the director (a local model on this GPU) to finish its turn"
            self.waiting[key] = msg
            if on_wait:
                await on_wait(msg)
            try:
                await self._llm_idle.wait()
            finally:
                self.waiting.pop(key, None)
        await self.unload_local()

    async def unload_local(self) -> list[str]:
        """Ask Ollama to unload every local model it holds, and wait (up to UNLOAD_WAIT_S) until they're gone."""
        loaded = await self.s.health.ollama_loaded()
        if not loaded:
            return []
        url = (await self.s.health.settings())["ollama_url"]
        names = [m["name"] for m in loaded]
        try:
            async with httpx.AsyncClient(base_url=url, timeout=30, transport=self._transport) as c:
                for name in names:
                    await c.post("/api/generate", json={"model": name, "keep_alive": 0})
        except httpx.HTTPError as exc:
            log.warning("couldn't ask Ollama to unload %s: %s", names, exc)
        for _ in range(int(UNLOAD_WAIT_S / 0.5)):
            if not await self.s.health.ollama_loaded():
                break
            await asyncio.sleep(0.5)
        still = await self.s.health.ollama_loaded()
        freed = sum(m["vram_gb"] for m in loaded if m["name"] not in {x["name"] for x in still})
        text = (f"Unloaded {', '.join(f'{m['name']} ({m['vram_gb']} GB)' for m in loaded)} from the GPU before WanGP"
                + (f"; still loaded: {', '.join(m['name'] for m in still)}" if still else ""))
        if self.s.console:
            await self.s.console.add("warn" if still else "info", "GPU", text, None,
                                     {"unloaded": names, "freed_gb": round(freed, 1), "still": [m["name"] for m in still]})
        await self.s.db.audit("system", "gpu.unload_ollama", None, {"models": names, "still": [m["name"] for m in still]})
        return names
