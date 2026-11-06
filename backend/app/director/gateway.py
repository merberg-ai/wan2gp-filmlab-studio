"""Client for the OpenClaw Gateway's OpenAI-compatible chat endpoint.

The backend supplies the director's tools as client functions on every
request and executes them itself, so the backend is the only place where
director actions take effect. Conversation continuity comes from a stable
`user` value per project (the Gateway derives the session key from it).
"""

import json
from dataclasses import dataclass, field

import httpx


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


class GatewayError(RuntimeError):
    pass


class GatewayClient:
    def __init__(self, base_url: str, token: str, agent: str, http: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.agent = agent
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3, read=300))

    async def aclose(self):
        await self.http.aclose()

    async def health(self) -> bool:
        try:
            resp = await self.http.get(f"{self.base_url}/v1/models", headers=self._headers, timeout=5)
            return resp.status_code == 200 and any(
                m.get("id") == f"openclaw/{self.agent}" for m in resp.json().get("data", []))
        except httpx.HTTPError:
            return False

    async def stream_turn(self, conversation: str, messages: list[dict], tools: list[dict], on_delta,
                          model: str | None = None) -> TurnResult:
        """model: a provider/model id (e.g. ollama/qwen3.5:9b) for this request only, via OpenClaw's
        x-openclaw-model header; None runs the agent on its configured model."""
        body = {"model": f"openclaw/{self.agent}", "user": conversation, "stream": True,
                "messages": messages, "tools": tools}
        headers = self._headers | ({"x-openclaw-model": model} if model else {})
        result, calls = TurnResult(), {}
        async with self.http.stream("POST", f"{self.base_url}/v1/chat/completions",
                                    headers=headers, json=body) as resp:
            if resp.status_code != 200:
                raise GatewayError(f"gateway HTTP {resp.status_code}: {(await resp.aread())[:300]!r}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                msg = json.loads(data)
                if "error" in msg:
                    raise GatewayError(str(msg["error"])[:500])
                choice = (msg.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    result.text += delta["content"]
                    await on_delta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                result.finish_reason = choice.get("finish_reason") or result.finish_reason
        result.tool_calls = list(calls.values())
        return result
