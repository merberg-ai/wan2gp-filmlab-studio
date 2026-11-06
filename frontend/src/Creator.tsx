/** The Creator: describe a character, location, prop, style or sound; the director drafts it; you generate its
 *  images (and voice or music), keep the ones you want, and approve it into the library (backend/app/creator.py). */
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ask } from "./Dialog";
import type { Listener } from "./App";
import { ImageJobs } from "./Library";
import {
  type AssetDetail, type AssetKind, type Capability, type Creation, type CreationPlan, KIND_LABEL, KINDS, lib,
  type TemplateSettings,
} from "./library-api";

type Subscribe = (l: Listener, resync: () => void) => () => void;
type Act = (fn: () => Promise<unknown>) => Promise<void>;
const ACTIVE = new Set(["submitting", "queued", "running", "cancelling", "restored"]);
const HINT: Record<AssetKind, string> = {
  character: "Who they are and how they look: age, build, face, hair, clothing, personality, how they sound",
  location: "The place: what it is, its era, layout, materials, light, time of day, weather",
  prop: "The object: what it is, size, materials, colours, wear, what it's used for",
  style: "The look: medium (e.g. watercolour, 35 mm film), palette, light, texture, era",
  sound: "The music: genre, instruments, mood, tempo, where it plays in the film; lyrics if it has words",
};

const usable = (t: Capability) => t.valid && t.installed !== false;

