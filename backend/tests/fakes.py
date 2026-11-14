"""HTTP-level fakes for WanGP MCP and the OpenClaw Gateway.

They speak the same wire formats the real services returned in the F0/F1b
spikes, so tests exercise the real clients, parsers and state machines.
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path

import httpx


def make_test_image(path: Path, size="96x64", source="testsrc2") -> bytes:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{source}=size={size}", "-frames:v", "1",
                    str(path)], check=True)
    return path.read_bytes()


# Template catalogue and validation behaviour as probed on a real WanGP install.
CATALOGUE = {
    "gen_video": [
        ("LTX-2 2.3 Distilled 1.0", "ltx2_22B_distilled", ["start", "end", "reference", "single_reference"], True),
        ("Enhanced Lightning v2", "i2v_2_2_Enhanced_Lightning_v2", ["start", "end"], False),
        ("MiniMax H3 FL2VA Pruned PDD 8 Steps", "minimax_h3_fl2va_pruned_pdd", ["start", "end", "injected_frames"], False),
    ],
    "gen_video_with_refs": [
        ("MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps", "minimax_h3_ref2va_pruned",
         ["start", "end", "reference", "multiple_references"], True),
        ("MiniMax H3 Ref2VA Pruned PDD 8 Steps", "minimax_h3_ref2va_pruned_pdd",
         ["start", "end", "reference", "multiple_references"], False),
    ],
    "gen_image": [("Z Image Turbo", "z_image", [], True)],
    "edit_image": [("Flux Klein 9B", "flux2_klein_9b", ["reference", "multiple_references", "background"], True)],
    "gen_speech_from_description": [("Qwen3 1.7B", "qwen3_tts_voicedesign", [], True)],
    "gen_song": [("ACE-Step 1.5 Turbo LM 1.7B", "ace_step_v1_5_turbo_lm_1_7b", [], True)],
    "gen_speech_from_sample": [("Index TTS 2", "index_tts2", [], True)],
    # video driven by an audio track (a music video's song slices): the same H3 model "With Sound", and Infinitalk
    "gen_video_with_speech": [
        ("Infinitalk", "infinitetalk", ["start", "reference", "multiple_references", "background"], True),
        ("MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps With Sound", "minimax_h3_ref2va_pruned",
         ["start", "end", "reference", "multiple_references"], False),
        ("MiniMax H3 FL2VA Pruned PDD 8 Steps With Sound", "minimax_h3_fl2va_pruned_pdd", ["start", "end"], False),
    ],
}
AUDIO_MODELS = {"qwen3_tts_voicedesign", "ace_step_v1_5_turbo_lm_1_7b", "index_tts2"}
_AUDIO: list[bytes] = []


def fake_audio() -> bytes:
    """A short WAV standing in for a generated voice or music take (made once)."""
    if not _AUDIO:
        _AUDIO.append(subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=6",
                                      "-ar", "32000", "-f", "wav", "-"], capture_output=True, check=True).stdout)
    return _AUDIO[0]
MISSING_LORA = {"MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps"}
NOT_DOWNLOADED = {"MiniMax H3 FL2VA Pruned PDD 8 Steps"}
REF_ONLY = {"minimax_h3_ref2va_pruned", "minimax_h3_ref2va_pruned_pdd"}
TEMPLATE_OF: dict = {}
for _task, _rows in CATALOGUE.items():  # a model type's first template (H3 "With Sound" shares its model type)
    for _name, _mt, _, _ in _rows:
        TEMPLATE_OF.setdefault(_mt, (_task, _name))


def make_test_video(path: Path) -> bytes:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x176:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1.04", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path.read_bytes()


class FakeWanGP:
    """Scenarios: 'normal' (runs then done), 'restart' (interrupted at shutdown,
    unreachable, empty queue, then restored in queue and output in gallery),
    'restart_hold' (as restart, but the restored task keeps running, next to a UI task),
    'lost' (interrupted, never restored), 'slow' (stays running), 'contended' (a WanGP UI
    task runs first while ours waits in the queue), 'stall' (progress never moves),
    'oom' (WanGP fails the job with a CUDA out-of-memory error)."""

    def __init__(self, video: bytes, scenario: str = "normal", image: bytes | None = None):
        self.video = video
        self.image = image or b""
        self.uploads: list[dict] = []      # gallery uploads received (filename, bytes, media_id)
        self.known_media: set[str] = set()  # gallery ids that exist in FilmLab's workspace
        self.scenario = scenario
        self.calls: list[tuple[str, dict]] = []
        self.jobs: dict[str, dict] = {}
        self.by_request: dict[str, str] = {}
        self.workspaces: list[dict] = []
        self.gallery: list[dict] = []
        self.queue: list[dict] = []
        self.pending_restore: list[dict] = []
        self.down_polls = self.empty_polls = 0
        self.download_polls = 0  # this many polls of the next job say WanGP is downloading model files
        self.cancelled_queue_ids: list[str] = []
        self.ui_polls = 3 if scenario == "contended" else 0
        if scenario in ("contended", "restart_hold"):
            self.queue.append({"queue_id": "ui1", "position": 1, "task_id": 1, "status": "running",
                               "client_id": None, "mcp_agent_id": None, "prompt": "owner's own WanGP job"})
        self._n = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/dl/"):
            if request.url.path.startswith("/dl/image"):
                return httpx.Response(200, content=self.image, headers={"content-type": "image/png"})
            if request.url.path.startswith("/dl/audio"):
                return httpx.Response(200, content=fake_audio(), headers={"content-type": "audio/wav"})
            return httpx.Response(200, content=self.video, headers={"content-type": "video/mp4"})
        if request.method == "PUT" and request.url.path.startswith("/upload/"):
            media_id = f"visual:up{len(self.uploads) + 1}"
            self.uploads.append({"token": request.url.path, "bytes": len(request.content), "media_id": media_id,
                                 "content_type": request.headers.get("content-type")})
            self.known_media.add(media_id)
            return httpx.Response(200, json={"status": "done", "media_id": media_id, "media_type": "image",
                                             "selected": False, "in_gallery": True})
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return self._reply(msg, {"protocolVersion": "2025-06-18", "serverInfo": {"name": "WanGP", "version": "fake"}})
        if msg["method"] == "tools/call":
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            self.calls.append((name, args))
            if name in ("wangp_templates", "wangp_models", "wangp_model") and "agent_context" in args:
                # Mirrors the real server: catalogue tools have no agent_context argument.
                return self._reply(msg, {"isError": True, "content": [{"type": "text", "text":
                    f"Invalid arguments for {name}: Unknown argument agent_context. Nothing was executed."}]})
            try:
                result = getattr(self, name)(args)
            except _ToolError as exc:
                return self._reply(msg, {"isError": True, "content": [{"type": "text", "text": str(exc)}]})
            return self._reply(msg, {"content": [{"type": "text", "text": json.dumps(result)}]})
        return self._reply(msg, None, error={"code": -32601, "message": "unknown method"})

    @staticmethod
    def _reply(msg, result, error=None):
        body = {"jsonrpc": "2.0", "id": msg["id"]}
        body.update({"error": error} if error else {"result": result})
        # The real server answers as an SSE stream; exercise that path.
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(body)}\n\n",
                              headers={"content-type": "text/event-stream"})

    # --- tools -----------------------------------------------------------

    def wangp_workspace(self, a):
        if a["action"] == "list":
            return {"workspaces": self.workspaces}
        if a["action"] == "create":
            ws = {"id": "ws1", "name": a["name"], "mcp_agent_id": "ctx1"}
            self.workspaces.append(ws)
        return {"agent_context": "ctx1", "workspace_id": "ws1", "name": "FilmLab", "folders": {}}

    def wangp_templates(self, a):
        rows = CATALOGUE[a.get("task", "gen_video")]
        if a.get("template"):
            name, mt, _, _ = next(r for r in rows if r[0] == a["template"])
            settings = {"prompt": "template sample", "model_type": mt, "num_inference_steps": 8}
            if a["task"].startswith("gen_video"):
                settings |= {"video_length": 241, "resolution": "832x480" if "MiniMax" in name else "1280x720"}
            else:
                settings |= {"resolution": "1024x1024"}
            if mt in REF_ONLY:
                settings["video_prompt_type"] = "I"
            if a["task"] == "gen_video_with_speech":
                settings["audio_prompt_type"] = "AS" if mt.startswith("minimax_h3") else "A"
            if mt == "minimax_h3_ref2va_pruned":  # like WanGP: the Turbo template declares it, PDD/PTD don't
                settings["custom_settings"] = {"audio_refinement": "none"}
            return {"settings": settings}
        return {"default_template": rows[0][0], "templates": [
            {"template": name, "default": default, "model_type": mt,
             "media_inputs": {"image": img, **({"audio": ["prompt"]} if a.get("task", "").startswith("gen_video") else {})}}
            for name, mt, img, default in rows]}

    def wangp_model(self, a):
        """A model's defaults, definition and LoRAs, shaped like the real server's (probed 2026-11): the distilled
        H3 PDD and LTX models lock their steps; the H3 Turbo model doesn't and has several samplers."""
        mt, action = a["model_type"], a.get("action")
        h3, pdd, ltx = mt.startswith("minimax_h3"), mt.endswith("_pdd"), mt.startswith("ltx2")
        if action == "loras":
            return {"status": "done", "loras": ["grain_v1.safetensors", "style/noir.safetensors"] if h3 else [],
                    "has_more": False, "next_cursor": None}
        if action == "defaults":
            d = {"num_inference_steps": 8 if pdd or ltx else 20, "force_fps": "", "repeat_generation": 1,
                 "spatial_upsampling": "", "temporal_upsampling": "", "activated_loras": [], "loras_multipliers": "",
                 "sliding_window_size": 362 if h3 else 129, "sliding_window_overlap": 18 if h3 else 5,
                 "model_type": mt}
            if h3:
                d |= {"sample_solver": "euler", "flow_shift": 12.0, "skip_steps_cache_type": ""}
            else:
                d |= {"negative_prompt": "", "NAG_scale": 1}
            if not h3 and not ltx:
                d |= {"guidance_scale": 1, "sample_solver": "unipc", "flow_shift": 5}
            return d
        if action == "definition":
            m = {"sliding_window": True, "frames_steps": 17 if h3 else 4,
                 "sliding_window_defaults": {"window_min": 124, "window_max": 481, "window_step": 17, "overlap_min": 1,
                                             "overlap_max": 120, "overlap_step": 17} if h3 else {}}
            if h3:
                m |= {"lock_inference_steps": pdd, "flow_shift": True, "no_negative_prompt": True,
                      "first_block_cache": True, "spectrum_cache": True, "guidance_max_phases": 1, "visible_phases": 0,
                      "skip_steps_multiplier_choices": [["Low (0.06)", 0.06], ["Balanced (0.08)", 0.08]],
                      "sample_solvers": [["Euler", "euler"]] if pdd else [["Euler", "euler"], ["RES Multistep", "res_multistep"]]}
            elif ltx:
                m |= {"lock_inference_steps": True, "NAG": True, "guidance_max_phases": 2, "visible_phases": 0}
            else:
                m |= {"NAG": True, "flow_shift": True, "guidance_max_phases": 3, "mag_cache": True,
                      "sample_solvers": [["unipc", "unipc"], ["euler", "euler"]]}
            return m
        raise _ToolError(f"wangp_model: unknown action {action}")

    def wangp_validate(self, a):
        s = a["settings"]
        if s.get("sliding_window_overlap", 0) >= s.get("sliding_window_size", 10 ** 6):
            return {"valid": False, "diagnostics": [{"severity": "error", "message":
                    "sliding_window_overlap must be smaller than sliding_window_size"}], "tasks": [{}]}
        _, name = TEMPLATE_OF.get(s.get("model_type"), ("gen_video", "LTX-2 2.3 Distilled 1.0"))
        diags, valid = [], True
        undeclared = set(s.get("custom_settings") or {}) - ({"audio_refinement"} if s.get("model_type") == "minimax_h3_ref2va_pruned" else set())
        if undeclared:
            valid = False
            diags.append({"severity": "error", "message": f"settings.custom_settings.{sorted(undeclared)[0]} is not a custom setting"})
        if s.get("model_type") in REF_ONLY and not s.get("image_refs"):
            valid = False
            diags.append({"severity": "error", "message": "The selected model/input mode requires image_refs."})
        if name in MISSING_LORA:
            valid = False
            diags.append({"severity": "error", "message": "LoRAs not found in this model's catalog: ['turbo_v1']"})
        available = name not in NOT_DOWNLOADED
        if not available:
            diags.append({"severity": "warning",
                          "message": "Model checkpoint availability is partial or missing; execution may require downloads."})
        return {"valid": valid, "diagnostics": diags, "queue": {"total_count": len(self.queue)},
                "tasks": [{"sizing": {"canvas_dimensions": {"width": 1280, "height": 704}},
                           "requested_video": {"frames": s.get("video_length"), "fps": 24.0},
                           "window_plan": {"windows": [{}]}, "availability": {"available": available}}]}

    def wangp_generate(self, a):
        batch = a["settings"] if isinstance(a["settings"], list) else [a["settings"]]
        for item in batch:
            for ref in item.get("image_refs") or []:
                if ref not in self.known_media:
                    raise _ToolError(f"Error executing tool wangp_generate: 'Unknown WanGP media_id: {ref}'")
        rid = a["request_id"]
        if rid in self.by_request:
            job = self.jobs[self.by_request[rid]]
            return {"job_id": job["id"], "reused": True}
        self._n += 1
        job_id = f"job{self._n}"
        self.jobs[job_id] = {"id": job_id, "request_id": rid, "polls": 0, "cancelled": False,
                             "client_id": f"mcp_{job_id}_1", "settings": a["settings"], "count": len(batch),
                             "image": batch[0].get("model_type") in ("z_image", "flux2_klein_9b"),
                             "audio": batch[0].get("model_type") in AUDIO_MODELS}
        self.by_request[rid] = job_id
        if self.scenario in ("contended", "deaf_cancel"):
            self.queue.append({"queue_id": f"q-{job_id}", "position": len(self.queue) + 1, "status": "queued",
                               "client_id": f"mcp_{job_id}_1", "mcp_agent_id": "ctx1"})
        return {"job_id": job_id, "reused": False, "canvas": {"requested": "1280x720", "generated": "1280x704"}}

    def wangp_session(self, a):
        action, args = a["action"], a.get("arguments") or {}
        if action == "list_queue":
            if self.down_polls:
                self.down_polls -= 1
                raise httpx.ConnectError("WanGP restarting")
            if self.empty_polls:
                self.empty_polls -= 1
                return {"status": "done", "total_count": 0, "tasks": []}
            self.queue.extend(self.pending_restore)
            self.pending_restore.clear()
            tasks = [dict(t) for t in self.queue]
            restored = [t for t in self.queue if t.get("restored")]
            for task in restored:  # a restored task finishes after two queue polls
                task["polls"] = task.get("polls", 0) + 1
            if self.scenario == "restart" and restored and all(t["polls"] > 2 for t in restored):
                self.finish_restored()
            return {"status": "done", "total_count": len(tasks), "tasks": tasks}
        if action == "cancel_queue_task":
            self.cancelled_queue_ids.append(args["queue_id"])
            gone = [t for t in self.queue if t["queue_id"] == args["queue_id"]]
            self.queue = [t for t in self.queue if t["queue_id"] != args["queue_id"]]
            for job in self.jobs.values():  # removing (or aborting) the task ends its job
                if any(t.get("client_id") == job["client_id"] for t in gone):
                    job["cancelled"] = True
            return {"status": "cancelled", "queue_id": args["queue_id"]}
        if action == "cancel_job":
            if self.scenario == "deaf_cancel":  # live: a task still waiting in WanGP's queue starts anyway
                return {"job_id": args["job_id"], "done": False, "cancel_requested": True}
            self.jobs[args["job_id"]]["cancelled"] = True
            return {"job_id": args["job_id"], "done": False, "cancel_requested": True}
        if action == "create_file_download":
            import hashlib
            if args["source"].startswith("audio:"):
                return {"download_url": f"/dl/{args['source'].replace(':', '-')}",
                        "sha256": hashlib.sha256(fake_audio()).hexdigest(), "filename": "out.wav"}
            if args["source"].startswith("image:"):
                return {"download_url": f"/dl/{args['source'].replace(':', '-')}",
                        "sha256": hashlib.sha256(self.image).hexdigest(), "filename": "out.png"}
            return {"download_url": "/dl/tok1", "sha256": hashlib.sha256(self.video).hexdigest(),
                    "filename": "out.mp4"}
        if action == "create_gallery_upload":
            return {"token": f"tok{len(self.uploads) + 1}", "upload_url": f"/upload/tok{len(self.uploads) + 1}",
                    "filename": args["filename"], "method": "PUT"}
        if action == "get_job":
            return self._get_job(self.jobs[args["job_id"]])
        raise AssertionError(f"unexpected session action {action}")

    def _get_job(self, job):
        job["polls"] += 1
        # Like the real server, get_job does not report client_ids; only gallery items carry task_id.
        base = {"job_id": job["id"]}
        if job["cancelled"]:
            return base | {"status": "interrupted", "done": True}
        if self.scenario == "contended" and self.ui_polls:
            self.ui_polls -= 1  # our task waits behind the owner's UI task
            if not self.ui_polls:
                self.queue = [t for t in self.queue if t["queue_id"] != "ui1"]
                for t in self.queue:
                    t["status"], t["position"] = "running", 1
            return base | {"done": False, "status": "queued", "progress": {}}
        if self.scenario == "stall":
            return base | {"done": False, "progress": {"phase": "inference", "current_step": 2, "total_steps": 8}}
        if self.download_polls:  # WanGP fetching a model's files first: a status line, no steps
            self.download_polls -= 1
            job["polls"] = 0
            return base | {"done": False, "progress": {"phase": "inference", "status": "Downloading model files (2/5)"}}
        if self.scenario == "oom" and job["polls"] >= 2:
            return base | {"status": "error", "done": True, "result": {"success": False, "errors": [
                {"message": "CUDA out of memory. Tried to allocate 2.00 GiB", "stage": "inference"}]}}
        if self.scenario in ("slow", "deaf_cancel") or job["polls"] < 3:
            return base | {"done": False, "progress": {"phase": "inference", "current_step": job["polls"], "total_steps": 8}}
        media_id = f"visual:{job['id']}"
        if self.scenario == "lost":  # interrupted and never restored by WanGP
            return base | {"status": "interrupted", "done": True, "client_ids": [job["client_id"]]}
        if self.scenario in ("restart", "restart_hold"):
            # Real sequence: get_job says interrupted while WanGP shuts down (recovered not
            # yet set), WanGP is unreachable for a while, its queue comes back empty, and
            # only then is the saved task restored and re-run.
            if not job.get("restored"):
                job["restored"] = True
                self.pending_restore.append({"client_id": job["client_id"], "status": "running", "queue_id": "q1",
                                             "position": 2, "mcp_agent_id": "ctx1", "restored": True})
                self.down_polls, self.empty_polls = 2, 2
            return base | {"status": "interrupted", "done": True, "client_ids": [job["client_id"]]}
        if job["image"] or job.get("audio"):
            kind = "audio" if job.get("audio") else "image"
            return base | {"status": "done", "done": True, "result": {"gallery_items": [
                {"media_id": f"{kind}:{job['id']}-{n}", "task_id": f"mcp_{job['id']}_{n}", "role": "final"}
                for n in range(1, job["count"] + 1)]}}
        return base | {"status": "done", "done": True,
                       "result": {"gallery_items": [{"media_id": media_id, "task_id": job["client_id"], "role": "final"}]}}

    def wangp_toolbox(self, a):
        assert a["action"] == "media_settings" and set(a["arguments"]) == {"media_id"}
        assert a.get("agent_context") == "ctx1", "gallery items are only visible in their workspace"
        return {"status": "done", "media_id": a["arguments"]["media_id"], "filename": "take_seed424242.mp4",
                "settings": {"seed": 424242, "model_type": "ltx2_22B_distilled", "num_inference_steps": 8,
                             "type": "WanGP v19.02 by DeepBeepMeep - LTX-2 2.3 Distilled 1.0 22B",
                             "model_filename": "ltx-2.3-22b-distilled.safetensors", "generation_time": 102}}

    def wangp_list_gallery(self, a):
        return {"media": self.gallery}

    def finish_restored(self):
        for task in [t for t in self.queue if t.get("restored")]:
            self.gallery.append({"media_id": f"visual:{task['client_id']}", "task_id": task["client_id"], "role": "final"})
        self.queue = [t for t in self.queue if not t.get("restored")]


