/** Create (its own page): describe a character, location, prop, style or sound; the director drafts it; you generate
 *  its pictures, voice or music and a test clip, keep the ones you want, and approve it into the library, optionally
 *  into a project's cast or onto a shot (backend/app/creator.py). */
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ask } from "./Dialog";
import type { Listener } from "./App";
import { ImageJobs } from "./Library";
import { api, type Project, type Shot } from "./api";
import {
  type AssetDetail, type AssetKind, type Capability, type Creation, type CreationPart, type CreationPlan, KIND_LABEL,
  KINDS, lib, type TemplateSettings,
} from "./library-api";
import { ResolutionPicker } from "./ResolutionPicker";
import { WanGPLink } from "./WanGPLink";

type Subscribe = (l: Listener, resync: () => void) => () => void;
type Act = (fn: () => Promise<unknown>) => Promise<void>;
const ACTIVE = new Set(["submitting", "queued", "running", "cancelling", "restored"]);
const HINT: Record<AssetKind, string> = {
  character: "Who they are and how they look: age, build, face, hair, clothing, personality, how they sound",
  location: "The place: what it is, its era, layout, materials, light, time of day, weather",
  prop: "The object: what it is, size, materials, colours, wear, what it's used for",
  style: "The look: medium (e.g. watercolour, 35 mm film), palette, light, texture, era",
  sound: "The sound: a song (genre, instruments, mood, tempo, lyrics if it has words) or an ambience (the place and what you hear)",
};
const TAB_LABEL: Record<AssetKind, string> = { character: "Character", location: "Location", prop: "Prop", style: "Style", sound: "Sound" };
const usable = (t: Capability) => t.valid && t.installed !== false;
const LAST_PROJECT = "filmlab:last-project";
export function rememberProject(id: string) { try { localStorage.setItem(LAST_PROJECT, id); } catch { /* this visit only */ } }
function lastProject(): string | null { try { return localStorage.getItem(LAST_PROJECT); } catch { return null; } }

// --- the page ------------------------------------------------------------------------------------------

/** #/create[/<kind>] or #/create/c/<creation id>: tabs per kind, the start form and drafts, and the open draft. */
export function CreatePage({ subscribe, kind, creationId }: { subscribe: Subscribe; kind: AssetKind; creationId: string | null }) {
  const [list, setList] = useState<Creation[]>([]);
  const load = useCallback(() => { lib.creations().then(setList).catch(() => setList([])); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => subscribe((e) => { if (e.type === "creation.updated") load(); }, load), [subscribe, load]);
  const open = list.find((c) => c.id === creationId);
  const tab = open?.kind ?? kind;
  const drafts = list.filter((c) => c.kind === tab);
  return (
    <main className="page create-page">
      <div className="create-head">
        <h2>Create</h2>
        <span className="muted small grow">Describe it; the director drafts it; you make and keep its pictures, voice or music,
          then add it to the Library.</span>
      </div>
      <nav className="create-tabs" role="tablist" aria-label="What to create">
        {KINDS.map((k) => {
          const n = list.filter((c) => c.kind === k).length;
          return (
            <a key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} href={`#/create/${k}`}>
              {TAB_LABEL[k]}{n > 0 && <span className="tab-count">{n}</span>}</a>);
        })}
      </nav>
      <div className="create-body">
        <aside className="create-side">
          <CreateForm key={tab} kind={tab} />
          {drafts.length > 0 && (
            <div className="card create-drafts">
              <strong className="small">Drafts in progress</strong>
              <ul className="creation-list">
                {drafts.map((c) => (
                  <li key={c.id}>
                    <a className={`asset-row ${c.id === creationId ? "selected" : ""}`} href={`#/create/c/${c.id}`}>
                      <span className="noimg creating" aria-hidden>✨</span>
                      <span><strong>{c.asset?.name ?? "New " + c.kind}</strong>
                        <span className="block muted small">{c.status === "drafting" ? "director is drafting…" : "draft, not in the library yet"}</span></span>
                    </a>
                  </li>))}
              </ul>
            </div>)}
        </aside>
        {creationId ? <CreationView key={creationId} id={creationId} subscribe={subscribe} />
          : (
            <section className="panel create-intro">
              <h3>New {TAB_LABEL[tab].toLowerCase()}</h3>
              <ol className="small">
                <li><strong>Describe it</strong> on the left; the director drafts its name, what must stay the same, and the
                  {tab === "sound" ? " music or ambience to make." : " pictures to make."}</li>
                <li><strong>Generate</strong> each part and keep what you like{tab === "character" ? ", design or clone a voice," : ""}
                  {tab !== "sound" ? " and try a short test clip to see it move." : "."}</li>
                <li><strong>Approve</strong>: it joins the Library, and if you like the open project's cast or a shot.</li>
              </ol>
              <p className="small muted">Nothing uses the GPU until you press Generate, and nothing is added to the Library until you approve.</p>
            </section>)}
      </div>
    </main>
  );
}

