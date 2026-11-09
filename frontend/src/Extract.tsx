/** 📸 Extract: make a Library asset (character, location, prop or style) from a frame of a take, an uploaded
 *  picture or a Library image. Scrub to the frame, grab it, drag a selection to crop, then add it as it is or let a
 *  WanGP model that takes reference pictures rework it first (its candidates are kept on the asset's page). */
import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { ask } from "./Dialog";
import { api, type Project, type ProjectDetail, type Take } from "./api";
import { ImagePicker, type Pick } from "./ImagePicker";
import { type AssetKind, type Capabilities, type Crop, type Frame, KIND_LABEL, lib, templateLabel } from "./library-api";

type Kind = Exclude<AssetKind, "sound">;
const KINDS: Kind[] = ["character", "location", "prop", "style"];
/** What to ask the model for, per kind: a clean reference picture of the same subject. */
const PRESET: Record<Kind, string> = {
  character: "The same person as in the picture, full body, standing in a relaxed neutral pose and facing the camera; "
    + "same face, hair, body and outfit; plain light grey background, soft even studio light.",
  location: "The same place as in the picture, empty: no people. Same architecture, materials, colours and light, "
    + "seen in a wide view.",
  prop: "The same object as in the picture, alone and centered on a plain white background; same shape, materials, "
    + "colours and wear; soft studio light.",
  style: "A new scene in exactly the visual style of the picture: same rendering, palette, line work, texture and "
    + "lighting, with a different subject.",
};
const SIZE: Record<Kind, string> = { character: "832x1216", location: "1216x832", prop: "1024x1024", style: "1216x832" };
const SIZES = ["1024x1024", "832x1216", "1216x832", "720x1280", "1280x720"];

type Source =
  | { type: "take"; media_id: string; label: string; fps: number; duration: number }
  | { type: "picture"; frame: Frame; label: string };

function params(): Record<string, string> {
  const q = location.hash.split("?")[1] ?? "";
  return Object.fromEntries(new URLSearchParams(q));
}

export function ExtractPage() {
  const start = useMemo(params, []);
  const [source, setSource] = useState<Source | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [crop, setCrop] = useState<Crop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (what: string, fn: () => Promise<void>) => {
    setError(null); setBusy(what);
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  };
  const choose = (s: Source) => {
    setSource(s); setCrop(null);
    setFrame(s.type === "picture" ? s.frame : null);
  };

  return (
    <main className="page extract-page">
      <div className="ex-head">
        <h1>📸 Extract to the Library</h1>
        <p className="muted small">Grab a frame of a take (or use a picture), select the character, place or thing, then
          add it as a new Library asset as it is, or let a WanGP model rework it first: full body on a plain background,
          the place without people, the object alone…</p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="ex-cols">
        <section className="panel ex-source">
          <h2>1 · Source</h2>
          <SourcePicker start={start} onChoose={choose} run={run} />
        </section>
        <section className="panel ex-frame">
          <h2>2 · Frame and selection</h2>
          {!source && <p className="muted">Pick a take, upload a picture or choose one from the Library.</p>}
          {source?.type === "take" && (
            <TakeScrubber key={source.media_id} source={source} startAt={Number(start.t) || 0} busy={!!busy}
              onGrab={(t) => run("Grabbing the frame…", async () => {
                setCrop(null);
                setFrame(await lib.grabFrame({ media_id: source.media_id, t }));
              })} />)}
          {frame && (
            <>
              <CropBox src={lib.fileUrl(frame.file_id)} crop={crop} onChange={setCrop} />
              <div className="row small">
                <span className="muted grow">{frame.width}×{frame.height}
                  {crop ? ` · selection ${Math.round(crop.w * frame.width)}×${Math.round(crop.h * frame.height)}` : " · drag on the picture to select a part"}</span>
                {crop && <button className="ghost small" onClick={() => setCrop(null)}>Use the whole picture</button>}
              </div>
            </>
          )}
        </section>
        <section className="panel ex-create">
          <h2>3 · New asset</h2>
          {frame ? <CreateForm frame={frame} crop={crop} busy={busy} run={run} />
            : <p className="muted">Grab a frame first.</p>}
        </section>
      </div>
      {busy && <p className="small muted" role="status">{busy}</p>}
    </main>
  );
}

