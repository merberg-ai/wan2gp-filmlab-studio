"""Planning straight from Ollama, for when the director's tool calls get lost.

Some models (gemma4:31b-cloud) often write a whole plan_storyboard call that Ollama's tool-call parser then drops:
the reply arrives with no text and no tool call, and OpenClaw reports it as empty. Asked for the same plan as JSON
(Ollama's structured output, `format`), the same model answers reliably. So when a planning turn's reply comes back
empty, FilmLab asks the model for each part of the plan as JSON itself and saves it through the same
plan_storyboard tool, with the same checks.
"""

import json

import httpx

from .settings import OLLAMA_URL
from .tools import TOOLS

SYSTEM = ("You are FilmLab's film director, planning a storyboard. You answer with JSON only, in the form you are "
          "given: no text around it.")
ASK = ("\n\nAnswer with JSON only: {\"all_planned\": false, \"plan\": {the arguments of your next plan_storyboard "
       "call: \"scenes\": [one or two scenes, with their shots]}}. When every scene of my brief is planned and saved, "
       "answer {\"all_planned\": true, \"summary\": \"All planned: …\"} with a short summary instead (scenes, shots, "
       "total length, anything missing). In each prompt the references are <Picture 1>, <Picture 2>, … in refs order, "
       "numbered from 1, and every image in refs is named.")
SAVED = ("Part {n} is saved. The storyboard now has these scenes: {scenes}. If my brief has scenes you haven't planned "
         "yet, answer with the next one or two, in film order (don't repeat a scene; keep the same style, characters "
         "and setting rules). If the whole brief is planned, answer with all_planned true and the summary.")
REFUSED = ("That part wasn't saved: {errors}. Fix exactly those and answer with the corrected part (all_planned false, "
           "the same scenes).")


def _plan_schema() -> dict:
    plan = next(t for t in TOOLS if t["function"]["name"] == "plan_storyboard")["function"]["parameters"]
    return {"type": "object", "required": ["all_planned"],
            "properties": {"all_planned": {"type": "boolean"}, "summary": {"type": "string"}, "plan": plan}}


class DirectPlanner:
    """One plan, part by part, as a conversation with the model on Ollama (not OpenClaw)."""

    def __init__(self, model: str, transport=None, timeout: float = 600):
        self.model = model.removeprefix("ollama/")
        self.transport, self.timeout = transport, timeout
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}]

    async def ask(self, text: str) -> dict:
        """Send text; return the model's answer: {"all_planned": bool, "summary"?: str, "plan"?: {...}}."""
        self.messages.append({"role": "user", "content": text})
        body = {"model": self.model, "messages": self.messages, "stream": False, "think": False,
                "format": _plan_schema()}
        async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=self.timeout, transport=self.transport) as c:
            resp = await c.post("/api/chat", json=body)
        if resp.status_code != 200:
            raise DirectError(f"Ollama answered HTTP {resp.status_code}: {resp.text[:200]}")
        content = ((resp.json().get("message") or {}).get("content") or "").strip()
        self.messages.append({"role": "assistant", "content": content})
        try:
            answer = json.loads(content.removeprefix("```json").removeprefix("```").removesuffix("```"))
        except ValueError as exc:
            raise DirectError("the model's answer wasn't JSON") from exc
        if not isinstance(answer, dict):
            raise DirectError("the model's answer wasn't a JSON object")
        return answer


class DirectError(RuntimeError):
    pass