// --- starting a draft ----------------------------------------------------------------------------------

function CreateForm({ kind }: { kind: AssetKind }) {
  const [brief, setBrief] = useState("");
  const [voice, setVoice] = useState<"none" | "design" | "clone">("design");
  const [soundType, setSoundType] = useState<"music" | "ambience">("music");
  const [caps, setCaps] = useState<{ templates: Capability[]; settings: TemplateSettings } | null>(null);
  const [image, setImage] = useState("");
  const [edit, setEdit] = useState("");
  const [size, setSize] = useState("1024x1024");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    lib.capabilities().then((c) => { setCaps(c); setImage(c.settings.image_text); setEdit(c.settings.image_refs); })
      .catch(() => setCaps(null));
    const pid = lastProject();  // the open (last opened) project's size
    if (pid) api.project(pid).then((p) => setSize(p.default_resolution)).catch(() => undefined);
  }, []);
  const gen = caps?.templates.filter((t) => t.task === "gen_image") ?? [];
  const edits = caps?.templates.filter((t) => t.task === "edit_image" && t.refs !== "none") ?? [];
  const designOk = caps?.templates.some((t) => t.task === "gen_speech_from_description" && usable(t));
  const cloneOk = caps?.templates.some((t) => t.task === "gen_speech_from_sample" && usable(t));
  const musicOk = caps?.templates.some((t) => t.task === "gen_song" && usable(t));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const pictures = kind !== "sound";
      const c = await lib.startCreation({
        kind, brief: brief.trim(), image_template: pictures ? image : undefined, edit_template: pictures ? edit : undefined,
        resolution: pictures ? size : undefined, voice: kind === "character" && voice !== "none",
        voice_mode: kind === "character" && voice !== "none" ? voice : undefined, sound_type: kind === "sound" ? soundType : undefined,
      });
      location.hash = `#/create/c/${c.id}`;
      setBrief("");
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const option = (t: Capability) => (
    <option key={t.template} value={t.template} disabled={!t.valid}>
      {t.template}{!t.valid ? " (not usable)" : t.installed === false ? " (downloads on first use)" : ""}</option>
  );
  return (
    <form className="card create-form" onSubmit={submit}>
      <strong>New {TAB_LABEL[kind].toLowerCase()}</strong>
      <textarea rows={5} placeholder={HINT[kind]} value={brief} maxLength={4000} onChange={(e) => setBrief(e.target.value)}
        aria-label="Describe it" />
      {kind === "sound" ? (
        <div className="seg" role="radiogroup" aria-label="Kind of sound">
          {(["music", "ambience"] as const).map((t) => (
            <label key={t} className={`seg-item ${soundType === t ? "on" : ""}`}>
              <input type="radio" name="sound-type" checked={soundType === t} onChange={() => setSoundType(t)} />
              {t === "music" ? "Music or song" : "Ambience"}</label>))}
          {!musicOk && <p className="small warn">No music model is ready in WanGP (e.g. ACE-Step).</p>}
        </div>
      ) : <>
        <div className="small sm-field">Picture size <ResolutionPicker value={size} onChange={setSize} /></div>
        <label className="small">First picture (from text)
          <select value={image} onChange={(e) => setImage(e.target.value)}>{gen.map(option)}</select></label>
        <label className="small">Other views (edits of the first, so it stays the same {kind})
          <select value={edit} onChange={(e) => setEdit(e.target.value)}>{edits.map(option)}</select></label>
      </>}
      {kind === "character" && (
        <div className="seg" role="radiogroup" aria-label="Voice">
          {([["design", "Design a voice", designOk], ["clone", "Clone a sample", cloneOk], ["none", "No voice", true]] as const).map(([v, l, ok]) => (
            <label key={v} className={`seg-item ${voice === v ? "on" : ""}`} title={ok ? "" : "No model for this is ready in WanGP"}>
              <input type="radio" name="voice-mode" checked={voice === v} disabled={!ok} onChange={() => setVoice(v)} />{l}</label>))}
        </div>
      )}
      {error && <p className="error small">{error}</p>}
      <button className="primary" disabled={busy || brief.trim().length < 10}>{busy ? "Starting…" : "✨ Draft it with the director"}</button>
    </form>
  );
}

