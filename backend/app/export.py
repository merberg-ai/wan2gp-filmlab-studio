"""Exports: a frozen manifest, one ffmpeg pass built only from it, numbered immutable deliveries.

The manifest lists every input (with SHA-256), trim, transition, audio and text setting, the output size
and fps, and the exact encoder settings. The film is built from the manifest alone, so "Verify" can
rebuild it later and compare decoded frames and audio samples (framemd5) with the delivery.
Encoding runs on the CPU (libx264): the GPU stays WanGP's. Progress comes from ffmpeg's own -progress.
"""

import asyncio
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path

from .db import Database, new_id, now
from .events import EventBus
from .media import _run, probe, sha256_file
from .shots import TRANSITIONS

log = logging.getLogger("filmlab.export")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
ENCODE = {"video": {"codec": "libx264", "preset": "medium", "crf": 18, "profile": "high", "pix_fmt": "yuv420p",
                    "threads": 8},
          "audio": {"codec": "aac", "bitrate": "192k", "rate": 48000, "channels": 2}}
DRAFT = {"video": {"codec": "libx264", "preset": "veryfast", "crf": 28, "profile": "high", "pix_fmt": "yuv420p",
                   "threads": 8},
         "audio": {"codec": "aac", "bitrate": "128k", "rate": 48000, "channels": 2}}
LOUDNESS = {"I": -14, "TP": -1.5, "LRA": 11}
ACTIVE = ("queued", "running")


class ExportInterrupted(RuntimeError):
    """ffmpeg was stopped by a signal FilmLab didn't send (e.g. systemd stopping the backend's whole unit)."""


