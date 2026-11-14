"""WanGP MCP v2.1 client (Streamable HTTP) with a hard tool/action allowlist.

Only the calls FilmLab needs are permitted; anything else (notably
wangp_python, wangp_io writes, plugins, postprocess) raises before a request
is sent. All calls carry FilmLab's own workspace agent_context.
"""

import json
import logging
from urllib.parse import urljoin

import httpx

from .. import VERSION

log = logging.getLogger("filmlab.wangp")
PROTOCOL_VERSION = "2025-06-18"

# tool -> allowed `action` values (None = tool takes no action argument).
ALLOWED: dict[str, set[str | None]] = {
    "wangp_validate": {None},
    "wangp_generate": {None},
    "wangp_templates": {None},
    "wangp_list_gallery": {None},
    "wangp_workspace": {"current", "list", "create"},
    "wangp_session": {"get_job", "wait_jobs", "cancel_job", "list_queue", "create_file_download",
                      "create_gallery_upload"},  # uploads go to FilmLab's own workspace gallery
    "wangp_toolbox": {"media_settings"},  # read-only: the settings that produced a gallery item
    # read-only: a model's default settings, its definition (which settings it has, their ranges) and its LoRAs,
    # for the project's generation settings (tuning.py)
    "wangp_model": {"defaults", "definition", "loras"},
}
# Per-action argument allowlists, where an action could otherwise reach beyond the gallery.
# media_settings also accepts `path`, which would read arbitrary files: only media ids pass.
ALLOWED_ARGS: dict[tuple[str, str], set[str]] = {
    ("wangp_toolbox", "media_settings"): {"media_id"},
    ("wangp_session", "create_gallery_upload"): {"filename"},
    ("wangp_model", "defaults"): set(),
    ("wangp_model", "definition"): set(),
    ("wangp_model", "loras"): {"cursor"},
}
# Catalogue tools reject agent_context ("Unknown argument"); they are workspace-independent.
NO_CONTEXT = {"wangp_templates", "wangp_model"}


class WanGPError(RuntimeError):
    pass


class ToolNotAllowed(WanGPError):
    pass