// --- one draft -----------------------------------------------------------------------------------------

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
    return <section className="panel"><p>“{c.plan?.name}” is in the library. <a href={`#/library/${c.asset_id}`}>Open it</a></p></section>;
  }
  if (c.status === "discarded") return <section className="panel"><p className="muted">Discarded: it's in Recently deleted.</p></section>;

  const files = asset?.variants.flatMap((v) => v.files) ?? [];
  const images = files.filter((f) => f.kind === "image");
  const audio = files.filter((f) => f.kind === "audio");
  const cands = asset?.images.candidates ?? [];
  const jobs = asset?.images.jobs ?? [];
  const running = (label: string) => jobs.some((j) => ACTIVE.has(j.status) && j.label === label);
  const plan = c.plan;
  const views = plan?.views ?? [];
  const canApprove = c.kind === "sound" ? audio.length > 0 : images.length > 0;
  const generate = (part: CreationPart, what: string, opts: { index?: number; line?: string; count?: number } = {}) => act(async () => {
    if (await ask(`Generate ${what} now? This uses the GPU.`, { confirm: "Generate" })) await lib.generateCreation(c.id, part, opts);
  });
  let n = 1;

  return (
    <section className="panel creation-view">
      <div className="row">
        <span className="chip s-approved">{KIND_LABEL[c.kind]}</span>
        <h2 className="grow creation-title">{plan?.name ?? "New " + c.kind} <span className="muted small">· draft</span></h2>
        <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask("Discard this draft? It goes to Recently deleted with anything generated for it.")) await lib.discardCreation(c.id);
        })}>Discard…</button>
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
      {jobs.some((j) => ACTIVE.has(j.status)) && <p className="small muted">Generating… <WanGPLink label="Watch in WanGP ↗" title="WanGP's own page" /></p>}
      <ImageJobs jobs={jobs} act={act} />

      {plan && c.kind !== "sound" && (
        <Step n={n++} title="Pictures" note={`The first is made from text with ${c.image_template}; the others are edits of the kept first one with ${c.edit_template}, so it stays the same ${c.kind}.`}>
          <div className="row create-size">
            <span className="small muted">Size</span>
            <ResolutionPicker value={c.resolution || "1024x1024"} onChange={(r) => act(() => lib.creationSettings(c.id, { resolution: r }))} />
          </div>
          {views.map((v, i) => {
            const mine = cands.filter((x) => x.label === v.label);
            const kept = images.filter((f) => f.caption === v.label);
            return (
              <div key={i} className="view-row">
                <div className="row">
                  <strong className="small grow">{i === 0 ? "First: " : ""}{v.label}{kept.length > 0 && <span className="ok-text"> · kept</span>}</strong>
                  {i === 0 && <UploadOwn variantId={asset?.variants[0]?.id} label={v.label} act={act} />}
                  <button className="primary small" disabled={running(v.label) || (i > 0 && !images.length)}
                    title={i > 0 && !images.length ? "Keep the first picture first: this one is made from it" : ""}
                    onClick={() => generate("view", `the ${v.label}`, { index: i })}>
                    {mine.length || kept.length ? "Make more" : "Generate"}</button>
                </div>
                <Candidates cands={mine} act={act} />
              </div>);
          })}
          {views.length > 1 && images.length > 0 && (
            <button className="ghost small" disabled={views.slice(1).some((v) => running(v.label))}
              onClick={() => generate("views", "all the other views")}>Generate all other views</button>)}
        </Step>
      )}
      {plan?.voice && <VoiceStep n={n++} c={c} asset={asset} plan={plan} cands={cands} running={running} act={act} generate={generate} />}
      {plan?.music && (
        <Step n={n++} title={c.sound_type === "ambience" ? "Ambience" : "Music"} note={`${plan.music.style} · ${plan.music.seconds} s${plan.music.lyrics ? " · with lyrics" : " · instrumental"}`}>
          <button className="primary small" disabled={running("music")} onClick={() => generate("music", "the sound")}>Generate</button>
          <Candidates cands={cands.filter((x) => x.label === "music")} act={act} />
        </Step>
      )}
      {plan && c.kind !== "sound" && (
        <Step n={n++} title="Test clip" note="A short clip from the kept pictures with your video model: does the look hold up in motion? Optional; it uses GPU time, and it stays with the draft.">
          <button className="ghost small" disabled={!images.length || running("test clip")} title={images.length ? "" : "Keep a picture first"}
            onClick={() => generate("clip", "a 3-second test clip")}>{c.clips?.length ? "Make another" : "Make a test clip"}</button>
          {!!c.clips?.length && (
            <div className="candidates">
              {c.clips.map((k) => (
                <figure key={k.id} className="candidate clip">
                  <video src={lib.clipUrl(c.id, k.id)} poster={k.has_thumb ? lib.clipUrl(c.id, k.id, true) : undefined}
                    controls loop playsInline preload="none" />
                  <figcaption className="small muted">{k.duration_s ? `${k.duration_s.toFixed(1)} s` : ""}</figcaption>
                </figure>))}
            </div>)}
        </Step>
      )}
      {(images.length > 0 || audio.length > 0) && (
        <div className="card">
          <strong className="small">Kept · goes into the Library</strong>
          <div className="candidates">
            {images.map((f) => <figure key={f.entry_id} className="candidate"><img src={lib.fileUrl(f.file_id, true)} alt={f.caption} />
              <figcaption className="small muted">{f.caption}</figcaption></figure>)}
            {audio.map((f) => <figure key={f.entry_id} className="candidate audio"><audio controls preload="none" src={lib.fileUrl(f.file_id)} />
              <figcaption className="small muted">{f.caption}</figcaption></figure>)}
          </div>
        </div>
      )}
      <ApproveStep n={n} c={c} canApprove={canApprove} act={act} />
    </section>
  );
}