class ExportError(ValueError):
    pass


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def srt_text(captions: list[dict]) -> str:
    def ts(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    out = []
    for i, c in enumerate(sorted(captions, key=lambda c: c["start_s"]), start=1):
        out.append(f"{i}\n{ts(c['start_s'])} --> {ts(c['end_s'])}\n{c['text']}\n")
    return "\n".join(out)


async def ffmpeg_version() -> str:
    out = await _run("ffmpeg", "-hide_banner", "-version")
    return out.decode().splitlines()[0]


async def stream_info(path: Path) -> dict:
    out = await _run("ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path))
    info = {"video": None, "audio": None}
    for s in json.loads(out).get("streams", []):
        if s.get("codec_type") == "video" and not info["video"]:
            info["video"] = {k: s.get(k) for k in ("codec_name", "profile", "pix_fmt", "width", "height", "r_frame_rate")}
        if s.get("codec_type") == "audio" and not info["audio"]:
            info["audio"] = {k: s.get(k) for k in ("codec_name", "sample_rate", "channels")}
    return info


def copy_possible(m: dict) -> bool:
    """Join without re-encoding only when nothing needs a filter: untrimmed cuts of identical streams."""
    clips = m["segments"]
    if (not clips or any(s["kind"] != "clip" for s in clips) or m["music"] or m["loudness"]
            or (m["captions"] and m["burn_captions"])):
        return False
    first = clips[0]["streams"]
    return all(s["transition"] == "cut" and s["in"] == 0 and s["out"] == s["src"]["frames"] and not s["mute"]
               and s["gain_db"] == 0 and s["streams"] == first and first["audio"] for s in clips)


def build_command(m: dict, media_dir: Path, library_root: Path, workdir: Path, out: Path,
                  measure: bool = False) -> list[str]:
    """The ffmpeg command for a manifest (encode path). Text goes through files, so nothing needs escaping.
    With measure=True it is the loudness pass: same graph, loudnorm only measures, nothing is written."""
    fps, W, H = m["fps"], m["width"], m["height"]
    enc = m["encode"]
    args = ["ffmpeg", "-y", "-v", "info" if measure else "error", "-nostdin", "-progress", "pipe:1", "-nostats"]
    graph, n_in = [], 0
    vlabels, alabels, durs = [], [], []
    for k, seg in enumerate(m["segments"]):
        d = seg["frames"] / fps
        durs.append(d)
        if seg["kind"] == "clip":
            args += ["-i", str(media_dir / seg["rel_path"])]
            i = n_in
            n_in += 1
            sfps = seg["src"]["fps"] or fps
            graph.append(f"[{i}:v]trim=start_frame={seg['in']}:end_frame={seg['out']},setpts=PTS-STARTPTS,fps={fps},"
                         f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                         f"setsar=1,format=yuv420p,settb=1/{fps}[v{k}]")
            if seg["src"]["has_audio"] and not seg["mute"]:
                graph.append(f"[{i}:a]atrim=start={seg['in'] / sfps:.6f}:end={seg['out'] / sfps:.6f},asetpts=PTS-STARTPTS,"
                             f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={seg['gain_db']}dB,"
                             f"apad,atrim=duration={d:.6f}[a{k}]")
            else:
                graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.6f},aformat=sample_fmts=fltp[a{k}]")
        else:  # title card: text on black
            tf = workdir / f"title_{k}.txt"
            tf.write_text(seg["text"], encoding="utf-8")
            graph.append(f"color=c=black:s={W}x{H}:r={fps}:d={d:.6f},format=yuv420p,"
                         f"{_drawtext(tf, seg['text_position'], H, size=0.07)},setsar=1,settb=1/{fps}[v{k}]")
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.6f},aformat=sample_fmts=fltp[a{k}]")
        vlabels.append(f"v{k}")
        alabels.append(f"a{k}")
    # Join pairwise: concat for cuts, xfade (the transition's effect) and acrossfade for the rest.
    av, aa, length = vlabels[0], alabels[0], durs[0]
    for k in range(1, len(vlabels)):
        prev = m["segments"][k - 1]
        nv, na = f"jv{k}", f"ja{k}"
        if prev["transition"] == "cut":
            graph.append(f"[{av}][{vlabels[k]}]concat=n=2:v=1:a=0,settb=1/{fps}[{nv}]")
            graph.append(f"[{aa}][{alabels[k]}]concat=n=2:v=0:a=1[{na}]")
            length += durs[k]
        else:
            t = prev["transition_s"]
            kind = TRANSITIONS.get(prev["transition"]) or "fade"  # an unknown name dissolves rather than failing
            graph.append(f"[{av}][{vlabels[k]}]xfade=transition={kind}:duration={t:.6f}:offset={length - t:.6f}[{nv}]")
            graph.append(f"[{aa}][{alabels[k]}]acrossfade=d={t:.6f}:c1=tri:c2=tri[{na}]")
            length += durs[k] - t
        av, aa = nv, na
    total = m["total_s"]
    # Captions burned into the picture.
    vout = av
    if m["captions"] and m["burn_captions"]:
        chain = []
        for j, c in enumerate(m["captions"]):
            tf = workdir / f"caption_{j}.txt"
            tf.write_text(c["text"], encoding="utf-8")
            chain.append(_drawtext(tf, c["position"], H, size=0.05,
                                   enable=f"between(t,{c['start_s']:.3f},{c['end_s']:.3f})"))
        graph.append(f"[{av}]{','.join(chain)}[vcap]")
        vout = "vcap"
    # Music: each track trimmed, faded, placed, optionally ducked under the clips' audio; then mixed.
    aout = aa
    if m["music"]:
        mix_in = []
        duckers = [x for x in m["music"] if x["duck"]]
        graph.append(f"[{aa}]asplit={1 + len(duckers)}" + "".join(f"[sc{j}]" for j in range(1 + len(duckers))))
        mix_in.append("sc0")
        dj = 1
        for j, mu in enumerate(m["music"]):
            args += ["-i", str(library_root / mu["rel_path"])]
            i = n_in
            n_in += 1
            avail = max(0.0, min(mu["duration_s"] - mu["offset_s"], total - mu["start_s"]))
            fo = min(mu["fade_out_s"], avail)
            chain = (f"[{i}:a]atrim=start={mu['offset_s']:.6f}:duration={avail:.6f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={mu['gain_db']}dB")
            if mu["fade_in_s"]:
                chain += f",afade=t=in:st=0:d={mu['fade_in_s']:.3f}"
            if fo:
                chain += f",afade=t=out:st={avail - fo:.6f}:d={fo:.3f}"
            chain += f",adelay={int(round(mu['start_s'] * 1000))}:all=1,apad,atrim=duration={total:.6f}"
            if mu["duck"]:
                graph.append(f"{chain}[m{j}]")
                graph.append(f"[m{j}][sc{dj}]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[md{j}]")
                mix_in.append(f"md{j}")
                dj += 1
            else:
                graph.append(f"{chain}[md{j}]")
                mix_in.append(f"md{j}")
        graph.append("".join(f"[{x}]" for x in mix_in) +
                     f"amix=inputs={len(mix_in)}:duration=first:normalize=0:dropout_transition=0[mix]")
        aout = "mix"
    if m["loudness"]:
        L = m["loudness_target"]
        ln = f"loudnorm=I={L['I']}:TP={L['TP']}:LRA={L['LRA']}"
        if measure:
            ln = ((_gain_chain(m["_trial_gain_db"], L, total) + ",") if "_trial_gain_db" in m else "") +                 ln + ":print_format=json"
        elif (q := m.get("loudness_measured")):
            # Second pass: one gain from the measurement, then a peak limiter (delay-compensated, so sync holds)
            # for the ceiling. loudnorm itself falls back to dynamic mode whenever the gain would push peaks over
            # the ceiling, which is most real takes, and then undershoots short films by several LU.
            ln = _gain_chain(q.get("gain_db", L["I"] - float(q["input_i"])), L, total)
        graph.append(f"[{aout}]{ln},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[aln]")
        aout = "aln"
    if measure:
        return args + ["-filter_complex", ";".join(graph), "-map", f"[{vout}]", "-map", f"[{aout}]",
                       "-t", f"{total:.6f}", "-f", "null", "-"]
    v, a = enc["video"], enc["audio"]
    args += ["-filter_complex", ";".join(graph), "-map", f"[{vout}]", "-map", f"[{aout}]", "-t", f"{total:.6f}",
             "-c:v", v["codec"], "-preset", v["preset"], "-crf", str(v["crf"]), "-profile:v", v["profile"],
             "-pix_fmt", v["pix_fmt"], "-threads", str(v["threads"]), "-r", str(fps),
             "-c:a", a["codec"], "-b:a", a["bitrate"], "-ar", str(a["rate"]), "-ac", str(a["channels"]),
             "-movflags", "+faststart", "-map_metadata", "-1", "-fflags", "+bitexact",
             "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-f", "mp4", str(out)]
    return args


