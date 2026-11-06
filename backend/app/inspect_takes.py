"""Take inspection: what FilmLab can tell about a take without a human watching it.

Two parts, both off the GPU:
- technical, with ffmpeg on the CPU: length against the request, black frames, frozen picture, silent or
  clipping sound;
- visual, from four keyframes (also kept as a contact sheet) sent to a vision model through the local Ollama
  endpoint. The default is the director's own cloud model, so it never competes with WanGP for VRAM.
  It answers a fixed JSON form: does the setting match, is every character visible, extra people, artefacts,
  how well the frames match the prompt.

Not checked: spoken words (no speech recognition is installed, and FilmLab installs no models). A failed
vision call leaves the technical part standing and says so.
"""

import asyncio
import base64
import json
import logging
import re
import shutil
from pathlib import Path

import httpx

from .db import Database, now
from .events import EventBus
from .media import _run

log = logging.getLogger("filmlab.inspect")

DEFAULTS = {"auto_inspect": True, "vision_model": "gemma4:31b-cloud", "ollama_url": "http://127.0.0.1:11434"}
FRACTIONS = (0.1, 0.37, 0.63, 0.9)
VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "setting_matches": {"type": "boolean"},
        "setting_seen": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "visible": {"type": "boolean"}}, "required": ["name", "visible"]}},
        "unexpected_people": {"type": "boolean"},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "prompt_match": {"type": "integer", "minimum": 1, "maximum": 5},
        "issues": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["setting_matches", "characters", "artifacts", "prompt_match", "issues", "summary"],
}


class InspectError(ValueError):
    pass


def technical_flags(t: dict, expected_s: float | None, has_audio_model: bool) -> list[str]:
    flags = []
    d = t.get("duration_s") or 0
    # Models snap lengths to their own frame grid (MiniMax gives 5.27 s for 5 s), so only a clearly wrong
    # length counts: much shorter than asked (cut off) or far longer.
    if expected_s and d and (d < expected_s * 0.8 - 0.1 or d > expected_s * 1.5 + 0.5):
        flags.append(f"length {d:.2f} s instead of about {expected_s:.2f} s")
    if d and t.get("black_s", 0) > 0.5 * d:
        flags.append(f"black picture for {t['black_s']:.1f} of {d:.1f} s")
    if d and t.get("frozen_s", 0) > 0.5 * d:
        flags.append(f"frozen picture for {t['frozen_s']:.1f} of {d:.1f} s")
    if has_audio_model:
        if not t.get("has_audio"):
            flags.append("no sound track")
        elif (t.get("mean_db") or 0) < -60:
            flags.append("the sound is silent")
        elif (t.get("max_db") or -99) >= -0.05:
            flags.append("the sound clips (peaks at 0 dBFS)")
    return flags


def vision_flags(v: dict, expect_setting: bool) -> list[str]:
    flags = []
    if expect_setting and v.get("setting_matches") is False:
        flags.append(f"setting doesn't match (looks like: {v.get('setting_seen') or 'something else'})")
    missing = [c.get("name") for c in v.get("characters") or [] if c.get("visible") is False]
    if missing:
        flags.append("not visible: " + ", ".join(str(m) for m in missing))
    if v.get("prompt_match") is not None and v["prompt_match"] <= 2:
        flags.append(f"matches the prompt poorly ({v.get('prompt_match')}/5)")
    if len(v.get("artifacts") or []) >= 2:
        flags.append("visible defects: " + "; ".join(v["artifacts"][:3]))
    return flags