class _ToolError(Exception):
    """Raised inside a fake tool to answer with isError, as WanGP does."""


class FakeGateway:
    """Scripted director. 'draft' in the user text -> draft_shot tool call;
    'render' -> request_render on the first shot; otherwise plain text."""

    def __init__(self):
        self.requests: list[dict] = []
        self.shot_id_for_render: str | None = None
        self.lazy = False  # answer plan/enhance briefs with text only, as a real model sometimes does
        self.no_redo = False  # answer redo-run briefs without proposing anything
        self.lazy_once = False  # like lazy, but calls plan_storyboard when FilmLab reminds it
        self.empty = False  # fail like OpenClaw does when the model's reply comes back empty: always, or N times
        self.more_parts = 0  # plan in parts: after the first part, this many more (each tries replace_drafts)
        self.empty_next_part = 0  # this many "Part N is saved" requests come back empty (the live failure)
        self.hang_next_part = False  # "Part N is saved" never answers (a slow model), until the turn is stopped

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    creator_bad_first = False  # the Creator's first draft misses its name and description (it gets the error back)

    def _creator(self, body):
        first = body["messages"][0]
        if first["role"] == "assistant":  # the error came back: fixed draft
            return self._sse(tool=("propose_asset", self.creator_plan(body)))
        plan = self.creator_plan(body)
        if self.creator_bad_first:
            self.creator_bad_first = False
            plan = {k: v for k, v in plan.items() if k not in ("name", "description")}
        return self._sse(tool=("propose_asset", plan))

    def creator_plan(self, body):
        text = next((m["content"] for r in self.requests for m in r["messages"] if m["role"] == "user"
                     and "Create a library" in m["content"]), "")
        if "library sound" in text:
            return {"name": "Lab theme", "description": "A curious, bubbly electronic theme for the lab",
                    "music": {"style": "playful synth, light percussion, 110 bpm", "lyrics": "", "seconds": 20}}
        plan = {"name": "Anthony_science", "description": "A friendly young android: silver face plates, blue eyes",
                "tags": "android robot", "views": [
                    {"label": "front portrait", "prompt": "Front portrait of a friendly silver android, plain grey background"},
                    {"label": "full body", "prompt": "Full body of the same android standing; keep the same face and colours"}]}
        if "design the voice" in text or "voice is cloned" in text:
            plan["voice"] = {"description": "young male android, bright and curious", "line": "(cheerfully) Hello! I am awake."}
        return plan

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer test-token":
            return httpx.Response(401)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "openclaw/director"}]})
        body = json.loads(request.content)
        body["_model_header"] = request.headers.get("x-openclaw-model")
        self.requests.append(body)
        if body["_model_header"] == "ollama/broken:cloud":
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        asks_next = re.match(r"part \d+ is saved", body["messages"][0]["content"].lower())
        if asks_next and self.hang_next_part:
            async def hang():
                await asyncio.sleep(60)
                return self._sse(text="too late")
            return hang()
        if getattr(self, "timeout", False):  # the model never answers (too big for the GPU, on the CPU)
            raise httpx.ReadTimeout("timed out")
        if getattr(self, "empty_gap", False) and "skips this part of my brief" in body["messages"][0]["content"]:
            err = {"error": {"message": "internal error", "type": "api_error"}}
            return httpx.Response(200, text=f"data: {json.dumps(err)}\n\n", headers={"content-type": "text/event-stream"})
        if self.empty or (self.empty_next_part and asks_next):
            if asks_next and self.empty_next_part:
                self.empty_next_part -= 1
            elif type(self.empty) is int:
                self.empty -= 1
            err = {"error": {"message": "internal error", "type": "api_error"}}
            return httpx.Response(200, text=f"data: {json.dumps(err)}\n\n", headers={"content-type": "text/event-stream"})
        first = body["messages"][0]
        if any(t["function"]["name"] == "propose_asset" for t in body["tools"]) and len(body["tools"]) == 1:
            return self._creator(body)
        if first["role"] == "assistant":  # tool follow-up
            if getattr(self, "claim_done_early", False):  # says it's all planned after one part (a small model)
                self.claim_done_early = False
                return self._sse(text="All planned: 21 shots across 3 scenes, about 60 seconds.")
            return self._sse(text="Done. " + body["messages"][-1]["content"][:60])
        if "skips this part of my brief" in first["content"]:  # the brief-coverage check asks for a skipped part
            if getattr(self, "ignore_gap", False):
                return self._sse(text="It is all there already.")
            return self._sse(tool=("plan_storyboard", {"scenes": [{"title": "The shell", "shots": [
                {"title": "Impact", "prompt": "A shell hits the trench at dusk, earth flying", "seconds": 3,
                 "rationale": "the skipped part", "sound": {"lines": []}}]}]}))
        if "isn't all planned yet" in first["content"]:  # FilmLab's challenge: the next part
            return self._sse(tool=("plan_storyboard", {"scenes": [{"title": "Later scene", "shots": [
                {"title": "Later", "prompt": "A later wide shot of the trench at night", "seconds": 3,
                 "rationale": "the next part", "sound": {"lines": []}}]}]}))
        text = first["content"].lower()
        # a later part in a fresh conversation (a local model), when the test asks for that behaviour
        carry_on = getattr(self, "carry_parts", False) and "carry on from it" in text
        if re.match(r"part \d+ is saved", text) or carry_on:
            if carry_on:
                self.fresh_parts = getattr(self, "fresh_parts", 0) + 1
                text = f"part {self.fresh_parts} is saved"
            if not self.more_parts:
                return self._sse(text="All planned: three scenes.")
            self.more_parts -= 1
            n = text.split()[1]
            return self._sse(tool=("plan_storyboard", {"replace_drafts": True, "scenes": [{"title": f"Later scene {n}", "shots": [
                {"title": f"Later {n}", "prompt": f"A later wide shot {n} of the trench at night", "seconds": 3,
                 "rationale": "the next part", "sound": {"lines": []}}]}]}))
        if self.lazy_once and text.startswith("you described") and "plan_storyboard" in text:
            return self._sse(tool=("plan_storyboard", self.plan_for("")))
        if (self.lazy or self.lazy_once) and ("plan the storyboard" in text or "enhance shot" in text):
            return self._sse(text="I have planned and improved everything as asked.")
        if m := re.search(r"\[redo run\] shot \d+ .*?\(shot_id (\w+)\)", text):
            if self.no_redo:
                return self._sse(text="I looked at it and I think it is fine.")
            return self._sse(tool=("propose_redo", {
                "shot_id": m.group(1), "reason": "the setting was lost; restated the rainy night street",
                "prompt": f"Redo {len(self.requests)}: a rainy suburban street at night, fog, no dialogue, no voices"}))
        if "write the story for this film's storybook" in text:
            ids = re.findall(r"^- shot_id (\w+)", text, re.M)
            return self._sse(tool=("propose_story", {"title": "The Long Wait", "subtitle": "A trench tale",
                                                     "summary": "Quiet and plain.", "pages": [
                {"shot_id": sid, "text": f"Page {n + 1}: the fog came in slow and grey over the trench."}
                for n, sid in enumerate(ids)]}))
        if "plan the transitions" in text and getattr(self, "lazy_transitions", False):
            return self._sse(text="Dissolves everywhere would suit it.")
        if "plan the transitions" in text:
            ids = re.findall(r"^\d+\. shot_id (\w+)", text, re.M)
            return self._sse(tool=("propose_transitions", {"summary": "Calm film: soft dissolves, a fade to close.",
                                                           "transitions": [
                {"shot_id": sid, "transition": "dissolve" if n % 2 == 0 else "fade", "seconds": 9 if n == 0 else 0.4,
                 "reason": f"reason {n + 1}"} for n, sid in enumerate(ids)]}))
        if m := re.search(r"enhance shot .*\(shot_id (\w+)\)", text):
            two = "<picture 2> =" in text  # the shot has two references (the enhance message lists them)
            return self._sse(tool=("update_shot", {"shot_id": m.group(1),
                                                   "prompt": "<Picture 1> crouches in <Picture 2> as it burns, night, smoke"
                                                   if two else "A soldier crouches in the trench as it burns, night, smoke",
                                                   "rationale": "named the pictures"}))
        if text.startswith("[filmlab project") and "plan the storyboard" in text:
            return self._sse(tool=("plan_storyboard", self.plan_for(first["content"])))
        if "draft" in text:
            return self._sse(tool=("draft_shot", {"title": "Establishing wide",
                                                  "prompt": "Yellow-green fog over a ruined trench at dusk", "frames": 120}))
        if "render" in text and self.shot_id_for_render:
            return self._sse(tool=("request_render", {"shot_id": self.shot_id_for_render, "note": "ready"}))
        return self._sse(text="I am the FilmLab Director.")

    @staticmethod
    def plan_for(brief: str) -> dict:
        """A small valid plan that names the first library character and place in its lead prompt ({Name}),
        as the brief lists them (“- {Name} (kind): … · views: …”)."""
        lib = brief.split("Library (", 1)[-1].split("Current storyboard:", 1)[0]
        chars = re.findall(r"^- \{([^}]+)\} \(character\)", lib, re.M)
        places = re.findall(r"^- \{([^}]+)\} \(location\)", lib, re.M)
        lead = ({"prompt": "{%s} waits in {%s} as fog rolls in, dusk, low rumble" % (chars[0], places[0])}
                if chars and places else {"prompt": "A soldier waits in a foggy trench at dusk, low rumble"})
        return {"logline": "A soldier waits for the march", "replace_drafts": "replace_drafts: true" in brief.lower(),
                "scenes": [
                    {"title": "The wait", "goal": "Establish the soldier", "mood": "tense", "shots": [
                        {"title": "Hale waits", "idea": "Hale waits in the trench", "seconds": 5, "transition": "cut",
                         "rationale": "introduce him in his place", **lead},
                        {"title": "Fog", "idea": "Fog over the empty trench", "seconds": 3, "transition": "dissolve",
                         "prompt": "Fog drifts over an empty trench at dusk, distant thunder", "rationale": "breathing room"}]},
                    {"title": "The march", "goal": "Release", "shots": [
                        {"title": "March", "idea": "They march away", "seconds": 4, "transition": "fade",
                         "prompt": "Soldiers march away into the fog, wide shot, drums", "rationale": "end on movement"}]}]}

    @staticmethod
    def _sse(text=None, tool=None):
        chunks = [{"choices": [{"delta": {"role": "assistant"}}]}]
        if text:
            for word in text.split(" "):
                chunks.append({"choices": [{"delta": {"content": word + " "}}]})
            chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        if tool:
            name, args = tool
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call1", "type": "function",
                                                                  "function": {"name": name, "arguments": ""}}]}}]})
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)}}]}}]})
            chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})


