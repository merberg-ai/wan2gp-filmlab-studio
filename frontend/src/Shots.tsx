import { TransitionSelect } from "./TransitionSelect";
import { SoundEditor, soundSummary } from "./SoundEditor";
import { type FormEvent, type HTMLAttributes, type ReactNode, useEffect, useState } from "react";
import { ask, askText } from "./Dialog";
import { api, type Job, type Revision, type Shot, type ShotIssue, type Take, type Template, type Validation, seconds, type ShotSound, transitionLabel } from "./api";
import { goShot } from "./Palette";
import { ImagePicker, type Pick } from "./ImagePicker";
import { SIZES } from "./ProjectSettings";
import { lib, type Preset } from "./library-api";
import { ShotReferences, templateOptions } from "./ShotRefs";
import { deleteWithUndo } from "./bulk";

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft", pending_approval: "Awaiting approval", approved: "Approved", queued: "Queued",
  rendering: "Rendering", ready: "Ready for review", failed: "Failed", cancelled: "Cancelled",
};
/** Ask a shot card to open its editor (the reshoot card after a Reject sends it). */
export const EDIT_SHOT = "filmlab:edit-shot";
export const ACTIVE_JOB = new Set(["submitting", "queued", "running", "cancelling", "restored"]);
export const PREVIEW_HINT = "A quick take at half the width and height (several times faster) with the same prompt, references, "
  + "sound and seed: judge timing and framing first, then render full quality.";

