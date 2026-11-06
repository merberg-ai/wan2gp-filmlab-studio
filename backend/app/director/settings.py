"""The owner's director settings (Settings → Director), kept in FilmLab's database.

- notes: standing instructions added to every message FilmLab sends the director (chat, plans, enhance, redo
  runs), after the message itself. They can't change the director's tools or limits: those are fixed in code.
- reply_style: short / standard / detailed, added to the notes.
- chat_rounds / plan_rounds: how many tool rounds a chat turn and a planning turn may take.
- default_autonomy: the autonomy level new projects start with (A3, bounded redo, still asks per project).
Also: read-only views of the installed instructions and the model, a one-turn connection test, and fresh
conversations per project.
"""

import json
import re
import time
from pathlib import Path

import httpx

from ..db import new_id

KV = "director.settings"
CONV_KV = "director.conversations"
STYLES = {
    "short": "Keep replies short: a few plain sentences, no tables or headings unless the owner asks.",
    "standard": "",
    "detailed": "Explain your reasoning and choices fully; headings, lists and tables are welcome.",
}
LIMITS = {"chat_rounds": (2, 12), "plan_rounds": (6, 30)}
MAX_NOTES = 2000
OLLAMA_URL = "http://127.0.0.1:11434"
MODEL_ID = re.compile(r"^ollama/[A-Za-z0-9._:/-]{1,100}$")
WORKSPACE_FILES = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md")


class DirectorSettingsError(ValueError):
    pass