def _gain_chain(gain: float, L: dict, total: float) -> str:
    """Gain, then a 4x-oversampled peak limiter under the true-peak ceiling. Its lookahead delay is compensated
    and the tail padded back to the film's length, so picture and sound stay frame-aligned."""
    return (f"volume={gain:.2f}dB,aresample=192000,alimiter=limit={10 ** ((L['TP'] - 0.5) / 20):.4f}:"
            f"attack=5:release=80:level=false:latency=true,aresample=48000,apad,atrim=duration={total:.6f}")


def _drawtext(textfile: Path, position: str, H: int, size: float, enable: str | None = None) -> str:
    y = {"top": "h*0.08", "center": "(h-text_h)/2", "bottom": "h-text_h-h*0.08"}[position]
    s = (f"drawtext=fontfile='{FONT}':textfile='{textfile}':fontcolor=white:fontsize={max(12, int(H * size))}:"
         f"x=(w-text_w)/2:y={y}:box=1:boxcolor=black@0.45:boxborderw={max(6, int(H * 0.012))}")
    return s + (f":enable='{enable}'" if enable else "")


def copy_command(m: dict, media_dir: Path, workdir: Path, out: Path) -> list[str]:
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{media_dir / s['rel_path']}'\n" for s in m["segments"]), encoding="utf-8")
    return ["ffmpeg", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-f", "concat", "-safe", "0",
            "-i", str(lst), "-c", "copy", "-movflags", "+faststart", "-map_metadata", "-1", "-f", "mp4", str(out)]