def vision_text(shot: dict, setting: str, mood: str, refs: list[dict]) -> str:
    chars = []
    for r in {x["asset_id"]: x for x in refs}.values():
        if r["kind"] == "character":
            desc = " ".join(str(r.get("asset_description") or "").split())[:120]
            chars.append(f"{r['asset_name']}{' (' + desc + ')' if desc else ''}")
    prompt = re.sub(r"<\s*picture\s+\d+\s*>\s*", "", shot["prompt"], flags=re.I)
    lines = [
        "You are checking one generated video clip for a short film. Four frames from the clip are attached, in order.",
        f"The shot was meant to show: “{prompt[:1500]}”",
        f"Scene setting: {setting or 'not given'}{'; mood: ' + mood if mood else ''}.",
        f"Characters that should be visible: {', '.join(chars) if chars else 'none named'}.",
        "Judge only what the frames show. Answer with JSON: setting_matches (place, time of day and weather match the "
        "setting; true if no setting is given), setting_seen (a few words), characters (each named character and "
        "whether it is visible in at least one frame), unexpected_people (people the shot doesn't call for), artifacts "
        "(visible defects such as distorted faces or hands, melting objects, garbled text; empty if none), prompt_match "
        "(1 = unrelated, 5 = exactly as described), issues (short phrases: what differs from the intent), summary "
        "(one sentence).",
    ]
    return "\n".join(lines)


_NONE = {"", "none", "no", "n/a", "na", "nothing", "null", "-"}


def _flag(v):
    if isinstance(v, bool) or v is None:
        return v
    t = str(v).strip().lower()
    return True if t in ("true", "yes", "y", "1") else False if t in ("false", "no", "n", "0", "none") else None