class FakeVision:
    """Ollama's /api/chat for the take inspector: answers the vision form with `answer` (a dict), or fails
    when `fail` is set. Each request is kept, so tests can see what was asked."""

    OK = {"setting_matches": True, "setting_seen": "as described", "characters": [], "unexpected_people": False,
          "artifacts": [], "prompt_match": 4, "issues": [], "summary": "Matches the shot."}

    TAGS = [("gemma4:31b-cloud", True, "32.7B", ["completion", "tools", "vision"]),
            ("qwen3.5:9b", False, "9.0B", ["completion", "tools"]),
            ("llava:latest", False, "7B", ["completion", "vision"]),
            ("retired:cloud", True, None, [])]

    def __init__(self):
        self.answer = dict(self.OK)
        self.answers: list[dict] = []  # used in order before `answer`
        self.fail = False
        self.requests: list[dict] = []
        self.loaded: list[dict] = []  # what /api/ps reports
        self.unloaded: list[str] = []  # models FilmLab asked to unload (keep_alive 0)
        self.who = {"characters": [], "place": "none"}  # castpass's answer: a dict, or a function of the request
        self.who_asked: list[dict] = []
        self.beats: list[str] | None = None  # coverage's beats of the brief; None: the question isn't answered
        self.shown = lambda question: {"closest_shot": 1, "shows_it": "no", "answer": "no"}
        self.shown_asked: list[dict] = []
        self.sticky = False  # a model that won't unload

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":  # Ollama's loaded models (the GPU-sharing check)
            return httpx.Response(200, json={"models": self.loaded})
        if request.url.path == "/api/generate":  # {"model", "keep_alive": 0}: unload it
            body = json.loads(request.content)
            if body.get("keep_alive") == 0:
                self.unloaded.append(body["model"])
                if not self.sticky:
                    self.loaded = [m for m in self.loaded if m["name"] != body["model"]]
            return httpx.Response(200, json={"model": body.get("model"), "done": True, "done_reason": "unload"})
        if request.url.path == "/api/tags":  # what Settings → Director offers
            return httpx.Response(200, json={"models": [{"name": n, "remote_host": "https://ollama.com" if c else None,
                                                         "details": {"parameter_size": s}} for n, c, s, _ in self.TAGS]})
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            caps = next((caps for n, _, _, caps in self.TAGS if n == name), None)
            return httpx.Response(200, json={"capabilities": caps}) if caps is not None else httpx.Response(404)
        body = json.loads(request.content)
        self.requests.append(body)
        asked = (body.get("format") or {}).get("properties") or {}
        if "beats" in asked and self.beats is not None:  # the brief's beats (coverage.py)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps({"beats": self.beats})}})
        if ("closest_shot" in asked or list(asked) == ["answer"]) and self.beats is not None:
            # which shot is closest to a beat, then whether that shot shows it: a function of the question
            if "closest_shot" in asked:
                self.shown_asked.append(body)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(
                self.shown(body["messages"][0]["content"]))}})
        if "place" in asked:  # the who-and-where question (castpass)
            self.who_asked.append(body)
            answer = self.who(body) if callable(self.who) else self.who
            return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(answer)}})
        if self.fail:
            return httpx.Response(503, json={"error": "model unavailable"})
        answer = self.answers.pop(0) if self.answers else self.answer
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(answer)}})