function SourcePicker({ start, onChoose, run }: {
  start: Record<string, string>; onChoose: (s: Source) => void; run: (w: string, fn: () => Promise<void>) => Promise<void>;
}) {
  const [tab, setTab] = useState<"take" | "upload" | "library">("take");
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState(start.project ?? "");
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [picked, setPicked] = useState<string | null>(start.media ?? null);
  const [picks, setPicks] = useState<Pick[]>([]);
  useEffect(() => { api.projects().then((ps) => { setProjects(ps); if (!pid && ps[0]) setPid(ps[0].id); }).catch(() => setProjects([])); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (pid) api.project(pid).then(setProject).catch(() => setProject(null)); }, [pid]);
  const takeLabel = (t: Take) => {
    const s = project?.shots.find((x) => x.id === t.shot_id);
    return s ? `${s.position}. ${s.title}` : "take";
  };
  const pickTake = (t: Take) => {
    setPicked(t.media_id);
    onChoose({ type: "take", media_id: t.media_id, label: `${project?.title} · ${takeLabel(t)}`, fps: t.fps || 24, duration: t.duration_s });
  };
  // Arrived from a take's "Extract to Library": open it straight away.
  useEffect(() => {
    if (!start.media || !project) return;
    const t = project.takes.find((x) => x.media_id === start.media);
    if (t) pickTake(t);
  }, [project]);  // eslint-disable-line react-hooks/exhaustive-deps

  const takes = (project?.takes ?? []).slice().sort((a, b) => {
    const pa = project?.shots.find((s) => s.id === a.shot_id)?.position ?? 0;
    const pb = project?.shots.find((s) => s.id === b.shot_id)?.position ?? 0;
    return pa - pb || a.created_at.localeCompare(b.created_at);
  });
  return (
    <div className="ex-picker">
      <div className="seg-toggle" role="tablist">
        {([["take", "A take"], ["upload", "Upload"], ["library", "Library image"]] as const).map(([k, v]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={`ghost small ${tab === k ? "on" : ""}`}
            onClick={() => setTab(k)}>{v}</button>))}
      </div>
      {tab === "take" && <>
        <label className="small">Project
          <select value={pid} onChange={(e) => setPid(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
        </label>
        {project && !takes.length && <p className="muted small">No takes in this project yet.</p>}
        <ul className="ex-takes">
          {takes.map((t) => (
            <li key={t.id}>
              <button type="button" className={`ex-take ${picked === t.media_id ? "on" : ""}`} onClick={() => pickTake(t)}
                title={`${takeLabel(t)} · ${t.width}×${t.height} · ${t.duration_s.toFixed(1)} s`}>
                {t.thumb_media_id ? <img src={api.mediaUrl(t.thumb_media_id)} alt="" loading="lazy" /> : <span className="ex-nothumb">▶</span>}
                <span className="small">{takeLabel(t)}</span>
              </button>
            </li>))}
        </ul>
      </>}
      {tab === "upload" && (
        <label className="st-drop" onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) run("Uploading…", async () => onChoose({ type: "picture", frame: await lib.uploadFrame(f), label: f.name })); }}>
          <input type="file" accept="image/png,image/jpeg,image/webp" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) run("Uploading…", async () => onChoose({ type: "picture", frame: await lib.uploadFrame(f), label: f.name })); }} />
          <strong>Choose or drop a picture</strong>
          <span className="block small muted">PNG, JPEG or WebP · a still, a screenshot, a concept sketch…</span>
        </label>
      )}
      {tab === "library" && (
        <ImagePicker picks={picks} max={1} hint="Pick one image to crop or rework into a new asset."
          onChange={(p) => {
            setPicks(p);
            if (p[0]) run("Opening…", async () => {
              // The Library's own file, as it is (its size from the browser): no copy unless it gets cropped.
              const img = new Image();
              img.src = lib.fileUrl(p[0].file_id);
              await img.decode().catch(() => undefined);
              onChoose({ type: "picture", label: p[0].label, frame: { file_id: p[0].file_id, width: img.naturalWidth,
                height: img.naturalHeight, source_note: `From the Library: ${p[0].label}` } });
            });
          }} />
      )}
    </div>
  );
}