def _phrases(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [] if v.strip().lower().rstrip(".") in _NONE else [v.strip()]
    if isinstance(v, dict):
        v = [f"{k}: {x}" if not isinstance(x, bool) else str(k) for k, x in v.items() if x]
    return [str(x).strip() for x in v if str(x).strip() and str(x).strip().lower().rstrip(".") not in _NONE][:8]


def normalise_answer(a: dict) -> dict:
    """Cloud models ignore the schema's types now and then ("none" for an empty list, a dict or plain names for
    the characters); bring the answer into the documented shape so the checks read it the same way."""
    chars = a.get("characters")
    out_chars = []
    if isinstance(chars, dict):
        chars = [{"name": k, "visible": v} for k, v in chars.items()]
    if isinstance(chars, str):
        chars = [] if chars.strip().lower() in _NONE else [x.strip() for x in chars.split(",")]
    for c in chars or []:
        if isinstance(c, dict) and c.get("name"):
            out_chars.append({"name": str(c["name"]), "visible": _flag(c.get("visible", True)) is not False})
        elif isinstance(c, str) and c.strip():
            out_chars.append({"name": c.strip(), "visible": True})
    try:
        match = max(1, min(5, int(float(a.get("prompt_match")))))
    except (TypeError, ValueError):
        match = None
    return {"setting_matches": _flag(a.get("setting_matches")), "setting_seen": str(a.get("setting_seen") or "")[:200],
            "characters": out_chars, "unexpected_people": bool(_flag(a.get("unexpected_people"))),
            "artifacts": _phrases(a.get("artifacts")), "prompt_match": match, "issues": _phrases(a.get("issues")),
            "summary": str(a.get("summary") or "")[:500]}


def parse_answer(content: str) -> dict:
    """The model's JSON, also when a cloud model ignores the format and wraps it in a ```json fence."""
    text = (content or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the vision model didn't answer with JSON") from None
        return json.loads(text[start:end + 1])


class Inspector:
    def __init__(self, db: Database, bus: EventBus, media_dir: Path, transport: httpx.AsyncBaseTransport | None = None):
        self.db, self.bus, self.media_dir = db, bus, media_dir
        self._transport = transport
        self._tasks: dict[str, asyncio.Task] = {}
        self._vision = asyncio.Semaphore(1)
        self.library = None  # set by Studio
        self.listeners: list = []  # async (take_id, inspection) -> None

    async def settings(self) -> dict:
        return DEFAULTS | (await self.db.kv_get("autonomy.settings") or {})

    async def set_settings(self, patch: dict) -> dict:
        cur = await self.settings()
        if "auto_inspect" in patch:
            cur["auto_inspect"] = bool(patch["auto_inspect"])
        if patch.get("vision_model") is not None:
            m = str(patch["vision_model"]).strip()
            if not re.fullmatch(r"[\w.:/-]{1,100}", m):
                raise InspectError("vision_model must be an Ollama model name")
            cur["vision_model"] = m
        await self.db.kv_set("autonomy.settings", {k: cur[k] for k in ("auto_inspect", "vision_model")})
        return cur

    async def get(self, take_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM take_inspections WHERE take_id = ?", (take_id,))
        if row:
            for k in ("technical", "vision"):
                row[k] = json.loads(row[k]) if row[k] else None
        return row

    def ensure(self, take_id: str, again: bool = False) -> asyncio.Task:
        t = self._tasks.get(take_id)
        if t and not t.done():
            if not again:
                return t

            async def after(prev=t):  # "inspect again" while one is finishing: run once more after it
                await asyncio.gather(prev, return_exceptions=True)
                await self._inspect(take_id, True)
            self._tasks[take_id] = asyncio.create_task(after())
            return self._tasks[take_id]
        self._tasks[take_id] = asyncio.create_task(self._inspect(take_id, again))
        return self._tasks[take_id]

    async def wait(self, take_id: str, timeout: float = 180) -> dict | None:
        row = await self.get(take_id)
        if row and row["status"] != "running":
            return row
        try:
            await asyncio.wait_for(asyncio.shield(self.ensure(take_id)), timeout)
        except asyncio.TimeoutError:
            pass
        return await self.get(take_id)

    async def take_done(self, take_id: str):
        if (await self.settings())["auto_inspect"]:
            self.ensure(take_id)

    async def _inspect(self, take_id: str, again: bool):
        existing = await self.get(take_id)
        if existing and existing["status"] == "done" and not again:
            return
        take = await self.db.fetchone(
            "SELECT t.id, t.shot_id, t.provenance, m.rel_path, m.duration_s, m.has_audio, s.project_id, s.scene_id, "
            "s.prompt, s.frames, s.title FROM takes t JOIN media m ON m.id = t.media_id JOIN shots s ON s.id = t.shot_id "
            "WHERE t.id = ?", (take_id,))
        if not take:
            raise InspectError("no such take")
        await self.db.execute("INSERT OR REPLACE INTO take_inspections (take_id, status, created_at) VALUES (?, 'running', ?)",
                              (take_id, now()))
        await self._changed(take)
        prov = json.loads(take["provenance"] or "{}")
        # What the take was asked for: its own provenance, not the shot as it is now.
        shot = {"prompt": (prov.get("requested") or {}).get("prompt") or take["prompt"],
                "frames": (prov.get("requested") or {}).get("video_length") or take["frames"]}
        scene = take["scene_id"] and await self.db.fetchone("SELECT time_place, mood FROM scenes WHERE id = ?",
                                                            (take["scene_id"],))
        setting, mood = (scene["time_place"] or "", scene["mood"] or "") if scene else ("", "")
        refs = prov.get("references") or []
        for r in refs:  # descriptions help the model recognise who is who
            if self.library and r.get("asset_id") and "asset_description" not in r:
                a = await self.db.fetchone("SELECT description FROM assets WHERE id = ?", (r["asset_id"],))
                r["asset_description"] = a and a["description"]
        src = self.media_dir / take["rel_path"]
        work = self.media_dir / "cache" / "inspect" / take_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        flags, vision, model, error = [], None, None, None
        try:
            tech = await self._technical(src)
            tech["has_audio"] = bool(take["has_audio"])
            audio_model = "2va" in str(prov.get("model_type") or "") or "ltx2" in str(prov.get("model_type") or "")
            flags += technical_flags(tech, (shot["frames"] - 1) / 24 if shot["frames"] else None, audio_model)
            frames = await self._keyframes(src, tech.get("duration_s") or take["duration_s"] or 1, work)
            sheet = await self._sheet(frames, work)
            cfg = await self.settings()
            model = cfg["vision_model"]
            try:
                async with self._vision:
                    vision = await self._ask_vision(cfg, vision_text(shot, setting, mood, refs), frames)
                flags += vision_flags(vision, bool(setting))
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                vision = {"error": f"the vision check didn't run ({type(exc).__name__}: {str(exc)[:200]})"}
                log.warning("vision check for take %s failed: %s", take_id, exc)
            tech["flags"] = flags
            verdict = "flagged" if flags else "ok"
            summary = ("; ".join(flags) if flags else
                       (vision or {}).get("summary") or "no technical problems found")
            await self.db.execute(
                "UPDATE take_inspections SET status = 'done', verdict = ?, technical = ?, vision = ?, summary = ?, "
                "model = ?, sheet_rel = ?, finished_at = ? WHERE take_id = ?",
                (verdict, json.dumps(tech), json.dumps(vision), summary[:1000], model,
                 str(sheet.relative_to(self.media_dir)), now(), take_id))
        except Exception as exc:  # noqa: BLE001 - recorded on the inspection and shown in the cockpit
            error = str(exc)[-400:]
            log.warning("inspection of take %s failed: %s", take_id, exc)
            await self.db.execute("UPDATE take_inspections SET status = 'failed', error = ?, finished_at = ? "
                                  "WHERE take_id = ?", (error, now(), take_id))
        finally:
            for f in work.glob("frame_*.jpg"):
                f.unlink(missing_ok=True)
        await self._changed(take)
        result = await self.get(take_id)
        for fn in self.listeners:
            try:
                await fn(take_id, result)
            except Exception:  # noqa: BLE001
                log.exception("inspection listener failed for %s", take_id)

    async def _technical(self, src: Path) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-v", "info", "-nostats", "-i", str(src),
            "-vf", "blackdetect=d=0.1:pix_th=0.05,freezedetect=n=0.001:d=0.5", "-af", "volumedetect",
            "-f", "null", "-", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        text = err.decode(errors="replace")
        if proc.returncode != 0:  # no audio stream: run again without the audio filter
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-v", "info", "-nostats", "-i", str(src),
                "-vf", "blackdetect=d=0.1:pix_th=0.05,freezedetect=n=0.001:d=0.5", "-an", "-f", "null", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            text = err.decode(errors="replace")
        dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
        num = lambda pat: [float(x) for x in re.findall(pat, text)]
        mean, peak = num(r"mean_volume: (-?[\d.]+) dB"), num(r"max_volume: (-?[\d.]+) dB")
        return {"duration_s": (int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])) if dur else None,
                "black_s": round(sum(num(r"black_duration:([\d.]+)")), 2),
                "frozen_s": round(sum(num(r"freeze_duration: ([\d.]+)")), 2),
                "mean_db": mean[-1] if mean else None, "max_db": peak[-1] if peak else None}

    async def _keyframes(self, src: Path, duration: float, work: Path) -> list[Path]:
        out = []
        for i, f in enumerate(FRACTIONS):
            dest = work / f"frame_{i}.jpg"
            await _run("ffmpeg", "-y", "-v", "error", "-ss", f"{duration * f:.3f}", "-i", str(src), "-frames:v", "1",
                       "-vf", "scale=640:-2", "-q:v", "4", str(dest))
            out.append(dest)
        return out

    async def _sheet(self, frames: list[Path], work: Path) -> Path:
        dest = work / "sheet.jpg"
        args = ["ffmpeg", "-y", "-v", "error"]
        for f in frames:
            args += ["-i", str(f)]
        await _run(*args, "-filter_complex", "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,scale=960:-2",
                   "-q:v", "4", str(dest))
        return dest

    async def _ask_vision(self, cfg: dict, text: str, frames: list[Path]) -> dict:
        images = [base64.b64encode(f.read_bytes()).decode() for f in frames]
        async with httpx.AsyncClient(base_url=cfg["ollama_url"], timeout=180, transport=self._transport) as c:
            r = await c.post("/api/chat", json={
                "model": cfg["vision_model"], "stream": False, "format": VISION_SCHEMA, "options": {"temperature": 0},
                "messages": [{"role": "user", "content": text, "images": images}]})
            r.raise_for_status()
            answer = parse_answer(r.json()["message"]["content"])
        if not isinstance(answer, dict):
            raise ValueError("the vision model didn't answer with an object")
        return normalise_answer(answer)

    async def _changed(self, take: dict):
        await self.bus.publish("take.inspected", {"take_id": take["id"], "shot_id": take["shot_id"]}, take["project_id"])

    async def shutdown(self):
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
