import { TransitionSelect } from "./TransitionSelect";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ask } from "./Dialog";
import { api, type ExportRow, type Music, type ProjectDetail, type Segment, type TextItem, type Timeline } from "./api";
import type { Listener } from "./App";
import { lib } from "./library-api";
import { SoundtrackModal } from "./Soundtrack";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
type Subscribe = (l: Listener, r: () => void) => () => void;
const ACTIVE = new Set(["queued", "running"]);
const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, "0")}`;

/** Assemble the film: clips from the storyboard with their chosen takes, music, titles, captions; export. */
export function EditView({ project, subscribe }: { project: ProjectDetail; subscribe: Subscribe }) {
  const [tl, setTl] = useState<Timeline | null>(null);
  const [exports, setExports] = useState<ExportRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  /** The soundtrack modal: undefined closed, null adding one, a number changing that music track. */
  const [soundtrack, setSoundtrack] = useState<number | null | undefined>(undefined);
  const seekRef = useRef<(t: number) => void>(() => undefined);
  const timer = useRef<number>();

  const load = useCallback(() => {
    Promise.all([api.edit(project.id), api.exports(project.id)])
      .then(([t, e]) => { setTl(t); setExports(e); }).catch((e) => setError((e as Error).message));
  }, [project.id]);
  useEffect(load, [load]);
  useEffect(() => subscribe((e) => {
    if (e.project_id !== project.id) return;
    if (["edit.updated", "export.updated", "shot.updated", "render.done"].includes(e.type)) {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(load, 150);
    }
  }, load), [subscribe, load, project.id]);

  const act: Act = async (fn) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
    load();
  };
  const patch = (p: Parameters<typeof api.patchEdit>[1]) => act(() => api.patchEdit(project.id, p));

  if (!tl) return <main className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading the edit…</p>}</main>;
  const clips = tl.segments.filter((s) => s.kind === "clip");
  const leftOut = project.shots.filter((s) => tl.doc.clips[s.id]?.skip);
  return (
    <main className="edit-view">
      <section className="panel ev-left">
        <div className="panel-head"><h2>Preview</h2>
          <span className="muted small">{clips.length} clip{clips.length === 1 ? "" : "s"} · {fmt(tl.total_s)} · {tl.width}×{tl.height} · {tl.fps} fps</span></div>
        {error && <p className="error" role="alert">{error}</p>}
        {tl.issues.map((i, n) => <p key={n} className="warn small">{i}</p>)}
        <Preview tl={tl} onTime={setPlayhead} seekRef={seekRef} />
        <ExportPanel project={project} tl={tl} exports={exports} act={act} />
      </section>
      <section className="panel ev-right">
        {tl.suggestions.length > 0 && (
          <div className="card approval-list">
            <h3>Director suggests</h3>
            {tl.suggestions.map((s) => (
              <div key={s.id} className="row">
                <span className="grow small">{s.note} <code className="muted">{JSON.stringify(s.change).slice(0, 160)}</code></span>
                <button className="ghost small" onClick={() => act(() => api.decideSuggestion(s.id, false))}>Reject</button>
                <button className="primary small" onClick={() => act(() => api.decideSuggestion(s.id, true))}>Accept</button>
              </div>
            ))}
          </div>
        )}
        <div className="panel-head"><h2>Timeline</h2>
          <span className="muted small grow">order and transitions come from the storyboard</span>
          {tl.doc.music.length < 2 && (
            <button className="ghost small" title="Play a song (MP3, WAV or FLAC) under the film"
              onClick={() => setSoundtrack(null)}>🎵 Add soundtrack</button>)}</div>
        <ol className="timeline">
          {tl.segments.map((s, i) => s.kind === "clip"
            ? <ClipRow key={s.shot_id} seg={s} tl={tl} project={project} playhead={playhead} onSeek={(t) => seekRef.current(t)} patch={patch} act={act} />
            : <li key={`t${s.id}${i}`} className="card seg-title" onClick={() => seekRef.current(s.start_s)}>
                <strong>Title</strong> “{s.text}” <span className="muted small">· {(s.frames / tl.fps).toFixed(1)} s at {fmt(s.start_s)}</span></li>)}
          {project.shots.filter((s) => !tl.segments.some((g) => g.shot_id === s.id) && !tl.doc.clips[s.id]?.skip).map((s) => (
            <li key={s.id} className="card seg-missing small muted">{s.position}. {s.title}: no usable take yet (every take is rejected, or none rendered)</li>
          ))}
        </ol>
        {leftOut.length > 0 && (
          <div className={`card left-out ${clips.length === 0 ? "all" : ""}`}>
            <div className="row">
              <strong className="grow">{clips.length === 0 ? "Every shot is left out of the film" : `Left out of the film (${leftOut.length})`}</strong>
              {leftOut.length > 1 && <button className="primary small" onClick={() => patch({
                clips: Object.fromEntries(leftOut.map((s) => [s.id, { skip: false }])), note: "put every left-out shot back" })}>Put all back</button>}
            </div>
            <ul className="small">{leftOut.map((s) => (
              <li key={s.id} className="row"><span className="grow">{s.position}. {s.title}</span>
                <button className="ghost small" onClick={() => patch({ clips: { [s.id]: { skip: false } }, note: `put shot ${s.position} back` })}>Put back</button></li>
            ))}</ul>
            <p className="muted small">“Leave out” only takes a shot out of this film; its takes stay. To remove a take you don't
              need, use Delete under the take in the Storyboard view.</p>
          </div>
        )}
        <TextsPanel tl={tl} project={project} playhead={playhead} patch={patch} />
        <MusicPanel tl={tl} patch={patch} open={setSoundtrack} />
        {soundtrack !== undefined && <SoundtrackModal tl={tl} editing={soundtrack} onClose={() => setSoundtrack(undefined)} onSaved={load} />}
        <details className="card">
          <summary><strong>Settings and history</strong></summary>
          <label className="small row check"><input type="checkbox" className="switch" checked={tl.doc.settings.loudness}
            onChange={(e) => patch({ settings: { ...tl.doc.settings, loudness: e.target.checked }, note: "loudness setting" })} />
            Normalise loudness to −14 LUFS (streaming level) on export</label>
          <label className="small">Default transition length (s)
            <NumberField value={tl.doc.settings.transition_s} min={0} max={3} step={0.05}
              onCommit={(v) => patch({ settings: { ...tl.doc.settings, transition_s: v }, note: "default transition" })} /></label>
          <ol className="history small">
            {tl.revisions.map((r, i) => (
              <li key={r.id} className="row">
                <span className="grow">{r.actor === "owner" ? "You" : r.actor}: {r.note || "edit"} <span className="muted">· {new Date(r.created_at).toLocaleString()}</span></span>
                {i > 0 && <button className="ghost small" onClick={() => act(async () => {
                  if (await ask("Restore the edit as it was after this change?")) await api.restoreEdit(r.id);
                })}>Restore</button>}
              </li>
            ))}
          </ol>
        </details>
      </section>
    </main>
  );
}

function NumberField({ value, onCommit, min, max, step, label }: {
  value: number; onCommit: (v: number) => void; min: number; max: number; step: number; label?: string;
}) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => { const n = Number(v); if (Number.isFinite(n) && n !== value) onCommit(Math.min(max, Math.max(min, n))); else setV(String(value)); };
  return <input type="number" aria-label={label} value={v} min={min} max={max} step={step} onChange={(e) => setV(e.target.value)}
    onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />;
}

function ClipRow({ seg, tl, project, playhead, onSeek, patch, act }: {
  seg: Segment; tl: Timeline; project: ProjectDetail; playhead: number; onSeek: (t: number) => void;
  patch: (p: Parameters<typeof api.patchEdit>[1]) => Promise<void>; act: Act;
}) {
  const fps = tl.fps, sid = seg.shot_id!, src = seg.src!;
  const inS = (seg.in ?? 0) / fps, outS = (seg.out ?? src.frames) / fps;
  const here = playhead >= seg.start_s && playhead < seg.start_s + seg.frames / fps;
  const local = Math.max(0, Math.min(src.frames / fps, inS + (playhead - seg.start_s)));
  const set = (c: Record<string, unknown>, note: string) => patch({ clips: { [sid]: c }, note });
  return (
    <li className={`card seg-clip ${here ? "here" : ""}`}>
      <div className="row">
        <button className="link" onClick={() => onSeek(seg.start_s)}><strong>{seg.position}. {seg.title}</strong></button>
        <span className="muted small grow">at {fmt(seg.start_s)} · {(seg.frames / fps).toFixed(2)} s of {(src.frames / fps).toFixed(2)} s</span>
        <TakePicker seg={seg} project={project} act={act} />
        <button className="ghost small" title="Leave this shot out of the film (you can put it back)" onClick={() => set({ skip: true }, `left out shot ${seg.position}`)}>Leave out</button>
      </div>
      {seg.media_id && <img className="filmstrip" src={api.filmstripUrl(seg.media_id)} alt="" loading="lazy" />}
      {seg.media_id && src.has_audio && !seg.mute && <img className="waveform" src={api.waveformUrl(seg.media_id)} alt="" loading="lazy" />}
      <div className="row small">
        <label>In (s)<NumberField value={Number(inS.toFixed(3))} min={0} max={outS - 1 / fps} step={1 / fps}
          onCommit={(v) => set({ in: Math.round(v * fps) }, `trimmed the start of shot ${seg.position}`)} /></label>
        <label>Out (s)<NumberField value={Number(outS.toFixed(3))} min={inS + 1 / fps} max={src.frames / fps} step={1 / fps}
          onCommit={(v) => set({ out: Math.round(v * fps) }, `trimmed the end of shot ${seg.position}`)} /></label>
        {here && <>
          <button className="ghost small" onClick={() => set({ in: Math.round(local * fps) }, `trimmed the start of shot ${seg.position}`)}>In = playhead</button>
          <button className="ghost small" onClick={() => set({ out: Math.max(Math.round(local * fps), (seg.in ?? 0) + 1) }, `trimmed the end of shot ${seg.position}`)}>Out = playhead</button>
        </>}
        <label>Gain (dB)<NumberField value={seg.gain_db ?? 0} min={-40} max={12} step={1}
          onCommit={(v) => set({ gain_db: v }, `audio level of shot ${seg.position}`)} /></label>
        <label className="check"><input type="checkbox" checked={!!seg.mute} onChange={(e) => set({ mute: e.target.checked }, `${e.target.checked ? "muted" : "unmuted"} shot ${seg.position}`)} /> Mute</label>
      </div>
      <div className="row small">
        <label>Into next
          <TransitionSelect value={seg.transition} onChange={(t) => act(() => api.updateShot(sid, { transition: t }))} />
        </label>
        {seg.transition !== "cut" && <label>Length (s)<NumberField value={seg.transition_s} min={0.05} max={3} step={0.05}
          onCommit={(v) => set({ transition_s: v }, `transition after shot ${seg.position}`)} /></label>}
      </div>
    </li>
  );
}

function TextsPanel({ tl, project, playhead, patch }: {
  tl: Timeline; project: ProjectDetail; playhead: number; patch: (p: Parameters<typeof api.patchEdit>[1]) => Promise<void>;
}) {
  const texts = tl.doc.texts;
  const save = (next: TextItem[], note: string) => patch({ texts: next, note });
  const change = (i: number, c: Partial<TextItem>, note: string) => save(texts.map((t, j) => (j === i ? { ...t, ...c } : t)), note);
  return (
    <details className="card" open={texts.length > 0}>
      <summary><strong>Titles and captions</strong> <span className="muted small">· {texts.length}</span></summary>
      <ul className="texts">
        {texts.map((t, i) => (
          <li key={t.id ?? i} className="row small">
            <span className="tag">{t.kind}</span>
            <input className="grow" defaultValue={t.text} maxLength={200} aria-label="Text"
              onBlur={(e) => e.target.value !== t.text && change(i, { text: e.target.value }, `${t.kind} text`)} />
            {t.kind === "title" ? <>
              <select value={t.at} aria-label="Where" onChange={(e) => change(i, { at: e.target.value }, "moved a title")}>
                <option value="start">At the start</option>
                {project.shots.map((s) => <option key={s.id} value={s.id}>Before {s.position}. {s.title}</option>)}
                <option value="end">At the end</option>
              </select>
              <label>s<NumberField value={t.duration_s ?? 2.5} min={0.5} max={15} step={0.5} onCommit={(v) => change(i, { duration_s: v }, "title length")} /></label>
            </> : <>
              <label>from<NumberField value={t.start_s ?? 0} min={0} max={3600} step={0.1} onCommit={(v) => change(i, { start_s: v }, "caption timing")} /></label>
              <label>to<NumberField value={t.end_s ?? 1} min={0.2} max={3600} step={0.1} onCommit={(v) => change(i, { end_s: v }, "caption timing")} /></label>
            </>}
            <select value={t.position ?? (t.kind === "title" ? "center" : "bottom")} aria-label="Position"
              onChange={(e) => change(i, { position: e.target.value }, `${t.kind} position`)}>
              {["top", "center", "bottom"].map((p) => <option key={p}>{p}</option>)}
            </select>
            <button className="ghost small" onClick={() => save(texts.filter((_, j) => j !== i), `removed a ${t.kind}`)}>Remove</button>
          </li>
        ))}
      </ul>
      <div className="row">
        <button className="ghost small" onClick={() => save([...texts, { kind: "title", text: project.title, at: "start", duration_s: 2.5, position: "center" }], "added a title")}>+ Title card</button>
        <button className="ghost small" onClick={() => save([...texts, { kind: "caption", text: "Caption", start_s: Number(playhead.toFixed(2)),
          end_s: Number(Math.min(playhead + 2, Math.max(tl.total_s, playhead + 0.5)).toFixed(2)), position: "bottom" }], "added a caption")}>+ Caption at playhead</button>
      </div>
    </details>
  );
}

function MusicPanel({ tl, patch, open }: {
  tl: Timeline; patch: (p: Parameters<typeof api.patchEdit>[1]) => Promise<void>; open: (i: number | null) => void;
}) {
  const music = tl.doc.music;
  const muted = !!tl.doc.settings.mute_shots;
  const save = (next: Music[], note: string) => patch({ music: next, note });
  const change = (i: number, c: Partial<Music>, note: string) => save(music.map((m, j) => (j === i ? { ...m, ...c } : m)), note);
  return (
    <details className="card" open={music.length > 0}>
      <summary><strong>Soundtrack and music</strong> <span className="muted small">· {music.length} of 2{muted ? " · shots muted" : ""}</span></summary>
      {music.map((m, i) => (
        <div key={m.id ?? i} className="music">
          <img className="waveform" src={`/api/library/files/${m.file_id}/waveform`} alt="" loading="lazy" />
          <div className="row small">
            <label>Starts at (s)<NumberField value={m.start_s} min={0} max={3600} step={0.1} onCommit={(v) => change(i, { start_s: v }, "music start")} /></label>
            <label>From (s into file)<NumberField value={m.offset_s} min={0} max={3600} step={0.1} onCommit={(v) => change(i, { offset_s: v }, "music offset")} /></label>
            <label>Gain (dB)<NumberField value={m.gain_db} min={-40} max={12} step={1} onCommit={(v) => change(i, { gain_db: v }, "music level")} /></label>
            <label>Fade in<NumberField value={m.fade_in_s} min={0} max={30} step={0.5} onCommit={(v) => change(i, { fade_in_s: v }, "music fade in")} /></label>
            <label>Fade out<NumberField value={m.fade_out_s} min={0} max={30} step={0.5} onCommit={(v) => change(i, { fade_out_s: v }, "music fade out")} /></label>
            <label className="check"><input type="checkbox" checked={m.duck} onChange={(e) => change(i, { duck: e.target.checked }, "music ducking")} /> Duck under dialogue</label>
            <label className="check"><input type="checkbox" checked={!!m.loop} onChange={(e) => change(i, { loop: e.target.checked }, e.target.checked ? "music loops" : "music plays once")} /> Loop</label>
            <button className="ghost small" title="Play it under the whole film: from the start, a gentle fade in and a longer fade out at the end"
              onClick={() => change(i, { start_s: 0, offset_s: 0, fade_in_s: 1, fade_out_s: 3 }, "music under the whole film")}>Whole film</button>
            <button className="ghost small" onClick={() => open(i)}>Edit…</button>
            <button className="ghost small" onClick={() => save(music.filter((_, j) => j !== i), "removed music")}>Remove</button>
          </div>
        </div>
      ))}
      <div className="row small">
        {music.length < 2 && <button className="ghost small" onClick={() => open(null)}>🎵 Add soundtrack</button>}
        <label className="check" title="The soundtrack alone: every shot's own sound (dialogue, effects) is muted, shots added later too">
          <input type="checkbox" checked={muted} onChange={(e) => patch({ settings: { ...tl.doc.settings, mute_shots: e.target.checked },
            note: e.target.checked ? "soundtrack only: shots muted" : "shots' own sound back" })} /> Soundtrack only (mute the shots)</label>
      </div>
    </details>
  );
}

/** Plays the edit in the browser: clips in order with trims, fades approximated, music and captions in sync.
 *  An exact preview is the Draft export, made the same way as the final film. */
function Preview({ tl, onTime, seekRef }: { tl: Timeline; onTime: (t: number) => void; seekRef: { current: (t: number) => void } }) {
  const video = useRef<HTMLVideoElement>(null);
  const audios = useRef<(HTMLAudioElement | null)[]>([]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const clock = useRef<{ at: number; t: number } | null>(null);
  const segs = tl.segments;
  const fps = tl.fps;
  const segAt = useCallback((x: number) => {
    let k = 0;
    for (let i = 0; i < segs.length; i++) if (segs[i].start_s <= x + 1e-6) k = i;
    return segs[k];
  }, [segs]);
  const sync = useCallback((x: number, play: boolean) => {
    const seg = segAt(x);
    const v = video.current;
    if (v && seg?.kind === "clip" && seg.media_id) {
      const url = api.mediaUrl(seg.media_id);
      if (!v.src.endsWith(url)) { v.src = url; }
      const want = (seg.in ?? 0) / fps + (x - seg.start_s);
      if (Math.abs(v.currentTime - want) > 0.2) v.currentTime = want;
      v.volume = seg.mute ? 0 : Math.min(1, Math.pow(10, (seg.gain_db ?? 0) / 20));
      if (play && v.paused) v.play().catch(() => undefined);
      if (!play && !v.paused) v.pause();
      // approximate the transition out of this clip as a fade
      const end = seg.start_s + seg.frames / fps;
      const left = end - x;
      v.style.opacity = seg.transition !== "cut" && left < seg.transition_s ? String(Math.max(0.15, left / seg.transition_s)) : "1";
    } else if (v && !v.paused) v.pause();
    tl.doc.music.forEach((m, i) => {
      const a = audios.current[i];
      if (!a) return;
      let local = x - m.start_s + m.offset_s;
      // Looped like the export: the file repeats from its beginning once it ends.
      if (m.loop && a.duration) local %= a.duration;
      if (x < m.start_s || local >= (a.duration || Infinity)) { if (!a.paused) a.pause(); return; }
      if (Math.abs(a.currentTime - local) > 0.25) a.currentTime = local;
      a.volume = Math.min(1, Math.pow(10, m.gain_db / 20));
      if (play && a.paused) a.play().catch(() => undefined);
      if (!play && !a.paused) a.pause();
    });
  }, [segAt, fps, tl.doc.music]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      if (!clock.current) return;
      const x = clock.current.t + (performance.now() - clock.current.at) / 1000;
      if (x >= tl.total_s) { setPlaying(false); setT(tl.total_s); sync(tl.total_s, false); onTime(tl.total_s); return; }
      setT(x); onTime(x); sync(x, true);
      raf = requestAnimationFrame(tick);
    };
    clock.current = { at: performance.now(), t };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);  // eslint-disable-line react-hooks/exhaustive-deps

  const seek = (x: number) => { setT(x); onTime(x); if (clock.current) clock.current = { at: performance.now(), t: x }; sync(x, playing); };
  seekRef.current = seek;
  useEffect(() => { sync(t, false); }, [tl]);  // eslint-disable-line react-hooks/exhaustive-deps
  const seg = segAt(t);
  const captions = useMemo(() => tl.doc.texts.filter((c) => c.kind === "caption" && (c.start_s ?? 0) <= t && t < (c.end_s ?? 0)), [tl, t]);
  return (
    <div className="preview">
      <div className="screen" style={{ aspectRatio: `${tl.width} / ${tl.height}` }}>
        <video ref={video} playsInline preload="auto" className={seg?.kind === "clip" ? "" : "hidden"} />
        {seg?.kind === "title" && <div className={`card-text pos-${seg.text_position}`}>{seg.text}</div>}
        {captions.map((c, i) => <div key={i} className={`caption pos-${c.position ?? "bottom"}`}>{c.text}</div>)}
        {!segs.length && <div className="card-text">Nothing to preview yet: render shots and pick takes.</div>}
      </div>
      {tl.doc.music.map((m, i) => <audio key={m.file_id + i} ref={(el) => { audios.current[i] = el; }} src={lib.fileUrl(m.file_id)} preload="auto" />)}
      <div className="row">
        <button className="primary small" disabled={!segs.length} onClick={() => { if (!playing && t >= tl.total_s - 0.05) seek(0); setPlaying(!playing); if (playing) sync(t, false); }}>
          {playing ? "Pause" : "Play"}</button>
        <input type="range" className="grow" min={0} max={tl.total_s || 0} step={1 / fps} value={t} aria-label="Playhead"
          onChange={(e) => seek(Number(e.target.value))} />
        <span className="small muted">{fmt(t)} / {fmt(tl.total_s)}</span>
      </div>
      <div className="ruler" aria-hidden="true">
        {segs.map((s, i) => (
          <span key={i} className={`r-${s.kind}`} title={s.title ?? s.text}
            style={{ left: `${(s.start_s / (tl.total_s || 1)) * 100}%`, width: `${((s.frames / fps) / (tl.total_s || 1)) * 100}%` }} />
        ))}
        <i style={{ left: `${(t / (tl.total_s || 1)) * 100}%` }} />
      </div>
      <p className="small muted">Approximate preview (fades are simplified). A Draft export renders the exact film at 540p.</p>
    </div>
  );
}

/** The file's name from Content-Disposition (filename*=UTF-8''… or filename="…"). */
function fileName(header: string | null, fallback: string): string {
  const star = header?.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) { try { return decodeURIComponent(star[1].trim()); } catch { /* fall through */ } }
  return header?.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

/** Downloads inside the page, then saves from memory. A plain download link hands the URL to the browser's
 *  downloader, which on Android doesn't trust FilmLab's own certificate authority (or send the session cookie),
 *  so the download failed there as "mp4". Fetching here uses the page's own trusted, signed-in connection. */
function DownloadLink({ url, label }: { url: string; label: string }) {
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const download = async (ev: React.MouseEvent) => {
    ev.preventDefault();
    if (state === "busy") return;
    setState("busy");
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`${res.status}`);
      const name = fileName(res.headers.get("content-disposition"), `${label.toLowerCase()}`);
      const href = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = href; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60_000);
      setState("idle");
    } catch {
      setState("failed");
    }
  };
  return <a className="button ghost small" href={url} onClick={download} title={state === "failed" ? "The download failed; try again" : undefined}>
    {state === "busy" ? `${label}…` : state === "failed" ? `${label} (retry)` : label}</a>;
}

function ExportPanel({ project, tl, exports, act }: { project: ProjectDetail; tl: Timeline; exports: ExportRow[]; act: Act }) {
  const hasCaptions = tl.doc.texts.some((t) => t.kind === "caption");
  const [burn, setBurn] = useState(true);
  const [srt, setSrt] = useState(false);
  const [watch, setWatch] = useState<string | null>(null);
  const busy = exports.some((e) => ACTIVE.has(e.status));
  const start = (kind: "draft" | "final") => act(() => api.startExport(project.id, { kind, burn_captions: burn, srt, loudness: tl.doc.settings.loudness }));
  return (
    <div className="card exports">
      <h3>Export</h3>
      {hasCaptions && (
        <div className="row small">
          <label className="check"><input type="checkbox" checked={burn} onChange={(e) => setBurn(e.target.checked)} /> Burn captions into the picture</label>
          <label className="check"><input type="checkbox" checked={srt} onChange={(e) => setSrt(e.target.checked)} /> Also a .srt file</label>
        </div>
      )}
      <div className="row">
        <button className="ghost" disabled={busy || !tl.segments.length} onClick={() => start("draft")} title="Fast 540p render, made exactly like the final film">Draft export</button>
        <button className="primary" disabled={busy || !tl.segments.length} onClick={() => start("final")}>Export film</button>
        <span className="small muted grow">MP4 · H.264 + AAC · {tl.width}×{tl.height} · {tl.fps} fps{tl.doc.settings.loudness ? " · −14 LUFS" : ""}. Runs on the CPU; WanGP keeps the GPU.</span>
      </div>
      <ul className="deliveries">
        {exports.map((e) => (
          <li key={e.id} className={`delivery ${e.kind}`}>
            <div className="row">
              <strong className="grow">{e.number ? `Delivery ${e.number}` : "Draft"}
                <span className="muted small"> · {e.status === "done" ? `${e.duration_s?.toFixed(2)} s · ${e.summary.width}×${e.summary.height} · ${((e.bytes ?? 0) / 1e6).toFixed(1)} MB · ${e.summary.mode === "copy" ? "joined without re-encoding" : "encoded"}`
                  : e.status} · {new Date(e.created_at).toLocaleString()}</span></strong>
              {ACTIVE.has(e.status) && <button className="ghost small" onClick={() => act(() => api.exportAction(e.id, "cancel"))}>Cancel</button>}
              {e.status === "done" && <>
                <button className="ghost small" onClick={() => setWatch(watch === e.id ? null : e.id)}>{watch === e.id ? "Hide" : "Play"}</button>
                <DownloadLink url={api.exportFile(e.id, "mp4")} label="MP4" />
                {e.srt ? <DownloadLink url={api.exportFile(e.id, "srt")} label="SRT" /> : null}
                <DownloadLink url={api.exportFile(e.id, "manifest")} label="Manifest" />
                {e.kind === "final" && <button className="ghost small" disabled={e.verify_status === "running"}
                  title="Rebuild the film from its manifest alone and compare every frame and audio sample"
                  onClick={() => act(() => api.exportAction(e.id, "verify"))}>{e.verify_status === "running" ? "Verifying…" : "Verify"}</button>}
                <button className="ghost small danger-text" onClick={() => act(async () => {
                  if (await ask(`Delete ${e.number ? `Delivery ${e.number}` : "this draft"}? Its files are removed.`)) await api.deleteExport(e.id);
                })}>Delete</button>
              </>}
            </div>
            {ACTIVE.has(e.status) && <progress max={1} value={e.progress ?? undefined} />}
            {e.status === "failed" && <p className="error small">{e.error}</p>}
            {e.verify_status && e.verify_status !== "running" && (
              <p className={`small ${e.verify_status === "match" ? "ok-text" : "error"}`}>
                {e.verify_status === "match" ? "✔ Reproduced from its manifest: " : e.verify_status === "mismatch" ? "✘ Doesn't match its manifest: " : "Verification failed: "}
                {e.verify_detail}</p>
            )}
            {e.sha256 && <p className="small muted">SHA-256 {e.sha256.slice(0, 16)}… · {e.summary.clips} clips{e.summary.titles ? `, ${e.summary.titles} titles` : ""}{e.summary.music ? `, music` : ""}{e.summary.captions ? `, ${e.summary.captions} captions${e.summary.burn_captions ? " (burned)" : ""}` : ""}</p>}
            {watch === e.id && <video className="player" controls src={api.exportFile(e.id, "mp4")} />}
          </li>
        ))}
        {!exports.length && <li className="small muted">No exports yet.</li>}
      </ul>
    </div>
  );
}

/** Which take of the shot the film uses: by default the newest marked good, else the newest not rejected. */
function TakePicker({ seg, project, act }: { seg: Segment; project: ProjectDetail; act: Act }) {
  const sid = seg.shot_id!;
  const takes = project.takes.filter((t) => t.shot_id === sid);
  const shot = project.shots.find((s) => s.id === sid);
  if (takes.length < 2 || !shot) return null;
  const mark: Record<string, string> = { good: " · 👍 good", maybe: " · 🤔 maybe", reject: " · 👎 rejected" };
  return (
    <label className="small take-pick">Take
      <select value={seg.take_id} onChange={(e) => act(() => api.selectTake(sid, e.target.value))}>
        {takes.map((t, i) => <option key={t.id} value={t.id} disabled={t.verdict === "reject"}>
          {i + 1} of {takes.length}{mark[t.verdict ?? ""] ?? ""}</option>)}
      </select>
      {shot.selected_take_id && <button className="link" title="Go back to the automatic choice (newest good take)"
        onClick={() => act(() => api.selectTake(sid, null))}>auto</button>}
    </label>
  );
}