class Exporter:
    def __init__(self, db: Database, bus: EventBus, edit, library, media_dir: Path):
        self.db, self.bus, self.edit, self.library = db, bus, edit, library
        self.media_dir = media_dir
        self._sem = asyncio.Semaphore(1)  # one encode at a time: it's CPU-heavy
        self._tasks: dict[str, asyncio.Task] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._interrupted: dict[str, int] = {}  # export id -> times ffmpeg was killed from outside
        self.closing = False

    # --- manifest --------------------------------------------------------

    async def manifest(self, project_id: str, kind: str, options: dict) -> dict:
        tl = await self.edit.timeline(project_id)
        if not any(s["kind"] == "clip" for s in tl["segments"]):
            raise ExportError("the edit has no clips yet: render shots (and pick takes) first")
        doc = tl["doc"]
        W, H = tl["width"], tl["height"]
        if kind == "draft":
            W, H = _even(W * 540 / H), 540
        segments = []
        for s in tl["segments"]:
            if s["kind"] == "clip":
                path = self.media_dir / s["rel_path"]
                if not path.is_file():
                    raise ExportError(f"the take of shot {s['position']} is missing from disk")
                s = s | {"streams": await stream_info(path)}
            segments.append({k: v for k, v in s.items() if k not in ("start_s", "title")} |
                            ({"title": s.get("title")} if s["kind"] == "clip" else {}))
        music = []
        for mu in doc["music"]:
            f = await self.library.file(mu["file_id"])
            meta = await probe(self.library.path(f))
            music.append(mu | {"rel_path": f["rel_path"], "sha256": f["sha256"], "duration_s": meta["duration_s"] or 0})
        captions = [{k: c[k] for k in ("text", "start_s", "end_s", "position")} for c in doc["texts"] if c["kind"] == "caption"]
        m = {"version": 1, "kind": kind, "fps": tl["fps"], "width": W, "height": H, "total_s": tl["total_s"],
             "segments": segments, "music": music, "captions": captions,
             "burn_captions": bool(options.get("burn_captions", True)) and bool(captions),
             "srt": bool(options.get("srt", False)) and bool(captions),
             "loudness": bool(options.get("loudness", doc["settings"]["loudness"])), "loudness_target": LOUDNESS,
             "encode": DRAFT if kind == "draft" else ENCODE, "ffmpeg": await ffmpeg_version(), "created_at": now()}
        m["mode"] = "copy" if kind == "final" and copy_possible(m) else "encode"
        if m["mode"] == "copy":
            v = segments[0]["streams"]["video"]
            m["width"], m["height"] = v["width"], v["height"]
        return m

    async def check_inputs(self, m: dict):
        """Every input must still be exactly what the manifest says (same bytes)."""
        for s in m["segments"]:
            if s["kind"] == "clip":
                p = self.media_dir / s["rel_path"]
                if not p.is_file() or await asyncio.to_thread(sha256_file, p) != s["sha256"]:
                    raise ExportError(f"the take of shot {s.get('position')} changed or is missing")
        for mu in m["music"]:
            p = self.library.root / mu["rel_path"]
            if not p.is_file() or await asyncio.to_thread(sha256_file, p) != mu["sha256"]:
                raise ExportError("a music file changed or is missing")

    # --- jobs ------------------------------------------------------------

    async def start(self, project_id: str, kind: str, options: dict, actor: str) -> dict:
        if kind not in ("draft", "final"):
            raise ExportError("kind must be draft or final")
        if await self.db.fetchone(f"SELECT 1 FROM exports WHERE project_id = ? AND status IN {ACTIVE}", (project_id,)):
            raise ExportError("an export of this project is already running")
        m = await self.manifest(project_id, kind, options)
        eid = new_id()
        rel = Path(project_id) / "exports" / eid
        await self.db.execute("INSERT INTO exports (id, project_id, kind, status, manifest, mode, rel_dir, srt, created_at) "
                              "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                              (eid, project_id, kind, json.dumps(m, sort_keys=True), m["mode"], str(rel), int(m["srt"]), now()))
        await self.db.audit(actor, "export.start", eid, {"kind": kind, "mode": m["mode"], "total_s": m["total_s"]})
        await self._changed(project_id, eid)
        self._spawn(eid)
        return await self.get(eid)

    def _spawn(self, eid: str):
        if eid not in self._tasks or self._tasks[eid].done():
            self._tasks[eid] = asyncio.create_task(self._run(eid))

    async def resume_all(self):
        """After a restart: exports that were queued or running start again cleanly from their manifest."""
        for r in await self.db.fetchall(f"SELECT id FROM exports WHERE status IN {ACTIVE}"):
            await self.db.execute("UPDATE exports SET status = 'queued', progress = NULL WHERE id = ?", (r["id"],))
            self._spawn(r["id"])

    async def get(self, eid: str) -> dict:
        e = await self.db.fetchone("SELECT * FROM exports WHERE id = ?", (eid,))
        if not e:
            raise ExportError("no such export")
        m = json.loads(e.pop("manifest"))
        e["summary"] = {"total_s": m["total_s"], "width": m["width"], "height": m["height"], "fps": m["fps"],
                        "clips": sum(1 for s in m["segments"] if s["kind"] == "clip"),
                        "titles": sum(1 for s in m["segments"] if s["kind"] == "title"),
                        "music": len(m["music"]), "captions": len(m["captions"]), "burn_captions": m["burn_captions"],
                        "loudness": m["loudness"], "mode": m["mode"]}
        return e

    async def for_project(self, project_id: str) -> list[dict]:
        rows = await self.db.fetchall("SELECT id FROM exports WHERE project_id = ? AND deleted_at IS NULL "
                                      "ORDER BY created_at DESC LIMIT 30", (project_id,))
        return [await self.get(r["id"]) for r in rows]

    async def _run(self, eid: str):
        async with self._sem:
            if self.closing:
                return
            e = await self.db.fetchone("SELECT * FROM exports WHERE id = ?", (eid,))
            if not e or e["status"] not in ACTIVE:
                return
            m = json.loads(e["manifest"])
            outdir = self.media_dir / e["rel_dir"]
            shutil.rmtree(outdir, ignore_errors=True)  # a restarted export begins from nothing
            outdir.mkdir(parents=True)
            await self.db.execute("UPDATE exports SET status = 'running', progress = 0, error = NULL, started_at = ? "
                                  "WHERE id = ?", (now(), eid))
            await self._changed(e["project_id"], eid)
            part = outdir / "film.mp4.part"
            try:
                await self.check_inputs(m)
                span = (0.0, 1.0)
                if m["mode"] == "encode" and m["loudness"]:
                    if "loudness_measured" not in m:
                        m["loudness_measured"] = await self.measure_loudness(eid, m, outdir, progress=True)
                        await self.db.execute("UPDATE exports SET manifest = ? WHERE id = ?", (json.dumps(m), eid))
                    span = (0.4, 1.0)
                await self._ffmpeg(eid, m, outdir, part, progress=True, span=span)
                final = outdir / "film.mp4"
                part.rename(final)
                (outdir / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
                for work in [*outdir.glob("title_*.txt"), *outdir.glob("caption_*.txt"), *outdir.glob("concat.txt"),
                             outdir / "m"]:
                    work.unlink(missing_ok=True)  # the ffmpeg graph's working files; the manifest has the texts
                if m["srt"]:
                    (outdir / "film.srt").write_text(srt_text(m["captions"]), encoding="utf-8")
                meta = await probe(final)
                digest = await asyncio.to_thread(sha256_file, final)
                await self._finish(e, digest, final.stat().st_size, meta["duration_s"])
            except asyncio.CancelledError:
                part.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001 - recorded on the export, shown in the cockpit
                part.unlink(missing_ok=True)
                if self.closing:  # the backend is stopping: leave it "running"; the next start redoes it cleanly
                    return
                cancelled = (await self.db.fetchone("SELECT status FROM exports WHERE id = ?", (eid,)))["status"] == "cancelled"
                if isinstance(exc, ExportInterrupted) and not cancelled:
                    # Usually the backend is being stopped and the signal reached ffmpeg first: leave it queued so
                    # the next start redoes it. If the backend carries on, try once more, then give up.
                    await self.db.execute("UPDATE exports SET status = 'queued', progress = NULL WHERE id = ?", (eid,))
                    shutil.rmtree(outdir, ignore_errors=True)
                    await self._changed(e["project_id"], eid)
                    tries = self._interrupted[eid] = self._interrupted.get(eid, 0) + 1
                    asyncio.get_running_loop().call_later(5, self._retry_interrupted, eid, tries, str(exc))
                    return
                if not cancelled:
                    log.warning("export %s failed: %s", eid, exc)
                    await self.db.execute("UPDATE exports SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                          (str(exc)[-800:], now(), eid))
                shutil.rmtree(outdir, ignore_errors=True)
            await self._changed(e["project_id"], eid)

    async def _ffmpeg(self, eid: str, m: dict, workdir: Path, out: Path, progress: bool, measure: bool = False,
                      span: tuple[float, float] = (0.0, 1.0)) -> str:
        cmd = (copy_command(m, self.media_dir, workdir, out) if m["mode"] == "copy"
               else build_command(m, self.media_dir, self.library.root, workdir, out, measure=measure))
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if progress:
            self._procs[eid] = proc
        last = 0.0
        try:
            async for line in proc.stdout:
                k, _, v = line.decode(errors="replace").strip().partition("=")
                if progress and k == "out_time_us" and v.isdigit():
                    frac = span[0] + (span[1] - span[0]) * min(1.0, int(v) / 1e6 / max(m["total_s"], 0.01))
                    loop = asyncio.get_running_loop().time()
                    if loop - last > 0.7:
                        last = loop
                        await self.db.execute("UPDATE exports SET progress = ? WHERE id = ?", (round(frac, 3), eid))
                        await self.bus.publish("export.updated", {"export_id": eid, "progress": frac})
            err = await proc.stderr.read()
            code = await proc.wait()
        finally:
            self._procs.pop(eid, None)
        if code < 0 and progress:
            raise ExportInterrupted(f"ffmpeg was stopped by signal {-code}")
        if code != 0:
            raise ExportError(f"ffmpeg failed: {err.decode(errors='replace')[-600:]}")
        return err.decode(errors="replace")

    async def measure_loudness(self, eid: str, m: dict, workdir: Path, progress: bool) -> dict:
        """Pass 1: the film's loudness as loudnorm measures it, stored in the manifest so the encode (and any
        later verify) applies exactly the same gain."""
        def parse(err: str) -> dict:
            start = err.rfind("{")
            try:
                q = json.loads(err[start:err.index("}", start) + 1])
                return {k: q[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
            except (ValueError, KeyError) as exc:
                raise ExportError("couldn't measure the film's loudness") from exc
        target = m["loudness_target"]["I"]
        q = parse(await self._ffmpeg(eid, m, workdir, workdir / "m", progress, measure=True, span=(0.0, 0.2)))
        if float(q["input_i"]) < -70:  # silence: nothing to normalise
            return q | {"gain_db": 0.0}
        gain = target - float(q["input_i"])
        # The limiter takes some loudness off; measure the result once and correct the gain for it.
        check = parse(await self._ffmpeg(eid, m | {"_trial_gain_db": gain}, workdir, workdir / "m", progress,
                                         measure=True, span=(0.2, 0.4)))
        if float(check["input_i"]) > -70:
            gain += target - float(check["input_i"])
        return q | {"gain_db": round(min(gain, 30.0), 2), "check_i": check["input_i"]}

    async def _finish(self, e: dict, digest: str, size: int, duration: float | None):
        ts = now()

        def write(c):
            number = None
            if e["kind"] == "final":
                number = c.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM exports WHERE project_id = ?",
                                   (e["project_id"],)).fetchone()[0]
            c.execute("UPDATE exports SET status = 'done', progress = 1, sha256 = ?, bytes = ?, duration_s = ?, "
                      "number = ?, finished_at = ? WHERE id = ?", (digest, size, duration, number, ts, e["id"]))
            if e["kind"] == "draft":  # only the newest draft is kept
                c.execute("UPDATE exports SET deleted_at = ? WHERE project_id = ? AND kind = 'draft' AND id != ? "
                          "AND deleted_at IS NULL AND status = 'done'", (ts, e["project_id"], e["id"]))
        await self.db.transaction(write)
        if e["kind"] == "draft":
            for old in await self.db.fetchall("SELECT rel_dir FROM exports WHERE project_id = ? AND kind = 'draft' "
                                              "AND deleted_at IS NOT NULL", (e["project_id"],)):
                shutil.rmtree(self.media_dir / old["rel_dir"], ignore_errors=True)
        await self.db.audit("system", "export.done", e["id"], {"sha256": digest, "kind": e["kind"]})

    async def cancel(self, eid: str, actor: str) -> dict:
        e = await self.get(eid)
        if e["status"] not in ACTIVE:
            raise ExportError(f"the export is {e['status']}")
        await self.db.execute("UPDATE exports SET status = 'cancelled', finished_at = ? WHERE id = ?", (now(), eid))
        proc = self._procs.get(eid)
        if proc and proc.returncode is None:
            proc.kill()
        await self.db.audit(actor, "export.cancel", eid)
        await self._changed(e["project_id"], eid)
        return await self.get(eid)

    async def delete(self, eid: str, actor: str) -> dict:
        e = await self.get(eid)
        if e["status"] in ACTIVE:
            raise ExportError("cancel the export first")
        await self.db.execute("UPDATE exports SET deleted_at = ? WHERE id = ?", (now(), eid))
        await self.db.audit(actor, "export.delete", eid)
        await self._changed(e["project_id"], eid)
        return {"deleted": eid}

    async def file(self, eid: str, what: str) -> tuple[Path, str]:
        e = await self.db.fetchone("SELECT * FROM exports WHERE id = ? AND status = 'done' AND deleted_at IS NULL", (eid,))
        if not e:
            raise ExportError("no such finished export")
        name = {"mp4": "film.mp4", "srt": "film.srt", "manifest": "manifest.json"}.get(what)
        if not name:
            raise ExportError("unknown file")
        root = self.media_dir.resolve()
        path = (root / e["rel_dir"] / name).resolve()
        if root not in path.parents or not path.is_file():
            raise ExportError("that file isn't part of this export")
        project = await self.db.fetchone("SELECT title FROM projects WHERE id = ?", (e["project_id"],))
        title = re.sub(r"[^\w\- ]+", "", project["title"]).strip() or "film"
        label = f"Delivery {e['number']}" if e["number"] else "Draft"
        return path, f"{title} - {label}.{name.rsplit('.', 1)[1]}"

    # --- verification ---------------------------------------------------------

    async def verify(self, eid: str, actor: str) -> dict:
        e = await self.db.fetchone("SELECT * FROM exports WHERE id = ? AND status = 'done' AND deleted_at IS NULL", (eid,))
        if not e:
            raise ExportError("only a finished export can be verified")
        if e["verify_status"] == "running":
            raise ExportError("verification is already running")
        await self.db.execute("UPDATE exports SET verify_status = 'running', verify_detail = NULL WHERE id = ?", (eid,))
        await self.db.audit(actor, "export.verify", eid)
        self._tasks[f"verify:{eid}"] = asyncio.create_task(self._verify(e))
        await self._changed(e["project_id"], eid)
        return await self.get(eid)

    async def _verify(self, e: dict):
        m = json.loads(e["manifest"])
        work = self.media_dir / e["rel_dir"] / "verify"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            async with self._sem:
                await self.check_inputs(m)
                rebuilt = work / "rebuilt.mp4"
                await self._ffmpeg(e["id"], m, work, rebuilt, progress=False)
            a = await self._framehashes(self.media_dir / e["rel_dir"] / "film.mp4")
            b = await self._framehashes(rebuilt)
            if a == b:
                status, detail = "match", f"{len(a)} video frames and audio packets decode identically"
            else:
                diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                status, detail = "mismatch", f"differs from frame/packet {diff} ({len(a)} vs {len(b)})"
        except Exception as exc:  # noqa: BLE001
            status, detail = "failed", str(exc)[-400:]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        await self.db.execute("UPDATE exports SET verify_status = ?, verify_detail = ?, verified_at = ? WHERE id = ?",
                              (status, detail, now(), e["id"]))
        await self.db.audit("system", "export.verified", e["id"], {"status": status, "detail": detail})
        await self._changed(e["project_id"], e["id"])

    @staticmethod
    async def _framehashes(path: Path) -> list[str]:
        out = await _run("ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v", "-map", "0:a",
                         "-f", "framemd5", "-")
        return [ln for ln in out.decode().splitlines() if ln and not ln.startswith("#")]

    async def _changed(self, project_id: str, eid: str):
        await self.bus.publish("export.updated", {"export_id": eid}, project_id)

    def _retry_interrupted(self, eid: str, tries: int, why: str):
        if self.closing:
            return
        if tries <= 1:
            log.warning("export %s: %s; starting it again", eid, why)
            self._spawn(eid)
        else:
            asyncio.ensure_future(self._fail(eid, f"{why} (twice); start the export again"))

    async def _fail(self, eid: str, error: str):
        await self.db.execute("UPDATE exports SET status = 'failed', error = ?, finished_at = ? WHERE id = ? "
                              "AND status = 'queued'", (error, now(), eid))
        e = await self.db.fetchone("SELECT project_id FROM exports WHERE id = ?", (eid,))
        if e:
            await self._changed(e["project_id"], eid)

    async def shutdown(self):
        self.closing = True
        for p in list(self._procs.values()):
            if p.returncode is None:
                p.kill()
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


# --- waveforms and filmstrips (cached by content hash) ---------------------------------

async def waveform(src: Path, digest: str, cache: Path) -> Path:
    out = cache / "waveforms" / f"{digest}.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.png")
        await _run("ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(src), "-filter_complex",
                   "aformat=channel_layouts=mono,showwavespic=s=800x80:colors=#d9a441", "-frames:v", "1", str(tmp))
        tmp.rename(out)
    return out


async def filmstrip(src: Path, digest: str, cache: Path, frames: int | None, fps: float | None) -> Path:
    out = cache / "filmstrips" / f"{digest}.jpg"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.jpg")
        n = 8
        every = max(1, int((frames or 120) / n))
        await _run("ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(src), "-vf",
                   f"select='not(mod(n\\,{every}))',scale=-2:72,tile={n}x1", "-frames:v", "1", "-q:v", "5", str(tmp))
        tmp.rename(out)
    return out


def manifest_digest(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