class WanGPClient:
    def __init__(self, url: str, http: httpx.AsyncClient | None = None):
        self.url = url
        # Under WSL mirrored networking a closed Windows port drops SYNs instead of
        # refusing them, so a short connect timeout is what detects "WanGP is down".
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3, read=660))
        self.session_id: str | None = None
        self._next_id = 1
        self._initialized = False
        self.agent_context: str | None = None
        self.server_info: dict = {}

    async def aclose(self):
        await self.http.aclose()

    # --- transport -------------------------------------------------------

    async def _post(self, message: dict):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        resp = await self.http.post(self.url, json=message, headers=headers)
        resp.raise_for_status()
        self.session_id = resp.headers.get("mcp-session-id") or self.session_id
        if "id" not in message:
            return None
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for chunk in resp.text.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(ln[5:].lstrip() for ln in chunk.split("\n") if ln.startswith("data:"))
                if data:
                    msg = json.loads(data)
                    if msg.get("id") == message["id"]:
                        return msg
            raise WanGPError("no JSON-RPC response in SSE stream")
        return resp.json()

    async def _request(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        reply = await self._post(msg)
        if "error" in reply:
            raise WanGPError(f"{method}: {reply['error']}")
        return reply["result"]

    async def initialize(self):
        if self._initialized:
            return
        res = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "filmlab-studio", "version": VERSION}})
        self.server_info = (res or {}).get("serverInfo") or {}
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    # --- tools -----------------------------------------------------------

    async def call(self, tool: str, arguments: dict, *, with_context: bool = True) -> dict:
        action = arguments.get("action")
        if tool not in ALLOWED or action not in ALLOWED[tool]:
            raise ToolNotAllowed(f"{tool}({action}) is not on FilmLab's WanGP allowlist")
        allowed_args = ALLOWED_ARGS.get((tool, action))
        if allowed_args is not None and set(arguments.get("arguments") or {}) - allowed_args:
            raise ToolNotAllowed(f"{tool}({action}) only accepts {sorted(allowed_args)}")
        return await self._call(tool, arguments, with_context)

    async def _call(self, tool: str, arguments: dict, with_context: bool = True) -> dict:
        await self.initialize()
        args = dict(arguments)
        if with_context and self.agent_context and tool not in NO_CONTEXT:
            args.setdefault("agent_context", self.agent_context)
        result = await self._request("tools/call", {"name": tool, "arguments": args})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise WanGPError(f"{tool}: {text[:500]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise WanGPError(f"{tool}: non-JSON result: {text[:200]}") from exc

    async def session(self, action: str, **arguments) -> dict:
        return await self.call("wangp_session", {"action": action, "arguments": arguments})

    async def media_settings(self, media_id: str) -> dict:
        return await self.call("wangp_toolbox", {"action": "media_settings", "arguments": {"media_id": media_id}})

    async def cancel_own_queue_task(self, client_id_prefix: str) -> str | None:
        """Cancel the queue task WanGP restored for one of FilmLab's jobs.

        cancel_queue_task can remove *any* task, including the owner's WanGP UI jobs, so it
        is not on the allowlist: this is its only use. The queue is re-read immediately
        before, and only a task whose client_id is FilmLab's (mcp_<job_id>_N) is cancelled.
        Returns the cancelled queue_id, or None if no such task is queued.
        """
        queue = await self.session("list_queue")
        task = next((t for t in queue.get("tasks", [])
                     if (t.get("client_id") or "").startswith(client_id_prefix)
                     and t.get("mcp_agent_id") in (None, self.agent_context)), None)
        if not task or not task.get("queue_id"):
            return None
        await self._call("wangp_session", {"action": "cancel_queue_task", "arguments": {"queue_id": task["queue_id"]}})
        return task["queue_id"]

    async def ensure_workspace(self, name: str, saved_context: str | None) -> dict:
        """Resume FilmLab's dedicated workspace, creating it on first use.

        Request-id receipts are scoped to the agent context, so the same
        context must be resumed on every run (action="current"), never attach.
        """
        if saved_context:
            ws = await self.call("wangp_workspace", {"action": "current", "agent_context": saved_context},
                                 with_context=False)
        else:
            found = await self.call("wangp_workspace", {"action": "list", "query": name}, with_context=False)
            match = next((w for w in found.get("workspaces", []) if w.get("name") == name), None)
            if match:
                ws = await self.call("wangp_workspace", {"action": "current", "agent_context": match["mcp_agent_id"]},
                                     with_context=False)
            else:
                ws = await self.call("wangp_workspace", {"action": "create", "name": name, "storage_mode": "dedicated"},
                                     with_context=False)
        self.agent_context = ws["agent_context"]
        return ws

    def _same_origin(self, url: str) -> str:
        full = urljoin(self.url, url)
        if httpx.URL(full).netloc != httpx.URL(self.url).netloc or httpx.URL(full).scheme != httpx.URL(self.url).scheme:
            raise WanGPError(f"WanGP returned a transfer URL on another host: {full}")
        return full

    async def upload_gallery(self, path, mime: str, filename: str) -> dict:
        """Upload one image or audio file into FilmLab's workspace gallery; returns WanGP's item (media_id)."""
        info = await self.session("create_gallery_upload", filename=filename)
        url = self._same_origin(info["upload_url"])
        with open(path, "rb") as fh:
            resp = await self.http.put(url, content=fh.read(), headers={"Content-Type": mime})
        resp.raise_for_status()
        item = resp.json()
        if not item.get("media_id"):
            raise WanGPError(f"upload of {filename} returned no media_id: {str(item)[:200]}")
        return item

    async def download(self, media_id: str, dest) -> dict:
        info = await self.session("create_file_download", source=media_id, include_sha256=True)
        url = self._same_origin(info["download_url"])
        async with self.http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 20):
                    fh.write(chunk)
        return info