/** Left column of the Library: start a creation, and the ones in progress. */
export function CreatorPanel({ subscribe, current }: { subscribe: Subscribe; current: string | null }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<Creation[]>([]);
  const load = useCallback(() => { lib.creations().then(setList).catch(() => setList([])); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => subscribe((e) => { if (e.type === "creation.updated") load(); }, load), [subscribe, load]);
  return (
    <div className="creator-panel">
      {open ? <CreateForm onClose={() => setOpen(false)} /> : (
        <button className="primary create-btn" onClick={() => setOpen(true)}
          title="Describe it; the director drafts it and WanGP makes its reference images">✨ Create with the director…</button>
      )}
      {list.length > 0 && (
        <ul className="creation-list">
          {list.map((c) => (
            <li key={c.id}>
              <a className={`asset-row ${c.id === current ? "selected" : ""}`} href={`#/library/create/${c.id}`}>
                <span className="noimg creating" aria-hidden>✨</span>
                <span><strong>{c.asset?.name ?? "New " + c.kind}</strong>
                  <span className="block muted small">{KIND_LABEL[c.kind]} · {c.status === "drafting" ? "director is drafting…" : "draft, not in the library yet"}</span></span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CreateForm({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<AssetKind>("character");
  const [brief, setBrief] = useState("");
  const [voice, setVoice] = useState(true);
  const [caps, setCaps] = useState<{ templates: Capability[]; settings: TemplateSettings } | null>(null);
  const [image, setImage] = useState("");
  const [edit, setEdit] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    lib.capabilities().then((c) => { setCaps(c); setImage(c.settings.image_text); setEdit(c.settings.image_refs); })
      .catch(() => setCaps(null));
  }, []);
  const gen = caps?.templates.filter((t) => t.task === "gen_image") ?? [];
  const edits = caps?.templates.filter((t) => t.task === "edit_image" && t.refs !== "none") ?? [];
  const voiceOk = caps?.templates.some((t) => t.task === "gen_speech_from_description" && usable(t));
  const musicOk = caps?.templates.some((t) => t.task === "gen_song" && usable(t));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const c = await lib.startCreation({ kind, brief: brief.trim(), image_template: kind === "sound" ? undefined : image,
        edit_template: kind === "sound" ? undefined : edit, voice: kind === "character" && voice && !!voiceOk });
      location.hash = `#/library/create/${c.id}`;
      onClose();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const option = (t: Capability) => (
    <option key={t.template} value={t.template} disabled={!usable(t)}>
      {t.template}{!t.valid ? " (not usable)" : t.installed === false ? " (not downloaded in WanGP)" : ""}</option>
  );
  return (
    <form className="card create-form" onSubmit={submit}>
      <div className="row"><strong className="grow">Create with the director</strong>
        <button type="button" className="ghost small" onClick={onClose} aria-label="Close">✕</button></div>
      <div className="row kind-chips" role="radiogroup" aria-label="Kind">
        {KINDS.map((k) => (
          <button type="button" key={k} role="radio" aria-checked={kind === k} className={`ghost small ${kind === k ? "on" : ""}`}
            onClick={() => setKind(k)}>{KIND_LABEL[k]}{k === "sound" ? " (music)" : ""}</button>
        ))}
      </div>
      <textarea rows={4} placeholder={HINT[kind]} value={brief} maxLength={4000} onChange={(e) => setBrief(e.target.value)}
        aria-label="Describe it" />
      {kind !== "sound" ? <>
        <label className="small">First image (from text)
          <select value={image} onChange={(e) => setImage(e.target.value)}>{gen.map(option)}</select></label>
        <label className="small">Other views (edits of the first, so it stays the same {kind})
          <select value={edit} onChange={(e) => setEdit(e.target.value)}>{edits.map(option)}</select></label>
      </> : !musicOk && <p className="small warn">No music model is ready in WanGP (e.g. ACE-Step); the music step won't run.</p>}
      {kind === "character" && (
        <label className="check small" title={voiceOk ? "" : "No voice-design model is ready in WanGP (e.g. Qwen3 1.7B)"}>
          <input type="checkbox" checked={voice && !!voiceOk} disabled={!voiceOk} onChange={(e) => setVoice(e.target.checked)} />
          Design a voice too{!voiceOk ? " (no voice model ready in WanGP)" : ""}</label>
      )}
      {error && <p className="error small">{error}</p>}
      <div className="actions"><button className="primary" disabled={busy || brief.trim().length < 10}>
        {busy ? "Starting…" : "Ask the director to draft it"}</button></div>
      <p className="small muted">The director writes a draft: name, what must stay the same, and the images to make. Nothing uses
        the GPU until you press Generate, and nothing is added to the library until you approve it.</p>
    </form>
  );
}

/** One creation: the director's draft (editable), each part to generate, the candidates to keep, approve. */
export function CreationView({ id, subscribe }: { id: string; subscribe: Subscribe }) {
  const [c, setC] = useState<Creation | null>(null);
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number>();
  const load = useCallback(async () => {
    try {
      const x = await lib.creation(id);
      setC(x);
      setAsset(x.status === "discarded" ? null : await lib.asset(x.asset_id).catch(() => null));
    } catch (e) { setError((e as Error).message); }
  }, [id]);
  const soon = useCallback(() => { window.clearTimeout(timer.current); timer.current = window.setTimeout(load, 120); }, [load]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => subscribe((e) => {
    const p = e.payload as { creation_id?: string; asset_id?: string };
    if ((e.type === "creation.updated" && p.creation_id === id) || ((e.type === "library.updated" || e.type.startsWith("image."))
      && p.asset_id === c?.asset_id)) soon();
  }, load), [subscribe, load, soon, id, c?.asset_id]);

  const act: Act = async (fn) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
    soon();
  };
  if (!c) return <section className="panel">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</section>;
  if (c.status === "approved") {
    return <section className="panel"><p>“{c.plan?.name}” is in the library. <a className="link" href={`#/library/${c.asset_id}`}>Open it</a></p></section>;
  }
  if (c.status === "discarded") return <section className="panel"><p className="muted">Discarded: it's in Recently deleted.</p></section>;

  const files = asset?.variants.flatMap((v) => v.files) ?? [];
  const images = files.filter((f) => f.kind === "image");
  const audio = files.filter((f) => f.kind === "audio");
  const cands = asset?.images.candidates ?? [];
  const jobs = asset?.images.jobs ?? [];
  const running = (label: string) => jobs.some((j) => ACTIVE.has(j.status) && j.label === label);
  const plan = c.plan;
  const anchorLabel = plan?.views[0]?.label ?? "";
  const viewLabels = plan?.views.slice(1).map((v) => v.label) ?? [];
  const canApprove = c.kind === "sound" ? audio.length > 0 : images.length > 0;
  const generate = (part: "anchor" | "views" | "voice" | "music", what: string) => act(async () => {
    if (await ask(`Generate ${what} now? This uses the GPU.`, { confirm: "Generate" })) await lib.generateCreation(c.id, part);
  });

  return (
    <section className="panel creation-view">
      <div className="row">
        <span className="chip s-approved">{KIND_LABEL[c.kind]}</span>
        <h2 className="grow creation-title">{plan?.name ?? "New " + c.kind} <span className="muted small">· draft</span></h2>
        <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask("Discard this draft? It goes to Recently deleted with anything generated for it.")) await lib.discardCreation(c.id);
        })}>Discard…</button>
        <button className="primary" disabled={!canApprove} title={canApprove ? "Add it to the library" : "Keep at least one generated result first"}
          onClick={() => act(async () => {
            const done = await lib.approveCreation(c.id);
            location.hash = `#/library/${done.asset_id}`;
          })}>Approve into the library</button>
      </div>
      <p className="small muted">Your description: “{c.brief}”</p>
      {error && <p className="error" role="alert">{error}</p>}
      {c.status === "drafting" && <div className="activity"><div className="activity-now"><span className="pulse" aria-hidden />
        The director is drafting it…</div></div>}
      {c.error && c.status === "ready" && (
        <div className="card"><p className="error small">The director didn't finish the draft: {c.error}</p>
          <button className="ghost small" onClick={() => act(() => lib.redraftCreation(c.id))}>Ask again</button></div>
      )}
      {plan && <PlanEditor kind={c.kind} plan={plan} act={act} onSave={(p) => lib.updateCreationPlan(c.id, p)}
        onRedraft={() => act(() => lib.redraftCreation(c.id, !!plan.voice))} />}
      <ImageJobs jobs={jobs} act={act} />

      {plan && c.kind !== "sound" && <>
        <Step n={1} title={`Anchor: ${anchorLabel}`} note={`Made from text with ${c.image_template}. Keep the one that looks right: every other view is made from it.`}>
          <button className="primary small" disabled={running(anchorLabel)} onClick={() => generate("anchor", `the ${anchorLabel}`)}>
            {images.length ? "Generate more" : "Generate"}</button>
          <Candidates cands={cands.filter((x) => x.label === anchorLabel)} act={act} />
        </Step>
        {viewLabels.length > 0 && (
          <Step n={2} title={`Other views: ${viewLabels.join(", ")}`} note={`Edits of the kept anchor with ${c.edit_template}, so it stays the same ${c.kind}.`}>
            <button className="primary small" disabled={!images.length || viewLabels.some(running)}
              title={images.length ? "" : "Keep an anchor image first"} onClick={() => generate("views", viewLabels.join(", "))}>Generate</button>
            <Candidates cands={cands.filter((x) => x.label && viewLabels.includes(x.label))} act={act} />
          </Step>
        )}
      </>}
      {plan?.voice && (
        <Step n={viewLabels.length ? 3 : 2} title="Voice" note={`“${plan.voice.description}”, saying: “${plan.voice.line}”`}>
          <button className="primary small" disabled={running("voice")} onClick={() => generate("voice", "the voice")}>
            {asset?.voice ? "Generate again" : "Generate"}</button>
          {asset?.voice && <p className="small ok-text">Voice kept ({asset.voice.duration_s?.toFixed(1)} s)
            <audio controls preload="none" src={lib.fileUrl(asset.voice.file_id)} className="voice-player" /></p>}
          <Candidates cands={cands.filter((x) => x.label === "voice")} act={act} />
        </Step>
      )}
      {plan?.music && (
        <Step n={1} title="Music" note={`${plan.music.style} · ${plan.music.seconds} s${plan.music.lyrics ? " · with lyrics" : " · instrumental"}`}>
          <button className="primary small" disabled={running("music")} onClick={() => generate("music", "the music")}>Generate</button>
          <Candidates cands={cands.filter((x) => x.label === "music")} act={act} />
        </Step>
      )}
      {(images.length > 0 || audio.length > 0) && (
        <div className="card">
          <h2>Kept · goes into the library</h2>
          <div className="candidates">
            {images.map((f) => <figure key={f.entry_id} className="candidate"><img src={lib.fileUrl(f.file_id, true)} alt={f.caption} />
              <figcaption className="small muted">{f.caption}</figcaption></figure>)}
            {audio.map((f) => <figure key={f.entry_id} className="candidate audio"><audio controls preload="none" src={lib.fileUrl(f.file_id)} />
              <figcaption className="small muted">{f.caption}</figcaption></figure>)}
          </div>
        </div>
      )}
    </section>
  );
}

function Step({ n, title, note, children }: { n: number; title: string; note: string; children: ReactNode }) {
  return (
    <div className="card creation-step">
      <div className="row"><span className="step-n">{n}</span><strong className="grow">{title}</strong></div>
      <p className="small muted">{note}</p>
      {children}
    </div>
  );
}

function Candidates({ cands, act }: { cands: { id: string; file_id: string; media?: string; seed: number | null }[]; act: Act }) {
  if (!cands.length) return null;
  return (
    <div className="candidates">
      {cands.map((x) => (
        <figure key={x.id} className={`candidate ${x.media === "audio" ? "audio" : ""}`}>
          {x.media === "audio" ? <audio controls preload="none" src={lib.fileUrl(x.file_id)} />
            : <a href={lib.fileUrl(x.file_id)} target="_blank" rel="noreferrer"><img src={lib.fileUrl(x.file_id)} alt="candidate" /></a>}
          {x.seed != null && <figcaption className="small muted">seed {x.seed}</figcaption>}
          <div className="actions">
            <button className="ghost small" onClick={() => act(() => lib.discard(x.id))}>Discard</button>
            <button className="primary small" onClick={() => act(() => lib.keep(x.id))}>Keep</button>
          </div>
        </figure>
      ))}
    </div>
  );
}

function PlanEditor({ kind, plan, act, onSave, onRedraft }: {
  kind: AssetKind; plan: CreationPlan; act: Act; onSave: (p: CreationPlan) => Promise<unknown>; onRedraft: () => void;
}) {
  const [p, setP] = useState(plan);
  useEffect(() => setP(plan), [plan]);
  const dirty = JSON.stringify(p) !== JSON.stringify(plan);
  const view = (i: number, patch: Partial<CreationPlan["views"][number]>) =>
    setP({ ...p, views: p.views.map((v, n) => (n === i ? { ...v, ...patch } : v)) });
  return (
    <details className="card plan-editor" open={dirty}>
      <summary><strong>The director's draft</strong> <span className="muted small">· name, description and prompts (edit before generating)</span></summary>
      <label className="small">Name<input value={p.name} maxLength={120} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
      <label className="small">What must stay the same <textarea rows={3} value={p.description} maxLength={2000}
        onChange={(e) => setP({ ...p, description: e.target.value })} /></label>
      <label className="small">Tags<input value={p.tags} onChange={(e) => setP({ ...p, tags: e.target.value })} /></label>
      {p.views.map((v, i) => (
        <div key={i} className="plan-view">
          <label className="small">{i === 0 ? "Anchor view" : `View ${i + 1}`}
            <input value={v.label} maxLength={80} onChange={(e) => view(i, { label: e.target.value })} /></label>
          <textarea rows={3} value={v.prompt} maxLength={2000} aria-label={`${v.label} prompt`} onChange={(e) => view(i, { prompt: e.target.value })} />
          {i > 0 && <button type="button" className="ghost small" onClick={() => setP({ ...p, views: p.views.filter((_, n) => n !== i) })}>Remove view</button>}
        </div>
      ))}
      {kind !== "sound" && p.views.length < 4 && (
        <button type="button" className="ghost small" onClick={() => setP({ ...p, views: [...p.views, { label: "another view",
          prompt: "The same subject from another angle; keep everything else identical." }] })}>+ View</button>
      )}
      {p.voice && <>
        <label className="small">Voice<input value={p.voice.description} maxLength={500}
          onChange={(e) => setP({ ...p, voice: { ...p.voice!, description: e.target.value } })} /></label>
        <label className="small">Sample line<input value={p.voice.line} maxLength={300}
          onChange={(e) => setP({ ...p, voice: { ...p.voice!, line: e.target.value } })} /></label>
      </>}
      {p.music && <>
        <label className="small">Music style<input value={p.music.style} maxLength={500}
          onChange={(e) => setP({ ...p, music: { ...p.music!, style: e.target.value } })} /></label>
        <label className="small">Lyrics <span className="muted">· empty for instrumental</span><textarea rows={3} value={p.music.lyrics}
          onChange={(e) => setP({ ...p, music: { ...p.music!, lyrics: e.target.value } })} /></label>
        <label className="small">Seconds<input type="number" min={5} max={120} value={p.music.seconds}
          onChange={(e) => setP({ ...p, music: { ...p.music!, seconds: Number(e.target.value) } })} /></label>
      </>}
      <div className="actions">
        <button type="button" className="ghost small" onClick={onRedraft} title="The director writes the draft again from your description">Redraft</button>
        {dirty && <button type="button" className="ghost small" onClick={() => setP(plan)}>Undo changes</button>}
        <button type="button" className="primary small" disabled={!dirty} onClick={() => act(() => onSave(p))}>Save draft</button>
      </div>
    </details>
  );
}
