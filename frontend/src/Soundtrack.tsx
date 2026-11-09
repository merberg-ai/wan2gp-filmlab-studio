/** 🎵 Add soundtrack: upload an MP3/WAV/FLAC (it's kept in the Library as a Sound) or pick a Sound from the Library,
 *  then set its level, where it starts, fades, looping, ducking and whether the shots keep their own sound. It is a
 *  music track of the edit (backend/app/edit.py), so the preview, the draft and the final export all play it. */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type Music, type Timeline } from "./api";
import { type Asset, type AssetDetail, lib } from "./library-api";
import { useUploads } from "./uploads";

const ACCEPT = ".mp3,.wav,.flac,audio/mpeg,audio/wav,audio/x-wav,audio/flac";
const MAX_MB = 150;
const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const pct = (db: number) => Math.round(Math.pow(10, db / 20) * 100);

export const NEW_TRACK: Omit<Music, "file_id"> = {
  start_s: 0, offset_s: 0, gain_db: -6, fade_in_s: 1, fade_out_s: 3, duck: true, loop: false,
};

type LibPick = { file_id: string; label: string; duration_s: number | null };

export function SoundtrackModal({ tl, editing, onClose, onSaved }: {
  tl: Timeline;
  /** The track being changed (its index in the edit's music), or null to add one. */
  editing: number | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const current = editing === null ? null : tl.doc.music[editing];
  const [source, setSource] = useState<"upload" | "library">(current ? "library" : "upload");
  const [file, setFile] = useState<File | null>(null);
  const [localUrl, setLocalUrl] = useState<string | null>(null);
  const [sounds, setSounds] = useState<Asset[] | null>(null);
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [pick, setPick] = useState<LibPick | null>(current ? { file_id: current.file_id, label: "the current soundtrack", duration_s: null } : null);
  const [t, setT] = useState<Omit<Music, "file_id">>(current ? { ...NEW_TRACK, ...current } : NEW_TRACK);
  const [muteShots, setMuteShots] = useState(!!tl.doc.settings.mute_shots);
  const [length, setLength] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploads = useUploads();
  const sending = file && uploads.find((u) => u.name === file.name);

  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const player = useRef<HTMLAudioElement>(null);
  useModalFrame(backdrop);
  const close = () => { if (!busy) onClose(); };
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); close(); } else trapTab(e, card.current);
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
  useEffect(() => {
    if (source === "library" && sounds === null) lib.search("", "sound").then(setSounds).catch(() => setSounds([]));
  }, [source, sounds]);
  useEffect(() => () => { if (localUrl) URL.revokeObjectURL(localUrl); }, [localUrl]);
  // The preview player follows the level (a browser can't play louder than the file: above 0 dB stays at 100 %).
  useEffect(() => { if (player.current) player.current.volume = Math.min(1, Math.pow(10, t.gain_db / 20)); }, [t.gain_db, localUrl, pick]);

  const chooseFile = (f: File | undefined) => {
    setError(null);
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) { setError(`${f.name} is larger than ${MAX_MB} MB.`); return; }
    if (localUrl) URL.revokeObjectURL(localUrl);
    setFile(f); setLocalUrl(URL.createObjectURL(f)); setLength(null);
  };
  const src = source === "upload" ? localUrl : pick && lib.fileUrl(pick.file_id);
  const ready = source === "upload" ? !!file : !!pick;
  const film = tl.total_s;
  const set = (c: Partial<Omit<Music, "file_id">>) => setT((x) => ({ ...x, ...c }));
  const full = tl.doc.music.length >= 2 && editing === null;

  const confirm = async () => {
    if (!ready || busy) return;
    setBusy(true); setError(null);
    try {
      let fileId = pick?.file_id ?? "";
      if (source === "upload" && file) {
        const name = file.name.replace(/\.[^.]+$/, "").slice(0, 100) || "Soundtrack";
        const a = await lib.createAsset({ kind: "sound", name, tags: "soundtrack", description: "Soundtrack uploaded in Edit & export" });
        const variantId = a.variants?.[0]?.id ?? (await lib.createVariant(a.id, "Main")).id;
        fileId = (await lib.upload(variantId, file, { caption: "soundtrack" })).file_id;
      }
      const track: Music = { ...t, file_id: fileId };
      const music = editing === null ? [...tl.doc.music, track] : tl.doc.music.map((m, i) => (i === editing ? { ...m, ...track } : m));
      await api.patchEdit(tl.project_id, {
        music, settings: { ...tl.doc.settings, mute_shots: muteShots },
        note: editing === null ? "added a soundtrack" : "changed the soundtrack",
      });
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return createPortal(
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div ref={card} className="modal glass settings-modal soundtrack" role="dialog" aria-modal="true" aria-labelledby="st-title">
        <header className="sm-head">
          <h3 id="st-title">🎵 {editing === null ? "Add soundtrack" : "Soundtrack"}</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={close} disabled={busy}>✕</button>
        </header>
        <div className="sm-body">
          {full && <p className="warn small">The film already has 2 music tracks: remove one first.</p>}
          <div className="seg-toggle" role="radiogroup" aria-label="Soundtrack from">
            <button type="button" className={`ghost small ${source === "upload" ? "on" : ""}`} aria-pressed={source === "upload"}
              onClick={() => setSource("upload")} disabled={busy}>Upload a file</button>
            <button type="button" className={`ghost small ${source === "library" ? "on" : ""}`} aria-pressed={source === "library"}
              onClick={() => setSource("library")} disabled={busy}>From the Library</button>
          </div>

          {source === "upload" ? (
            <label className={`st-drop ${file ? "has" : ""}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); chooseFile(e.dataTransfer.files[0]); }}>
              <input type="file" accept={ACCEPT} hidden onChange={(e) => chooseFile(e.target.files?.[0])} disabled={busy} />
              {file ? <><strong>{file.name}</strong><span className="block small muted">{(file.size / 1048576).toFixed(1)} MB · click to choose another</span></>
                : <><strong>Choose or drop a song</strong><span className="block small muted">MP3, WAV or FLAC · up to {MAX_MB} MB · it's kept in the Library as a Sound</span></>}
            </label>
          ) : (
            <div className="st-lib">
              {sounds === null && <p className="muted small">Loading the Library…</p>}
              {sounds?.length === 0 && <p className="muted small">No sounds in the Library yet: upload a file instead.</p>}
              <div className="row wrap">{sounds?.map((a) => (
                <button key={a.id} type="button" className={`ghost small ${asset?.id === a.id ? "on" : ""}`}
                  onClick={() => lib.asset(a.id).then(setAsset).catch((e) => setError((e as Error).message))}>{a.name}</button>))}</div>
              {asset && <div className="st-files">{asset.variants.flatMap((v) => v.files.filter((f) => f.kind === "audio").map((f) => (
                <label key={f.entry_id} className="ea-check small">
                  <input type="radio" name="st-file" checked={pick?.file_id === f.file_id}
                    onChange={() => { setPick({ file_id: f.file_id, label: `${asset.name} · ${f.caption || "audio"}`, duration_s: f.duration_s }); setLength(null); }} />
                  {asset.name} · {f.caption || "audio"}{f.duration_s ? ` · ${fmt(f.duration_s)}` : ""}
                </label>)))}</div>}
            </div>
          )}

          {src && <audio ref={player} className="st-player" controls preload="metadata" src={src}
            onLoadedMetadata={(e) => setLength(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : null)} />}
          {length !== null && (
            <p className="small muted">The song is {fmt(length)}; the film is {fmt(film)}.
              {length - t.offset_s < film - t.start_s && !t.loop ? <span className="warn"> It ends before the film does: turn on Loop to fill it.</span> : null}</p>)}

          <label className="st-volume">Volume <span className="muted small">{t.gain_db > 0 ? "+" : ""}{t.gain_db} dB · {pct(t.gain_db)} %</span>
            <input type="range" min={-30} max={6} step={1} value={t.gain_db} onChange={(e) => set({ gain_db: Number(e.target.value) })} />
          </label>
          <div className="st-grid small">
            <label>Starts at (s in the film)<input type="number" min={0} max={3600} step={0.1} value={t.start_s} onChange={(e) => set({ start_s: Math.max(0, Number(e.target.value) || 0) })} /></label>
            <label>Play from (s into the song)<input type="number" min={0} max={3600} step={0.1} value={t.offset_s} onChange={(e) => set({ offset_s: Math.max(0, Number(e.target.value) || 0) })} /></label>
            <label>Fade in (s)<input type="number" min={0} max={30} step={0.5} value={t.fade_in_s} onChange={(e) => set({ fade_in_s: Math.min(30, Math.max(0, Number(e.target.value) || 0)) })} /></label>
            <label>Fade out (s)<input type="number" min={0} max={30} step={0.5} value={t.fade_out_s} onChange={(e) => set({ fade_out_s: Math.min(30, Math.max(0, Number(e.target.value) || 0)) })} /></label>
          </div>
          <label className="check small"><input type="checkbox" className="switch" checked={!!t.loop} onChange={(e) => set({ loop: e.target.checked })} />
            Loop to the end of the film</label>
          <label className="check small"><input type="checkbox" className="switch" checked={muteShots} onChange={(e) => setMuteShots(e.target.checked)} />
            Soundtrack only: mute every shot's own sound</label>
          <label className="check small"><input type="checkbox" className="switch" checked={t.duck} disabled={muteShots} onChange={(e) => set({ duck: e.target.checked })} />
            Duck under dialogue <span className="muted">(lower it while a shot has its own sound)</span></label>
          {sending && <progress max={sending.total} value={sending.loaded} aria-label="uploading" />}
        </div>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="row modal-actions">
            <span className="grow" />
            <button type="button" className="ghost" onClick={close} disabled={busy}>Cancel</button>
            <button type="button" className="primary" disabled={!ready || busy || full} onClick={confirm}>
              {busy ? (sending ? "Uploading…" : "Saving…") : editing === null ? "Add soundtrack" : "Save"}</button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