function TakeScrubber({ source, startAt, busy, onGrab }: {
  source: Extract<Source, { type: "take" }>; startAt: number; busy: boolean; onGrab: (t: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(startAt);
  const step = (frames: number) => {
    const v = video.current;
    if (!v) return;
    v.pause();
    v.currentTime = Math.min(Math.max(0, v.currentTime + frames / source.fps), Math.max(0, (v.duration || source.duration) - 0.01));
  };
  return (
    <div className="ex-scrub">
      <video ref={video} className="player" controls preload="auto" src={api.mediaUrl(source.media_id)}
        onLoadedMetadata={(e) => { if (startAt) e.currentTarget.currentTime = startAt; }}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onSeeked={(e) => setT(e.currentTarget.currentTime)} />
      <span className="muted small ex-where">{source.label} · {t.toFixed(2)} s · frame {Math.round(t * source.fps) + 1}</span>
      <div className="row small">
        <button type="button" className="ghost small" title="Back one frame" onClick={() => step(-1)}>◀ 1 frame</button>
        <button type="button" className="ghost small" title="Forward one frame" onClick={() => step(1)}>1 frame ▶</button>
        <span className="grow" />
        <button type="button" className="primary small" disabled={busy}
          onClick={() => { video.current?.pause(); onGrab(video.current?.currentTime ?? t); }}>📸 Grab this frame</button>
      </div>
    </div>
  );
}

/** The picture with a selection you drag; the rest is dimmed. The selection is in fractions of the picture. */
function CropBox({ src, crop, onChange }: { src: string; crop: Crop | null; onChange: (c: Crop | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const from = useRef<{ x: number; y: number } | null>(null);
  const at = (e: ReactPointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const update = (e: ReactPointerEvent) => {
    if (!from.current) return;
    const p = at(e), a = from.current;
    onChange({ x: Math.min(a.x, p.x), y: Math.min(a.y, p.y), w: Math.abs(p.x - a.x), h: Math.abs(p.y - a.y) });
  };
  return (
    <div ref={box} className="crop-box" onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); from.current = at(e); onChange(null); }}
      onPointerMove={update}
      onPointerUp={(e) => { update(e); from.current = null; if (crop && (crop.w < 0.02 || crop.h < 0.02)) onChange(null); }}>
      <img src={src} alt="The grabbed frame" draggable={false} />
      {crop && <div className="crop-sel" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.w * 100}%`, height: `${crop.h * 100}%` }} />}
    </div>
  );
}

function CreateForm({ frame, crop, busy, run }: {
  frame: Frame; crop: Crop | null; busy: string | null; run: (w: string, fn: () => Promise<void>) => Promise<void>;
}) {
  const [kind, setKind] = useState<Kind>("character");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rework, setRework] = useState(false);
  const [prompt, setPrompt] = useState(PRESET.character);
  const [size, setSize] = useState(SIZE.character);
  const [count, setCount] = useState(2);
  const [template, setTemplate] = useState("");
  const [caps, setCaps] = useState<Capabilities | null>(null);
  useEffect(() => { lib.capabilities().then(setCaps).catch(() => setCaps(null)); }, []);
  const models = (caps?.templates ?? []).filter((c) => (c.task === "gen_image" || c.task === "edit_image") && c.refs !== "none");
  const model = template || caps?.settings.image_refs || "";
  const pickKind = (k: Kind) => {
    if (prompt === PRESET[kind]) setPrompt(PRESET[k]);  // follow the kind until the owner writes their own
    if (size === SIZE[kind]) setSize(SIZE[k]);
    setKind(k);
  };
  const create = () => run(rework ? "Creating the asset and starting the model…" : "Creating the asset…", async () => {
    if (rework && !await ask(`Make ${count} candidate${count === 1 ? "" : "s"} with ${model} from this picture? This uses the GPU.`)) return;
    const pic = crop ? await lib.grabFrame({ file_id: frame.file_id, crop }) : frame;
    const out = await lib.extract({
      file_id: pic.file_id, kind, name: name.trim(), description: description.trim(),
      source_note: frame.source_note + (crop ? " (cropped)" : ""),
      ...(rework ? { edit: { prompt, template: model || undefined, count, resolution: size } } : {}),
    });
    if (out.error) await ask(out.error, { title: "Asset made", confirm: "Open it" });
    location.hash = `#/library/${out.asset_id}`;
  });
  return (
    <div className="ex-form">
      <div className="seg-toggle" role="radiogroup" aria-label="Kind">
        {KINDS.map((k) => <button key={k} type="button" className={`ghost small ${kind === k ? "on" : ""}`} aria-pressed={kind === k}
          onClick={() => pickKind(k)}>{KIND_LABEL[k] ?? k}</button>)}
      </div>
      <label className="small">Name<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)}
        placeholder={kind === "character" ? "e.g. Chrome Rider" : kind === "location" ? "e.g. Temple of Ash" : kind === "prop" ? "e.g. Plasma sword" : "e.g. 80s airbrush"} /></label>
      <label className="small">Description <span className="muted">· optional: helps the director pick it</span>
        <textarea rows={2} value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} /></label>
      <div className="ex-mode">
        <label className={`np-option ${!rework ? "on" : ""}`}><input type="radio" checked={!rework} onChange={() => setRework(false)} />
          <span><strong>Add it as it is</strong><span className="block muted small">The {crop ? "selection" : "picture"} becomes the asset's look.</span></span></label>
        <label className={`np-option ${rework ? "on" : ""}`}><input type="radio" checked={rework} onChange={() => setRework(true)} />
          <span><strong>Rework it with a model first</strong><span className="block muted small">A WanGP model that takes reference
            pictures makes new ones from it; keep the best on the asset's page. The original stays as its “source frame” look.</span></span></label>
      </div>
      {rework && (
        <div className="ex-rework">
          <label className="small">What to make from it
            <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
          <div className="row small">
            <label className="grow">Model
              <select value={model} onChange={(e) => setTemplate(e.target.value)}>
                {models.map((c) => <option key={c.task + c.template} value={c.template} disabled={!c.valid}>{templateLabel(c)}</option>)}
              </select>
            </label>
            <label>Size<select value={size} onChange={(e) => setSize(e.target.value)}>{SIZES.map((s) => <option key={s}>{s}</option>)}</select></label>
            <label>Candidates<select value={count} onChange={(e) => setCount(Number(e.target.value))}>{[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select></label>
          </div>
        </div>
      )}
      <p className="muted small">{frame.source_note}{crop ? " (cropped)" : ""}</p>
      <div className="row">
        <span className="grow" />
        <a className="ghost button-link" href="#/library">Cancel</a>
        <button className="primary" disabled={!!busy || !name.trim() || (rework && prompt.trim().length < 3)} onClick={create}>
          {rework ? "Create and rework" : "Create asset"}</button>
      </div>
    </div>
  );
}