function VoiceStep({ n, c, asset, plan, cands, running, act, generate }: {
  n: number; c: Creation; asset: AssetDetail | null; plan: CreationPlan; cands: AssetDetail["images"]["candidates"];
  running: (label: string) => boolean; act: Act;
  generate: (part: CreationPart, what: string, opts?: { line?: string }) => Promise<void>;
}) {
  const [line, setLine] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const clone = c.voice_mode === "clone";
  return (
    <Step n={n} title="Voice" note={clone ? "Cloned from a recording you upload (2-30 s of one person speaking clearly)."
      : `Designed from “${plan.voice!.description}”.`}>
      <div className="seg" role="radiogroup" aria-label="How the voice is made">
        {(["design", "clone"] as const).map((m) => (
          <label key={m} className={`seg-item ${(c.voice_mode ?? "design") === m ? "on" : ""}`}>
            <input type="radio" name={`vm-${c.id}`} checked={(c.voice_mode ?? "design") === m}
              onChange={() => act(() => lib.creationSettings(c.id, { voice_mode: m }))} />
            {m === "design" ? "Design from a description" : "Clone a sample"}</label>))}
      </div>
      {clone && (
        <div className="row">
          <span className="small grow">{c.voice_sample_file_id ? <>Sample: <audio controls preload="none" src={lib.fileUrl(c.voice_sample_file_id)} className="voice-player" /></>
            : <span className="warn">No sample yet.</span>}</span>
          <button className="ghost small" onClick={() => file.current?.click()}>{c.voice_sample_file_id ? "Replace sample…" : "Upload a sample…"}</button>
          <input ref={file} type="file" hidden accept="audio/wav,audio/x-wav,audio/mpeg,audio/flac,.wav,.mp3,.flac"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) act(() => lib.creationVoiceSample(c.id, f)); }} />
        </div>)}
      <div className="row">
        <span className="small muted grow">Says: “{plan.voice!.line}”</span>
        <button className="primary small" disabled={running("voice") || (clone && !c.voice_sample_file_id)}
          onClick={() => generate("voice", "the voice")}>{asset?.voice ? "Make more" : "Generate"}</button>
      </div>
      {asset?.voice && <p className="small ok-text">Voice kept ({asset.voice.duration_s?.toFixed(1)} s)
        <audio controls preload="none" src={lib.fileUrl(asset.voice.file_id)} className="voice-player" /></p>}
      <Candidates cands={cands.filter((x) => x.label === "voice")} act={act} />
      {(asset?.voice || c.voice_sample_file_id) && (
        <div className="row say-line">
          <input className="grow" placeholder="Type a line to hear it in this voice" value={line} maxLength={300}
            onChange={(e) => setLine(e.target.value)} />
          <button className="ghost small" disabled={line.trim().length < 3 || running("voice test")}
            onClick={() => generate("say", "this line", { line: line.trim() })}>Hear it</button>
        </div>)}
      <Candidates cands={cands.filter((x) => x.label === "voice test")} act={act} listenOnly />
    </Step>
  );
}