export function ShotCard({ shot, templates, selected, job, approval, onSelect, act, controls, drag, pick, issues, takes }: {
  shot: Shot; templates: Template[]; selected: boolean; job?: Job; approval?: { note: string; requested_by: string };
  onSelect: () => void; act: (fn: () => Promise<unknown>) => Promise<void>;
  controls?: ReactNode; drag?: HTMLAttributes<HTMLLIElement>;
  /** Selecting shots for a bulk action (the storyboard's Select mode). */
  pick?: { on: boolean; toggle: () => void };
  /** Pre-render warnings (the storyboard's /checks). */
  issues?: ShotIssue[];
  takes?: Take[];
}) {
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState(false);
  useEffect(() => {
    const on = (e: Event) => {
      if ((e as CustomEvent<{ shotId: string }>).detail.shotId !== shot.id) return;
      setEditing(true);
      requestAnimationFrame(() => document.getElementById(`shot-${shot.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };
    window.addEventListener(EDIT_SHOT, on);
    return () => window.removeEventListener(EDIT_SHOT, on);
  }, [shot.id]);
  const [check, setCheck] = useState<Validation | null>(null);
  const locked = !!shot.locked;
  // A rendered shot stays editable: the next take uses the new version; earlier takes keep their own record.
  const editable = ["draft", "failed", "pending_approval", "ready"].includes(shot.status) && !(job && ACTIVE_JOB.has(job.status)) && !locked;
  const canRender = ["draft", "pending_approval", "failed", "ready", "approved"].includes(shot.status) && !(job && ACTIVE_JOB.has(job.status));

  return (
    <li id={`shot-${shot.id}`} className={`card shot ${selected ? "selected" : ""} ${locked ? "locked" : ""} ${pick?.on ? "picked" : ""}`} {...drag}>
      <div className="shot-head">
      {pick && <input type="checkbox" className="shot-pick" checked={pick.on} onChange={pick.toggle}
        aria-label={`Select shot ${shot.position} ${shot.title}`} />}
      <button className="shot-title" onClick={pick ? pick.toggle : onSelect} aria-expanded={pick ? undefined : selected}>
        {drag?.draggable && <span className="grip" aria-hidden="true" title="Drag to reorder">⠿</span>}
        <span className="pos">{shot.position}</span>
        <strong>{shot.title}</strong>
        {locked && <span title="Locked: the director can't change it and batches don't re-render it">🔒</span>}
        {issues && issues.length > 0 && <span className={`chip ${issues.some((i) => i.level === "block") ? "s-failed" : "warn-chip"}`}
          title={issues.map((i) => i.message).join("\n")}>⚠ {issues.length}</span>}
        <span className={`chip s-${shot.status}`}>{STATUS_LABEL[shot.status] ?? shot.status}</span>
      </button>
      </div>
      <div className="meta muted small">
        {seconds(shot.frames)} s · {shot.frames} f · {shot.resolution} · {shot.template}
        {shot.template_explicit === false && <span className="tag" title="Switches automatically between the with- and without-references defaults">follows defaults</span>}
        {shot.created_by === "director" && <span className="tag">by director</span>}
        {shot.transition && shot.transition !== "cut" && <span className="tag" title="Into the next shot">→ {transitionLabel(shot.transition).toLowerCase()}</span>}
      </div>
      {approval && <p className="approval">Director asks to render{approval.note ? `: “${approval.note}”` : "."}</p>}
      {editing ? (
        <ShotEditor initial={shot} templates={templates}
          onSave={(s) => act(async () => { await api.updateShot(shot.id, s); setEditing(false); })} onCancel={() => setEditing(false)} />
      ) : (
        <>
          {shot.idea && shot.idea !== shot.prompt && <p className="small muted idea">Your idea: “{shot.idea}”</p>}
          <p className="prompt">{shot.prompt}</p>
          <p className={`small sound-summary ${shot.sound ? "" : "unset"}`} title="The shot's sound (Edit to change it)">
            <span aria-hidden>{shot.sound ? (shot.sound.lines.length ? "🗣" : "🔇") : "🔈"}</span>{" "}
            {soundSummary(shot.sound) ?? "Sound not set: the model may invent speech"}</p>
          {shot.rationale && <p className="small muted rationale" title="The director's latest reason">Director: {shot.rationale}</p>}
        </>
      )}
      {issues && issues.length > 0 && !editing && (selected || issues.some((i) => i.level === "block")) && (
        <div className="shot-issues" role="group" aria-label="Warnings">
          {issues.map((i, n) => <p key={n} className={`small ${i.level === "block" ? "error" : "warn"}`}>{i.level === "block" ? "Can't render: " : ""}{i.message}</p>)}
          {editable && (
            <div className="row">
              {issues.some((i) => i.level === "warn") && (
                <button className="ghost small" title="The director fixes only what's listed and keeps the rest of the prompt"
                  onClick={() => act(() => api.fixPrompts(shot.project_id, [shot.id]))}>✨ Fix with director</button>
              )}
              <button className="ghost small" onClick={() => setEditing(true)}>Edit</button>
            </div>
          )}
        </div>
      )}
      <ShotReferences shot={shot} active={!!(job && ACTIVE_JOB.has(job.status))} selected={selected} act={act} />
      {check && (
        <p className={`small ${check.valid ? "ok-text" : "error"}`}>
          {check.valid ? "Valid" : "Invalid"}{check.available === false ? " · model files not fully downloaded" : ""}
          {" "}· renders at {check.canvas?.width}×{check.canvas?.height}
          {check.requested_video && ` · ${check.requested_video.frames} f`} · {check.windows} window{check.windows === 1 ? "" : "s"}
          {check.diagnostics.map((d, i) => <span key={i} className="block">{d.severity}: {d.message}</span>)}
        </p>
      )}
      <div className="actions">
        {editable && !editing && <button className="ghost" onClick={() => setEditing(true)}>Edit</button>}
        {editable && !editing && (
          <button className="ghost" title="The director picks references from the library if there are none, then rewrites the prompt around <Picture 1>, <Picture 2>, … You see every change in the chat."
            onClick={() => act(() => api.enhanceShot(shot.id))}>✨ Enhance</button>
        )}
        <button className="ghost" onClick={() => act(async () => setCheck(await api.validateShot(shot.id)))}>Validate</button>
        <button className="ghost" aria-expanded={history} onClick={() => setHistory((h) => !h)}>History</button>
        {!(job && ACTIVE_JOB.has(job.status)) && (
          <button className="ghost" title={locked ? "Let the director change it again" : "Keep it as it is: the director can't change it and batches don't re-render it"}
            onClick={() => act(() => api.lockShot(shot.id, !locked))}>{locked ? "Unlock" : "Lock"}</button>
        )}
        {controls}
        {shot.status === "pending_approval" && <button className="ghost" onClick={() => act(() => api.rejectShot(shot.id))}>Reject</button>}
        {!editing && !locked && !(job && ACTIVE_JOB.has(job.status)) && shot.status !== "approved" && (
          <button className="ghost danger-text" title="Move to Recently deleted, with its takes (Undo right after, or restore later)"
            onClick={() => act(() => deleteWithUndo(shot.project_id, { shot_ids: [shot.id] }, `Deleted shot ${shot.position} “${shot.title}”`))}>Delete</button>
        )}
        {canRender && (
          <button className="ghost" title={PREVIEW_HINT} onClick={() => act(async () => {
            if (await confirmRender(shot.id, setCheck)) await api.approveAndRender(shot.id, true);
          })}>Preview</button>
        )}
        {canRender && (
          <button className="primary" onClick={() => act(async () => {
            if (await confirmRender(shot.id, setCheck)) await api.approveAndRender(shot.id);
          })}>
            {shot.status === "ready" ? "Render new take" : shot.status === "failed" ? "Render again" : "Approve & render"}
          </button>
        )}
      </div>
      {history && <ShotHistory shot={shot} act={act} takes={takes ?? []} />}
    </li>
  );
}

/**
 * Pre-render checks with WanGP (CPU only, nothing is queued):
 * an invalid shot is blocked with WanGP's reasons; missing model files or a busy queue
 * only warn, because rendering then is still the owner's call.
 */
export async function confirmRender(shotId: string, show: (v: Validation) => void): Promise<boolean> {
  const check = await api.validateShot(shotId);
  show(check);
  const blocks = (check.issues ?? []).filter((i) => i.level === "block");
  if (blocks.length) throw new Error(`Can't render: ${blocks.map((i) => i.message).join("; ")}. Nothing was queued.`);
  // Download warnings are asked about below (from WanGP's availability); other warnings are asked here.
  const warns = (check.issues ?? []).filter((i) => i.level === "warn" && !i.message.includes("download"));
  if (warns.length && !await ask(`${warns.map((i) => i.message).join("\n")}\n\nRender anyway?`)) return false;
  if (!check.valid) {
    const why = check.diagnostics.map((d) => d.message).join(" ") || "no reason given";
    throw new Error(`WanGP won't accept this shot: ${why} Nothing was queued.`);
  }
  if (check.available === false && !await ask(
    "This model's files aren't fully downloaded in WanGP, so the render will download them first "
    + "(can be several GB and take a while). Render anyway?")) return false;
  let q;
  try { q = await api.queue(); } catch { return true; }  // the backend refuses the render itself if WanGP is down
  if (!q.total) return true;
  const others = q.other ? `, ${q.other} not from FilmLab` : "";
  return await ask(`WanGP is busy: ${q.total} task${q.total === 1 ? "" : "s"} in its queue${others}. `
    + "This render will wait its turn behind them. Queue it anyway?");
}

export function ShotEditor({ initial, templates, withRefs, defaults, onSave, onCancel }: {
  initial?: Shot; templates: Template[]; withRefs?: boolean; defaults?: { resolution: string; frames: number };
  onSave: (s: Partial<Shot>, refs: Pick[], enhance: boolean) => void; onCancel?: () => void;
}) {
  const [picks, setPicks] = useState<Pick[]>([]);
  const [enhance, setEnhance] = useState(true);
  const [title, setTitle] = useState(initial?.title ?? "");
  const [prompt, setPrompt] = useState(initial?.prompt ?? "");
  const [idea, setIdea] = useState(initial?.idea ?? "");
  const [secs, setSecs] = useState(Number(seconds(initial?.frames ?? defaults?.frames ?? 121)));
  const [resolution, setResolution] = useState(initial?.resolution ?? defaults?.resolution ?? "1280x720");
  // "" = follow the configured defaults (they switch between shots with and without references)
  const [template, setTemplate] = useState(initial && initial.template_explicit !== false ? initial.template : "");
  const [transition, setTransition] = useState(initial?.transition ?? "cut");
  const [sound, setSound] = useState<ShotSound | null>(initial?.sound ?? null);
  const speakers = [...new Set((initial?.refs ?? []).filter((r) => r.kind === "character").map((r) => r.asset_name))];
  const [presets, setPresets] = useState<Preset[]>([]);
  const [preset, setPreset] = useState<Preset | null>(null);
  const [presetNote, setPresetNote] = useState("");
  const groups = templateOptions(templates);
  const frames = Math.round((Math.round(secs * 24) + 1 - 1) / 8) * 8 + 1;
  const loadPresets = () => lib.presets().then(setPresets).catch(() => setPresets([]));
  useEffect(() => { loadPresets(); }, []);
  const choosePreset = (id: string) => {
    const p = presets.find((x) => x.id === id) ?? null;
    setPreset(p);
    setPresetNote("");
    if (!p || p.status !== "ready") return;
    setSecs(p.seconds); setResolution(p.resolution); setTemplate(p.template);
  };
  const saveAsPreset = async () => {
    const name = (await askText("Name this preset", { title: "Save as preset", confirm: "Save preset", placeholder: "e.g. Teaser close-up" }))?.trim();
    if (!name) return;
    try {
      const p = await lib.createPreset({ name, template, resolution, frames });
      await loadPresets();
      setPreset(p);
      setPresetNote(`Saved “${p.name}”.`);
    } catch (e) { setPresetNote(e instanceof Error ? e.message : String(e)); }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const filled = sound && { ...sound, lines: sound.lines.filter((l) => l.text.trim() || l.speaker.trim()) };
    onSave({ title, prompt, frames, resolution, transition, ...(template || initial ? { template } : {}),
      ...(filled && JSON.stringify(filled) !== JSON.stringify(initial?.sound ?? null) ? { sound: filled } : {}),
      ...(initial && idea !== (initial.idea ?? "") ? { idea } : {}) }, picks, withRefs ? enhance : false);
  };
  return (
    <form className="editor" onSubmit={submit}>
      {initial?.status === "ready" && <p className="small muted">This shot has takes. Changes apply to the next take; existing takes keep the prompt and references they used.</p>}
      <input placeholder="Shot title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      {initial && (
        <label className="small">Your idea <span className="muted">· what ✨ Enhance rewrites from, every time</span>
          <textarea rows={2} placeholder="Your own words for the shot, e.g. Jason stalks Hale through the burning trench"
            value={idea} onChange={(e) => setIdea(e.target.value)} />
        </label>
      )}
      <label className="small">{initial ? "Picture prompt sent to the model" : "Prompt"} <span className="muted">· what we see; speech and sound go in Sound below</span>
        <textarea placeholder={withRefs ? "Your idea, rough is fine: the director can expand it (Enhance)" : "Prompt: subject, action, camera, lighting, mood"}
          rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      <SoundEditor value={sound} onChange={setSound} speakers={speakers} seconds={secs} />
      {presets.length > 0 && (
        <div className="preset-row">
          <label className="small">Preset
            <select value={preset?.id ?? ""} onChange={(e) => choosePreset(e.target.value)}>
              <option value="">None: set the model, size and length below</option>
              {(["filmlab", "wangp", "owner"] as const).map((src) => {
                const list = presets.filter((p) => p.source === src);
                return list.length > 0 && (
                  <optgroup key={src} label={{ filmlab: "FilmLab presets", wangp: "Other installed templates", owner: "Your presets" }[src]}>
                    {list.map((p) => <option key={p.id} value={p.id} disabled={p.status !== "ready"}>
                      {p.name} · {p.seconds} s · {p.resolution}{p.status === "needs_download" ? " (model not downloaded)"
                        : p.status === "unavailable" ? " (not available)" : ""}</option>)}
                  </optgroup>
                );
              })}
            </select>
          </label>
          {preset && <p className="small muted">{preset.template}{preset.refs ? ` · ${preset.refs}` : ""}. {preset.use}
            {preset.tips && <><br /><strong>Writing the prompt:</strong> {preset.tips}</>}</p>}
          {presetNote && <p className="small muted">{presetNote}</p>}
        </div>
      )}
      <div className="row">
        <label className="small">Seconds<input type="number" min={1} max={20} step={0.5} value={secs} onChange={(e) => setSecs(Number(e.target.value))} /></label>
        <span className="small muted">→ {frames} frames</span>
        <label className="small">Size
          <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
            {[...new Set([resolution, ...SIZES])].map((r) => <option key={r}>{r}</option>)}
          </select>
        </label>
        <label className="small">Into next shot
          <TransitionSelect value={transition} onChange={setTransition} />
        </label>
      </div>
      <label className="small">Template
        <select value={template} onChange={(e) => setTemplate(e.target.value)}>
          <option value="">Follow defaults (switches with references)</option>
          <optgroup label="Without references">
            {groups.plain.map((t) => <option key={t.template} value={t.template}>{t.template}</option>)}
          </optgroup>
          <optgroup label="With references">
            {groups.refs.map((t) => <option key={t.template} value={t.template}>{t.template}</option>)}
          </optgroup>
        </select>
      </label>
      {template && <button type="button" className="ghost small" onClick={saveAsPreset}
        title="Save this model, size and length as a preset of your own">Save as preset</button>}
      {withRefs && (
        <fieldset className="refs-field">
          <legend className="small">Reference images (optional)</legend>
          <ImagePicker picks={picks} onChange={setPicks} max={8}
            hint="Characters, locations, props or styles from the library. In the prompt, refer to them as <Picture 1>, <Picture 2>, … in this order." />
        </fieldset>
      )}
      {withRefs && (
        <label className="small check">
          <input type="checkbox" checked={enhance} onChange={(e) => setEnhance(e.target.checked)} />
          {" "}Enhance with the director after saving: it picks references if you chose none and rewrites the prompt
          around them. A rough idea like “the trench is on fire” is enough.
        </label>
      )}
      <div className="actions">
        {onCancel && <button type="button" className="ghost" onClick={onCancel}>Cancel</button>}
        <button className="primary" disabled={!title.trim() || prompt.trim().length < 10}>Save draft</button>
      </div>
    </form>
  );
}

const ACTOR: Record<string, string> = { owner: "You", director: "Director", system: "FilmLab" };
const KIND: Record<string, string> = { create: "created", plan: "planned", update: "edited", refs: "references changed", revert: "restored" };
const FIELD: Record<string, string> = {
  prompt: "prompt", idea: "idea", title: "title", frames: "length", resolution: "size", template: "model",
  template_explicit: "model choice", seed: "seed", transition: "transition", refs: "references",
};

/** Every change to what the shot renders: who, what, why; restore any version. */
function ShotHistory({ shot, act, takes }: { shot: Shot; act: (fn: () => Promise<unknown>) => Promise<void>; takes: Take[] }) {
  const [revs, setRevs] = useState<Revision[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [promptsOnly, setPromptsOnly] = useState(false);
  useEffect(() => { api.revisions(shot.id).then(setRevs).catch(() => setRevs([])); }, [shot.id, shot.updated_at, takes.length]);
  if (!revs) return <p className="small muted">Loading history…</p>;
  if (!revs.length) return <p className="small muted">No recorded changes yet (shots from before F4 start their history at the next change).</p>;
  const latest = revs[revs.length - 1].id;
  const shown = promptsOnly ? revs.filter((r) => r.kind === "create" || r.kind === "plan" || r.changed.includes("prompt") || r.takes?.length) : revs;
  return (
    <>
    <label className="row small muted history-filter">
      <input type="checkbox" checked={promptsOnly} onChange={(e) => setPromptsOnly(e.target.checked)} />
      Only prompt changes and versions that were rendered
    </label>
    <ol className="history small">
      {[...shown].reverse().map((r) => (
        <li key={r.id}>
          <div className="row">
            <span className="grow"><strong>{ACTOR[r.actor] ?? r.actor}</strong> {KIND[r.kind] ?? r.kind}
              {r.changed.length > 0 && r.kind !== "create" && r.kind !== "plan" && <span className="muted"> · {r.changed.map((c) => FIELD[c] ?? c).join(", ")}</span>}
              <span className="muted"> · {new Date(r.created_at).toLocaleString()}</span></span>
            <button className="ghost small" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide" : "Show"}</button>
            {r.id !== latest && (
              <button className="ghost small" disabled={!!shot.locked} title={shot.locked ? "Unlock the shot first" : "Make the shot what it was right after this change"}
                onClick={() => act(async () => {
                  if (await ask("Restore this version of the shot? The current one stays in the history.")) await api.restoreRevision(r.id);
                })}>Restore this version</button>
            )}
          </div>
          {r.rationale && <p className="muted">“{r.rationale}”</p>}
          {r.takes && r.takes.length > 0 && (
            <div className="history-takes">
              <span className="muted">Rendered from this version:</span>
              {r.takes.map((t) => (
                <button key={t.id} className={`thumb mini v-${t.verdict ?? "none"}`} title={`Open this take${t.verdict ? ` (${t.verdict})` : ""}${t.preview ? " · preview" : ""}`}
                  onClick={() => goShot(shot.project_id, shot.id, t.id)}>
                  {t.thumb_media_id ? <img src={api.mediaUrl(t.thumb_media_id)} alt="take" loading="lazy" /> : "take"}
                  {t.verdict && <span className="verdict-dot" aria-label={t.verdict} />}
                  {t.preview && <span className="preview-mark">P</span>}
                </button>
              ))}
            </div>
          )}
          {open === r.id && (
            <dl className="diff">
              {(r.kind === "create" || r.kind === "plan" ? ["prompt", "frames", "template", "refs"] : r.changed).map((k) => (
                <div key={k}>
                  <dt>{FIELD[k] ?? k}</dt>
                  {r.kind !== "create" && r.kind !== "plan" && <dd className="before">{show(k, r.before[k])}</dd>}
                  <dd className="after">{show(k, r.after[k])}</dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ol>
    </>
  );
}

function show(field: string, v: unknown): string {
  if (field === "refs") return Array.isArray(v) && v.length ? `${v.length} reference${v.length === 1 ? "" : "s"}` : "none";
  if (field === "frames" && typeof v === "number") return `${seconds(v)} s (${v} f)`;
  if (v === undefined || v === null || v === "") return "—";
  return String(v);
}
