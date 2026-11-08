/** Music video setup: the song (generated from exact lyrics, uploaded, or from the Library) and how the shots follow
 *  it: lip-synced to their slice of the song by a song-driven model, or picture with the song laid over. Opens by
 *  itself on a new music-video project; the 🎵 chip in the project header opens it again. */
import { useEffect, useRef, useState } from "react";
import { ask, dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type LipsyncModel, type MusicMode, type MusicVideoSetup, type ProjectDetail } from "./api";
import { lib } from "./library-api";
import { WanGPLink } from "./WanGPLink";

const DISMISSED = "filmlab:music-setup-later:";
export function setupDismissed(projectId: string) {
  try { return localStorage.getItem(DISMISSED + projectId) === "1"; } catch { return false; }
}
function dismiss(projectId: string) {
  try { localStorage.setItem(DISMISSED + projectId, "1"); } catch { /* this visit only */ }
}

const DOWNLOAD_WARNING = "isn't downloaded yet. WanGP downloads its files the first time it generates in this project, "
  + "which can take a while (models are often several GB). You can watch the download on the render, or on WanGP's own page.";
const secs = (s: number | null | undefined) => (s ? `${s.toFixed(1)} s` : "");
type SongSource = "generate" | "upload" | "library";

export function MusicVideoSetupModal({ project, onClose, onSaved }: {
  project: ProjectDetail; onClose: () => void; onSaved: () => void;
}) {
  const [data, setData] = useState<MusicVideoSetup | null>(null);
  const [mode, setMode] = useState<MusicMode>("lipsync");
  const [model, setModel] = useState<string>("");
  const [lyrics, setLyrics] = useState("");
  const [style, setStyle] = useState("");
  const [length, setLength] = useState(project.target_seconds ?? 30);
  const [source, setSource] = useState<SongSource>("generate");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const file = useRef<HTMLInputElement>(null);
  useModalFrame(backdrop);

  const load = async (first = false) => {
    const d = await api.musicVideo(project.id);
    setData(d);
    if (first) {
      setMode(d.mode ?? "lipsync");
      setModel(d.speech_template ?? d.lipsync_models.find((m) => m.suggested)?.template ?? "");
      setLyrics(d.lyrics);
      setStyle(d.style);
      if (d.song) setSource("library");
    }
    return d;
  };
  useEffect(() => { load(true).catch((e) => setError((e as Error).message)); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  // while a song is being generated, look again every few seconds
  const generating = !!data?.song_jobs.some((j) => !["done", "failed", "cancelled", "interrupted"].includes(j.status));
  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => { load().catch(() => undefined); }, 4000);
    return () => clearInterval(t);
  }, [generating]);  // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const close = () => onClose();
  const later = () => { dismiss(project.id); close(); };

  const chosen: LipsyncModel | undefined = data?.lipsync_models.find((m) => m.template === model);
  const songModel = data?.song_models.find((m) => m.template === data.song_model);

  const generate = () => run(async () => {
    if (songModel?.installed === false && !await ask(`${songModel.template} ${DOWNLOAD_WARNING}`, { confirm: "Generate" })) return;
    await api.generateSong(project.id, { lyrics, style, seconds: length });
    await load();
  });
  const upload = (f: File) => run(async () => {
    const { variant_id } = await api.songAsset(project.id);
    const r = await lib.upload(variant_id, f, { caption: "song", source_note: "Uploaded by the owner" });
    setData(await api.updateMusicVideo(project.id, { song_file_id: r.file_id }));
  });
  const choose = (fileId: string) => run(async () => setData(await api.updateMusicVideo(project.id, { song_file_id: fileId })));
  const useCandidate = (id: string) => run(async () => setData(await api.useSongCandidate(project.id, id)));
  const removeCandidate = (id: string) => run(async () => { await lib.discard(id); await load(); });

  const save = () => run(async () => {
    if (mode === "lipsync" && chosen?.installed === false
      && !await ask(`${chosen.template} ${DOWNLOAD_WARNING}`, { confirm: "Use it" })) return;
    await api.updateMusicVideo(project.id, { mode, lyrics, style, ...(mode === "lipsync" && model ? { speech_template: model } : {}) });
    onSaved();
    close();
  });

  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); close(); } else trapTab(e, card.current);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const songs = data ? [...data.song_files.map((f) => ({ file_id: f.file_id, name: f.caption || "Song", duration_s: f.duration_s ?? null })),
    ...data.library_songs.filter((s) => !data.song_files.some((f) => f.file_id === s.file_id))] : [];

  return (
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div ref={card} className="modal glass settings-modal music-setup" role="dialog" aria-modal="true" aria-labelledby="mv-title">
        <header className="sm-head">
          <h3 id="mv-title">🎵 Music video</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={close}>✕</button>
        </header>
        <div className="sm-body">
          <p className="muted small">One song plays from start to end under the whole film, so cuts never restart the music.
            Make the song first; the director then plans the shots to it, line by line.</p>
          {!data && !error && <p className="muted small">Loading WanGP's models…</p>}
          {data && <>
            <section>
              <h4>1 · The song</h4>
              {data.song ? (
                <div className="mv-song">
                  <audio controls preload="none" src={lib.fileUrl(data.song.file_id)} />
                  <span className="small muted">{secs(data.song.duration_s)} · the film's length follows it</span>
                </div>
              ) : <p className="small warn">No song chosen yet.</p>}
              <div className="mv-tabs" role="tablist">
                {(["generate", "upload", "library"] as const).map((s) => (
                  <button key={s} type="button" role="tab" aria-selected={source === s} className={`ghost small ${source === s ? "on" : ""}`}
                    onClick={() => setSource(s)}>{s === "generate" ? "Generate from lyrics" : s === "upload" ? "Upload a track" : "From the Library"}</button>
                ))}
              </div>
              {source === "generate" && (
                <div className="mv-generate">
                  <label className="small">Lyrics <span className="muted">· exactly as sung, with [Verse] / [Chorus] markers; empty for instrumental</span>
                    <textarea rows={6} value={lyrics} maxLength={3000} onChange={(e) => setLyrics(e.target.value)}
                      placeholder={"[Verse]\nLoad up on guns, bring your friends\n…\n[Chorus]\n…"} /></label>
                  <label className="small">Style <span className="muted">· genre, mood, instruments, voice</span>
                    <input value={style} maxLength={500} onChange={(e) => setStyle(e.target.value)}
                      placeholder="90s grunge rock, distorted guitars, raspy male vocals, 117 bpm" /></label>
                  <div className="row">
                    <label className="small">Length (s) <input type="number" min={10} max={240} value={length}
                      onChange={(e) => setLength(Number(e.target.value))} style={{ width: 80 }} /></label>
                    <span className="grow small muted">{data.song_model}{songModel?.installed === false ? " · not downloaded yet" : ""}</span>
                    <button type="button" className="primary small" disabled={busy || style.trim().length < 3}
                      onClick={generate}>{data.song_candidates.length ? "Generate more" : "Generate song"}</button>
                  </div>
                  {generating && <p className="small muted">Generating… the candidates appear here when they're done.</p>}
                  {data.song_jobs.filter((j) => j.status === "failed").slice(0, 1).map((j) => (
                    <p key={j.id} className="small error">The last song didn't generate: {j.error}</p>))}
                  {data.song_candidates.length > 0 && <ul className="mv-list">
                    {data.song_candidates.map((c, i) => (
                      <li key={c.id} className="row">
                        <span className="small">Candidate {i + 1}</span>
                        <audio controls preload="none" src={lib.fileUrl(c.file_id)} className="grow" />
                        <button type="button" className="ghost small" disabled={busy} onClick={() => useCandidate(c.id)}>Use this</button>
                        <button type="button" className="ghost small" disabled={busy} aria-label={`Remove candidate ${i + 1}`}
                          title="Remove this candidate" onClick={() => removeCandidate(c.id)}>✕</button>
                      </li>))}
                  </ul>}
                </div>
              )}
              {source === "upload" && (
                <div className="row">
                  <span className="small muted grow">A WAV, MP3 or FLAC of the finished song. It's added to the Library.</span>
                  <button type="button" className="ghost small" disabled={busy} onClick={() => file.current?.click()}>Choose a file…</button>
                  <input ref={file} type="file" hidden accept="audio/wav,audio/x-wav,audio/mpeg,audio/flac,.wav,.mp3,.flac"
                    onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
                </div>
              )}
              {source === "library" && (songs.length ? (
                <ul className="mv-list">
                  {songs.map((s) => (
                    <li key={s.file_id} className="row">
                      <span className="small grow">{s.name} <span className="muted">{secs(s.duration_s)}</span></span>
                      <audio controls preload="none" src={lib.fileUrl(s.file_id)} />
                      {data.song?.file_id === s.file_id ? <span className="chip s-ready">The song</span>
                        : <button type="button" className="ghost small" disabled={busy} onClick={() => choose(s.file_id)}>Use this</button>}
                    </li>))}
                </ul>) : <p className="small muted">No sounds in the Library yet.</p>)}
            </section>

            <section>
              <h4>2 · How the shots follow it</h4>
              <label className={`mv-method ${mode === "lipsync" ? "on" : ""}`}>
                <input type="radio" name="mv-mode" checked={mode === "lipsync"} onChange={() => setMode("lipsync")} />
                <span><strong>Lip-sync to the song</strong>
                  <span className="block small muted">Each shot is made from its own slice of the song, and the performer sings
                    exactly that part. Closest to a real music video; mouths may not match every syllable.</span></span>
              </label>
              {mode === "lipsync" && (data.lipsync_models.length ? (
                <ul className="mv-models">
                  {data.lipsync_models.map((m) => (
                    <li key={m.template}>
                      <label className="row">
                        <input type="radio" name="mv-model" checked={model === m.template} onChange={() => setModel(m.template)} />
                        <span className="grow"><strong className="small">{m.template}</strong>
                          {m.suggested && <span className="chip s-ready">Suggested</span>}
                          {m.installed === false ? <span className="chip warn-chip">Not downloaded</span>
                            : <span className="chip">Downloaded</span>}
                          <span className="block small muted">{m.note}</span></span>
                      </label>
                    </li>))}
                </ul>) : <p className="small warn">WanGP lists no song-driven video models. Refresh the models in Settings, or
                  use the other method.</p>)}
              {mode === "lipsync" && chosen?.installed === false && (
                <p className="small warn">{chosen.template} {DOWNLOAD_WARNING} <WanGPLink label="Open WanGP ↗" /></p>)}
              <label className={`mv-method ${mode === "soundtrack" ? "on" : ""}`}>
                <input type="radio" name="mv-mode" checked={mode === "soundtrack"} onChange={() => setMode("soundtrack")} />
                <span><strong>Picture with one soundtrack</strong>
                  <span className="block small muted">Shots are made with your usual video models and their own sound is muted;
                    the song plays over the whole film. Works with any model and is faster, but mouths only roughly match.</span></span>
              </label>
            </section>
          </>}
        </div>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="row">
            <button type="button" className="ghost" onClick={later}>Not now</button>
            <span className="grow" />
            <button type="button" className="primary" disabled={busy || !data || (mode === "lipsync" && !model)} onClick={save}>
              Save</button>
          </div>
        </footer>
      </div>
    </div>
  );
}