function ApproveStep({ n, c, canApprove, act }: { n: number; c: Creation; canApprove: boolean; act: Act }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(lastProject() ?? "");
  const [shots, setShots] = useState<Shot[]>([]);
  const [shotId, setShotId] = useState("");
  useEffect(() => { api.projects().then(setProjects).catch(() => setProjects([])); }, []);
  useEffect(() => {
    setShotId("");
    if (projectId && c.kind !== "sound") api.project(projectId).then((p) => setShots(p.shots)).catch(() => setShots([]));
    else setShots([]);
  }, [projectId, c.kind]);
  return (
    <Step n={n} title="Approve" note="It joins the Library. Optionally also the cast of a project, and a shot's references.">
      <div className="row approve-to">
        <label className="small">Add to project
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">— none —</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select></label>
        {c.kind !== "sound" && projectId && (
          <label className="small">and to shot
            <select value={shotId} onChange={(e) => setShotId(e.target.value)}>
              <option value="">— none —</option>
              {shots.map((s) => <option key={s.id} value={s.id}>{s.position}. {s.title}</option>)}
            </select></label>)}
        <span className="grow" />
        <button className="primary" disabled={!canApprove} title={canApprove ? "Add it to the library" : "Keep at least one result first"}
          onClick={() => act(async () => {
            const done = await lib.approveCreation(c.id, { ...(projectId ? { project_id: projectId } : {}), ...(shotId ? { shot_id: shotId } : {}) });
            location.hash = `#/library/${done.asset_id}`;
          })}>Approve into the Library</button>
      </div>
    </Step>
  );
}

/** Use the owner's own picture as the first view: uploaded into the draft's look, it becomes what the others are made from. */
function UploadOwn({ variantId, label, act }: { variantId?: string; label: string; act: Act }) {
  const file = useRef<HTMLInputElement>(null);
  if (!variantId) return null;
  return <>
    <button className="ghost small" title="Use your own picture as the first view; the other views are made from it"
      onClick={() => file.current?.click()}>Use my picture…</button>
    <input ref={file} type="file" hidden accept="image/png,image/jpeg,image/webp"
      onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) act(() => lib.upload(variantId, f, { caption: label, source_note: "Uploaded by the owner" })); }} />
  </>;
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

function Candidates({ cands, act, listenOnly }: {
  cands: { id: string; file_id: string; media?: string; seed: number | null }[]; act: Act; listenOnly?: boolean;
}) {
  const [big, setBig] = useState<string | null>(null);
  if (!cands.length) return null;
  return (
    <div className="candidates">
      {cands.map((x) => (
        <figure key={x.id} className={`candidate ${x.media === "audio" ? "audio" : ""} ${big === x.id ? "big" : ""}`}>
          {x.media === "audio" ? <audio controls preload="none" src={lib.fileUrl(x.file_id)} />
            : <button type="button" className="cand-img" title="Click to enlarge" onClick={() => setBig(big === x.id ? null : x.id)}>
              <img src={lib.fileUrl(x.file_id)} alt="candidate" /></button>}
          {x.seed != null && <figcaption className="small muted">seed {x.seed}</figcaption>}
          <div className="actions">
            <button className="ghost small" onClick={() => act(() => lib.discard(x.id))}>{listenOnly ? "Done" : "Discard"}</button>
            {!listenOnly && <button className="primary small" onClick={() => act(() => lib.keep(x.id))}>Keep</button>}
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
          <label className="small">{i === 0 ? "First view" : `View ${i + 1}`}
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
        <label className="small">Style<input value={p.music.style} maxLength={500}
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
