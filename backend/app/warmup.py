"""Model warm-up for a new install: make WanGP download the models FilmLab uses by default.

WanGP has no "download this model" command: it fetches a model's files the first time a generation uses it. So the
warm-up asks WanGP which of FilmLab's default templates are fully installed (wangp_validate's availability) and, with
the owner's consent, runs one tiny generation per missing template (a 17-frame video, a small image, a short voice,
a few seconds of music). That downloads the files and proves each model really runs on this machine.

    python -m app.cli warmup          # list what is missing (nothing is downloaded)
    python -m app.cli warmup --yes    # download the missing ones (can be tens of GB; takes a while)
    python -m app.cli warmup --yes --all   # run every default model once, even installed ones
"""

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path

from .capabilities import DEFAULTS
from .config import Settings
from .db import Database, new_id
from .media import sha256_file
from .wangp.client import WanGPClient, WanGPError

# setting key -> (task, what it is for, small settings for a quick run)
PURPOSE = {
    "video_refs": ("gen_video_with_refs", "video shots with references", {"video_length": 17, "resolution": "832x480"}),
    "video_plain": ("gen_video", "video shots without references", {"video_length": 17, "resolution": "832x480"}),
    "image_text": ("gen_image", "reference images from text", {"resolution": "512x512"}),
    "image_refs": ("edit_image", "reference images from other images", {"resolution": "512x512"}),
    "voice_design": ("gen_speech_from_description", "character voices (Creator)", {"duration_seconds": 3,
                                                                                   "alt_prompt": "A calm adult voice."}),
    "music": ("gen_song", "music (Creator)", {"duration_seconds": 5}),
}
PROMPTS = {"gen_speech_from_description": "Hello, this is a test.", "gen_song": "[Instrumental]"}
POLL_S, JOB_TIMEOUT_S = 5.0, 3 * 3600


def _probe_image(folder: Path) -> Path:
    p = folder / "warmup-ref.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=512x512", "-frames:v", "1", str(p)],
                   check=True)
    return p


async def _settings(db: Database) -> dict:
    return DEFAULTS | (await db.kv_get("settings.templates", {}) or {})


async def plan(client: WanGPClient, db: Database, probe_id: str) -> list[dict]:
    """Each default template: its purpose, and whether WanGP has all of its files."""
    chosen = await _settings(db)
    rows = []
    for key, (task, purpose, _small) in PURPOSE.items():
        template = chosen.get(key)
        row = {"key": key, "task": task, "purpose": purpose, "template": template, "installed": None, "error": None}
        try:
            full = await client.call("wangp_templates", {"task": task, "template": template})
            settings = dict(full.get("settings") or {}, prompt=PROMPTS.get(task, "FilmLab warm-up"))
            if task == "gen_video_with_refs" or task == "edit_image":
                settings["image_refs"] = [probe_id]  # reference templates validate only with a reference
            res = await client.call("wangp_validate", {"settings": settings})
            t = (res.get("tasks") or [{}])[0]
            row["installed"] = (t.get("availability") or {}).get("available")
        except WanGPError as exc:
            row["error"] = str(exc)[:200]
        rows.append(row)
    return rows


async def run(client: WanGPClient, row: dict, probe_id: str | None, say) -> bool:
    task, _purpose, small = PURPOSE[row["key"]]
    full = await client.call("wangp_templates", {"task": task, "template": row["template"]})
    settings = dict(full.get("settings") or {}) | small | {"prompt": PROMPTS.get(task, "A small test scene, soft light."),
                                                           "seed": 1}
    if task in ("gen_video_with_refs", "edit_image"):
        settings["image_refs"] = [probe_id]
    res = await client.call("wangp_generate", {"settings": settings, "wait": False,
                                               "request_id": f"filmlab-warmup-{new_id()}"})
    job, started, last = res["job_id"], time.monotonic(), ""
    while time.monotonic() - started < JOB_TIMEOUT_S:
        st = await client.session("get_job", job_id=job)
        if st.get("done"):
            ok = st.get("status") == "done"
            say(f"    {'done' if ok else 'failed: ' + str(st.get('result') or st.get('status'))[:200]}"
                f" after {round(time.monotonic() - started)} s")
            return ok
        p = st.get("progress") or {}
        now = " ".join(str(x) for x in (p.get("phase"), p.get("current_step") and f"{p['current_step']}/{p.get('total_steps')}") if x)
        if now and now != last:
            say(f"    {now}")
            last = now
        await asyncio.sleep(POLL_S)
    say("    still running after 3 hours; leaving it to WanGP")
    return False


async def warmup(yes: bool, every: bool, say=print, client: WanGPClient | None = None, db: Database | None = None) -> int:
    s = Settings()
    db = db or Database(s.db_path)
    client = client or WanGPClient(s.wangp_url)
    try:
        try:
            ws = await client.ensure_workspace(s.wangp_workspace, await db.kv_get("wangp.agent_context"))
        except Exception as exc:  # noqa: BLE001 - WanGP not running / MCP off: say so plainly
            say(f"WanGP's MCP server doesn't answer at {s.wangp_url} ({type(exc).__name__}). Start WanGP with MCP "
                "enabled, then run this again.")
            return 2
        if ws["agent_context"] != await db.kv_get("wangp.agent_context"):
            await db.kv_set("wangp.agent_context", ws["agent_context"])
        with tempfile.TemporaryDirectory() as tmp:
            img = _probe_image(Path(tmp))
            probe_id = (await client.upload_gallery(img, "image/png", f"filmlab-warmup-{sha256_file(img)[:12]}.png"))["media_id"]
        rows = await plan(client, db, probe_id)
        say("FilmLab's default models in WanGP:")
        for r in rows:
            state = ("error: " + r["error"]) if r["error"] else "installed" if r["installed"] else \
                "NOT DOWNLOADED" if r["installed"] is False else "unknown"
            say(f"  - {r['purpose']}: {r['template']} ... {state}")
        todo = [r for r in rows if not r["error"] and (every or r["installed"] is False)]
        if not todo:
            say("Nothing to download." if not every else "Nothing to run.")
            return 0
        if not yes:
            say(f"{len(todo)} model(s) would be downloaded by a tiny test generation each (run with --yes). Together "
                "they can be tens of GB and take a long time on a slow connection.")
            return 0
        failed = 0
        for r in todo:
            say(f"  > {r['purpose']}: {r['template']} (WanGP downloads its files first)")
            try:
                if not await run(client, r, probe_id, say):
                    failed += 1
            except WanGPError as exc:
                say(f"    WanGP refused it: {str(exc)[:300]}")
                failed += 1
        say("Warm-up finished." if not failed else f"Warm-up finished; {failed} model(s) failed (see above).")
        return 1 if failed else 0
    finally:
        await client.aclose()
        db.close()