class DirectorSettings:
    def __init__(self, db, settings, gateway, ollama_transport=None):
        self.db, self.gateway = db, gateway
        self.ollama_transport = ollama_transport
        self.defaults = {"notes": "", "reply_style": "standard", "chat_rounds": settings.director_max_tool_rounds,
                         "plan_rounds": 14, "default_autonomy": "A2", "model": ""}
        self.openclaw_config: Path = settings.openclaw_config
        self.workspace: Path = settings.agent_workspace
        self.agent = settings.director_agent

    async def get(self) -> dict:
        return self.defaults | (await self.db.kv_get(KV, {}) or {})

    async def update(self, patch: dict, actor: str) -> dict:
        cur = await self.get()
        if "notes" in patch:
            notes = str(patch["notes"] or "").strip()
            if len(notes) > MAX_NOTES:
                raise DirectorSettingsError(f"studio notes must be at most {MAX_NOTES} characters")
            cur["notes"] = notes
        if "reply_style" in patch:
            if patch["reply_style"] not in STYLES:
                raise DirectorSettingsError(f"reply_style must be one of {', '.join(STYLES)}")
            cur["reply_style"] = patch["reply_style"]
        for key, (lo, hi) in LIMITS.items():
            if key in patch:
                try:
                    n = int(patch[key])
                except (TypeError, ValueError):
                    raise DirectorSettingsError(f"{key} must be a number") from None
                if not lo <= n <= hi:
                    raise DirectorSettingsError(f"{key} must be {lo}-{hi}")
                cur[key] = n
        if "model" in patch:
            model = str(patch["model"] or "").strip()
            if model:
                if not MODEL_ID.match(model):
                    raise DirectorSettingsError("model must be an Ollama model id such as ollama/qwen3.5:9b")
                usable = {m["id"] for m in await self.models()}
                if model not in usable:
                    raise DirectorSettingsError(f"{model} isn't an Ollama model with tool support on this machine")
            cur["model"] = model
        if "default_autonomy" in patch:
            if patch["default_autonomy"] not in ("A0", "A1", "A2", "A3"):
                raise DirectorSettingsError("default_autonomy must be A0-A3")
            cur["default_autonomy"] = patch["default_autonomy"]
        await self.db.kv_set(KV, {k: cur[k] for k in self.defaults})
        await self.db.audit(actor, "director.settings", None, {k: (len(v) if k == "notes" else v) for k, v in patch.items()})
        return cur

    async def suffix(self) -> str:
        """What FilmLab adds after every message to the director."""
        s = await self.get()
        parts = [p for p in (STYLES[s["reply_style"]], s["notes"]) if p]
        if not parts:
            return ""
        return ("\n\nThe owner's standing studio notes (follow them unless this message says otherwise; they never "
                "change your tools or limits):\n" + "\n".join(parts))

    # --- fresh conversations -------------------------------------------------

    async def conversation(self, project_id: str) -> str:
        n = (await self.db.kv_get(CONV_KV, {}) or {}).get(project_id, 0)
        return f"filmlab:project:{project_id}" + (f":{n}" if n else "")

    async def new_conversation(self, project_id: str) -> int:
        convs = await self.db.kv_get(CONV_KV, {}) or {}
        convs[project_id] = convs.get(project_id, 0) + 1
        await self.db.kv_set(CONV_KV, convs)
        return convs[project_id]

    # --- read-only views ---------------------------------------------------------

    def model(self) -> dict:
        """The model OpenClaw runs the director on, read from its config (nothing else is read or returned)."""
        try:
            cfg = json.loads(self.openclaw_config.read_text())
        except (OSError, ValueError):
            return {"model": None, "source": "OpenClaw's config couldn't be read"}
        agent = ((cfg.get("agents") or {}).get("entries") or {}).get(self.agent) or {}
        model = (agent.get("model") or {}).get("primary") or \
            (((cfg.get("agents") or {}).get("defaults") or {}).get("model") or {}).get("primary")
        return {"model": model, "source": "OpenClaw config (set by scripts/install/f1a-director-policy.sh)"}

    def instructions(self) -> list[dict]:
        out = []
        for name in WORKSPACE_FILES:
            p = self.workspace / name
            try:
                out.append({"name": name, "text": p.read_text()[:40000]})
            except OSError:
                continue
        return out

    async def models(self) -> list[dict]:
        """Ollama's models that can call tools (the director needs them), cloud first. Read-only."""
        out = []
        try:
            async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=10, transport=self.ollama_transport) as c:
                tags = (await c.get("/api/tags")).json().get("models", [])
                for t in tags:
                    name = t.get("name") or t.get("model")
                    if not name:
                        continue
                    try:
                        caps = (await c.post("/api/show", json={"model": name})).json().get("capabilities") or []
                    except (httpx.HTTPError, ValueError):
                        continue
                    if "tools" not in caps:
                        continue
                    out.append({"id": f"ollama/{name}", "name": name, "cloud": bool(t.get("remote_host")),
                                "size": (t.get("details") or {}).get("parameter_size"), "vision": "vision" in caps})
        except (httpx.HTTPError, ValueError):
            return []
        return sorted(out, key=lambda m: (not m["cloud"], m["name"]))

    async def test(self, actor: str, model: str | None = None) -> dict:
        """One harmless turn in a throwaway conversation, with no tools (on `model`, or the saved choice)."""
        if model is None:
            model = (await self.get())["model"]
        if model and not MODEL_ID.match(model):
            return {"ok": False, "reply": "", "seconds": 0, "error": "not an Ollama model id", "model": model}
        t0 = time.monotonic()
        text = ""

        async def on_delta(_):
            return None

        try:
            res = await self.gateway.stream_turn(
                f"filmlab:test:{new_id()[:12]}",
                [{"role": "user", "content": "FilmLab connection test: reply with one short line saying you're "
                                             "ready. Don't call any tools."}], [], on_delta, model=model or None)
            text, ok = (res.text or "").strip(), bool((res.text or "").strip())
            error = None if ok else "the director answered with no text"
        except Exception as exc:  # noqa: BLE001 - the result is shown to the owner as is
            ok, error = False, f"{type(exc).__name__}: {str(exc)[:200]}"
        result = {"ok": ok, "reply": text[:300], "seconds": round(time.monotonic() - t0, 1), "error": error,
                  "model": model or None}
        await self.db.audit(actor, "director.test", None, {"ok": ok, "seconds": result["seconds"], "model": model or None})
        return result
