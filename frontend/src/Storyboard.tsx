import { type DragEvent, type FormEvent, useEffect, useMemo, useState } from "react";
import { ask } from "./Dialog";
import { api, type Batch, type BatchReview, type ProjectDetail, type Scene, type Shot, type Template, seconds } from "./api";
import { lib } from "./library-api";
import { ACTIVE_JOB, ShotCard, ShotEditor } from "./Shots";
import { RedoPanel } from "./Autonomy";
import { FilmStrip } from "./FilmStrip";
import { duration, etaText, expected, jobEta, useNow } from "./eta";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const UNSORTED = "unsorted";

/** The project's shots grouped by scene, in film order: plan, edit, reorder, lock and render as a batch. */
export function Storyboard({ project, templates, selected, onSelect, act }: {
  project: ProjectDetail; templates: Template[]; selected: string | null;
  onSelect: (id: string | null) => void; act: Act;
}) {
  const [adding, setAdding] = useState<string | null>(null);  // scene id, UNSORTED, or null (closed)
  const [review, setReview] = useState<{ preselect?: string[]; requestId?: string } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const sceneIds = new Set(project.scenes.map((s) => s.id));
  const groups = useMemo(() => [
    ...project.scenes.map((sc) => ({ key: sc.id, scene: sc as Scene | null, shots: project.shots.filter((s) => s.scene_id === sc.id) })),
    { key: UNSORTED, scene: null, shots: project.shots.filter((s) => !s.scene_id || !sceneIds.has(s.scene_id)) },
  ], [project]);  // eslint-disable-line react-hooks/exhaustive-deps
  const total = project.shots.reduce((n, s) => n + (s.frames - 1) / 24, 0);
  const lastJob = (id: string) => [...project.jobs].reverse().find((j) => j.shot_id === id);

  const drop = (e: DragEvent, sceneKey: string, beforeShot?: Shot) => {
    e.preventDefault();
    const id = dragging ?? e.dataTransfer.getData("text/x-filmlab-shot");
    setDragging(null);
    if (!id || id === beforeShot?.id) return;
    const target = groups.find((g) => g.key === sceneKey)!;
    const others = target.shots.filter((s) => s.id !== id);
    const index = beforeShot ? others.findIndex((s) => s.id === beforeShot.id) : others.length;
    act(() => api.moveShot(id, sceneKey === UNSORTED ? null : sceneKey, index));
  };

  const shotCard = (s: Shot, i: number, group: typeof groups[number]) => {
    const job = lastJob(s.id);
    const busy = !!(job && ACTIVE_JOB.has(job.status));
    const movable = !busy;
    return (
      <ShotCard key={s.id} shot={s} templates={templates} selected={s.id === selected} job={job}
        approval={project.approvals.find((a) => a.shot_id === s.id)}
        onSelect={() => onSelect(s.id === selected ? null : s.id)} act={act}
        drag={movable ? {
          draggable: true,
          onDragStart: (e) => { setDragging(s.id); e.dataTransfer.setData("text/x-filmlab-shot", s.id); e.dataTransfer.effectAllowed = "move"; },
          onDragEnd: () => setDragging(null),
          onDragOver: (e) => { if (dragging) e.preventDefault(); },
          onDrop: (e) => drop(e, group.key, s),
        } : undefined}
        controls={movable && (
          <span className="move-controls">
            <button className="ghost small" disabled={i === 0} aria-label="Move up" title="Move up"
              onClick={() => act(() => api.moveShot(s.id, group.scene?.id ?? null, i - 1))}>↑</button>
            <button className="ghost small" disabled={i === group.shots.length - 1} aria-label="Move down" title="Move down"
              onClick={() => act(() => api.moveShot(s.id, group.scene?.id ?? null, i + 1))}>↓</button>
            {project.scenes.length > 0 && (
              <select className="small" aria-label="Move to scene" value=""
                onChange={(e) => e.target.value && act(() => api.moveShot(s.id, e.target.value === UNSORTED ? null : e.target.value))}>
                <option value="">Move to…</option>
                {project.scenes.filter((sc) => sc.id !== group.scene?.id).map((sc) => <option key={sc.id} value={sc.id}>{sc.title}</option>)}
                {group.scene && <option value={UNSORTED}>Unsorted</option>}
              </select>
            )}
          </span>
        )} />
    );
  };

  return (
    <section className="panel shots storyboard">
      <div className="panel-head">
        <h2>Storyboard</h2>
        <span className="muted small grow">
          {project.shots.length} shot{project.shots.length === 1 ? "" : "s"} · {total.toFixed(1)} s
          {project.target_seconds ? ` of ${project.target_seconds} s target` : ""}
        </span>
        <button className="ghost" onClick={() => act(() => api.createScene(project.id, { title: `Scene ${project.scenes.length + 1}` }))}>+ Scene</button>
        <button className="primary" disabled={!project.shots.length || !!(project.batch && ["running", "paused"].includes(project.batch.status))}
          onClick={() => setReview({})}>Render storyboard…</button>
      </div>
      <FilmStrip project={project} selected={selected} onSelect={onSelect} act={act} />
      <BriefPanel project={project} act={act} />
      {project.batch_requests.map((r) => (
        <div key={r.id} className="card approval row">
          <span className="grow small">Director asks to render {r.shot_ids.length} shot{r.shot_ids.length === 1 ? "" : "s"} as a batch
            {r.note ? `: “${r.note}”` : "."}</span>
          <button className="ghost small" onClick={() => act(() => api.rejectBatchRequest(r.id))}>Reject</button>
          <button className="primary small" onClick={() => setReview({ preselect: r.shot_ids, requestId: r.id })}>Review…</button>
        </div>
      ))}
      {project.batch && <BatchBar batch={project.batch} project={project} act={act} />}
      <RedoPanel project={project} act={act} />
      {review && <RenderReview project={project} {...review} onClose={() => setReview(null)} act={act} />}
      {groups.map((g) => (g.scene || g.shots.length || !project.scenes.length) && (
        <div key={g.key} className={`scene ${dragging ? "drop-target" : ""}`}
          onDragOver={(e) => { if (dragging) e.preventDefault(); }} onDrop={(e) => drop(e, g.key)}>
          {g.scene ? <SceneHeader scene={g.scene} index={project.scenes.indexOf(g.scene)} count={project.scenes.length}
            shots={g.shots} act={act} onAdd={() => setAdding(g.key)} />
            : project.scenes.length > 0 && <div className="scene-head"><h3 className="muted">Unsorted</h3>
              <button className="ghost small" onClick={() => setAdding(UNSORTED)}>+ Shot</button></div>}
          {adding === g.key && (
            <ShotEditor templates={templates} withRefs defaults={{ resolution: project.default_resolution, frames: project.default_frames }}
              onCancel={() => setAdding(null)}
              onSave={(s, picks, enhance) => act(async () => {
                const shot = await api.createShot(project.id, { ...s, scene_id: g.scene?.id ?? null });
                if (picks.length) await lib.setShotRefs(shot.id, picks.map(({ variant_id, file_id }) => ({ variant_id, file_id })));
                setAdding(null);
                if (enhance) await api.enhanceShot(shot.id);
              })} />
          )}
          <ol className="shot-list">{g.shots.map((s, i) => shotCard(s, i, g))}</ol>
          {!g.scene && !project.scenes.length && !g.shots.length && adding === null && (
            <p className="muted">No shots yet. Write a brief above and let the director plan them, or
              <button className="link" onClick={() => setAdding(UNSORTED)}>add one yourself</button>.</p>
          )}
          {!g.scene && !project.scenes.length && g.shots.length > 0 && adding === null && (
            <button className="ghost small" onClick={() => setAdding(UNSORTED)}>+ Shot</button>
          )}
        </div>
      ))}
    </section>
  );
}

function SceneHeader({ scene, index, count, shots, act, onAdd }: {
  scene: Scene; index: number; count: number; shots: Shot[]; act: Act; onAdd: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState({ title: scene.title, goal: scene.goal, time_place: scene.time_place, mood: scene.mood });
  useEffect(() => setF({ title: scene.title, goal: scene.goal, time_place: scene.time_place, mood: scene.mood }), [scene]);
  const secs = shots.reduce((n, s) => n + (s.frames - 1) / 24, 0);
  if (editing) {
    const save = (e: FormEvent) => { e.preventDefault(); act(async () => { await api.updateScene(scene.id, f); setEditing(false); }); };
    return (
      <form className="editor scene-edit" onSubmit={save}>
        <input value={f.title} maxLength={120} aria-label="Scene title" onChange={(e) => setF({ ...f, title: e.target.value })} />
        <textarea rows={2} placeholder="Goal: what this scene does in the story" value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} />
        <div className="row">
          <input className="grow" placeholder="Time and place" value={f.time_place} onChange={(e) => setF({ ...f, time_place: e.target.value })} />
          <input className="grow" placeholder="Mood" value={f.mood} onChange={(e) => setF({ ...f, mood: e.target.value })} />
        </div>
        <div className="actions"><button type="button" className="ghost small" onClick={() => setEditing(false)}>Cancel</button>
          <button className="primary small" disabled={!f.title.trim()}>Save</button></div>
      </form>
    );
  }
  return (
    <div className="scene-head">
      <h3>{scene.title}</h3>
      <span className="muted small grow">{[scene.time_place, scene.mood].filter(Boolean).join(" · ")}
        {shots.length ? ` · ${shots.length} shot${shots.length === 1 ? "" : "s"}, ${secs.toFixed(1)} s` : " · empty"}</span>
      <button className="ghost small" disabled={index === 0} aria-label="Move scene up" onClick={() => act(() => api.moveScene(scene.id, index - 1))}>↑</button>
      <button className="ghost small" disabled={index === count - 1} aria-label="Move scene down" onClick={() => act(() => api.moveScene(scene.id, index + 1))}>↓</button>
      <button className="ghost small" onClick={() => setEditing(true)}>Edit</button>
      <button className="ghost small" onClick={onAdd}>+ Shot</button>
      <button className="ghost small danger-text" onClick={() => act(async () => {
        if (shots.some((s) => s.locked)) throw new Error("This scene has locked shots; unlock or move them first.");
        if (await ask(`Delete scene “${scene.title}”?${shots.length ? ` Its ${shots.length} shot(s) go to Recently deleted, where you can restore them.` : ""}`))
          await api.deleteScene(scene.id);
      })}>Delete</button>
      {scene.goal && <p className="small muted scene-goal">{scene.goal}</p>}
    </div>
  );
}

const TEMPLATE_HINT: Record<string, string> = {
  horror_teaser: "Who is in danger, where and when (time of day, weather), what threatens them, and the moment of the scare…",
  music_video: "The song (add it to the Library as a Sound), the performer, the places and the visual idea…",
  illustrated_story: "The story in a few sentences, its characters and the illustrated style (e.g. watercolour, ink and wash)…",
  character_dialogue: "Who talks to whom, where, what they want from each other, and the lines (one per shot)…",
};

function BriefPanel({ project, act }: { project: ProjectDetail; act: Act }) {
  const [brief, setBrief] = useState(project.brief);
  const [target, setTarget] = useState(project.target_seconds ?? 0);
  useEffect(() => { setBrief(project.brief); setTarget(project.target_seconds ?? 0); }, [project.brief, project.target_seconds]);
  const dirty = brief !== project.brief || (target || null) !== (project.target_seconds || null);
  const busy = project.director_busy;
  const save = () => api.updateProject(project.id, { brief, target_seconds: target || 0 });
  const planNow = (replace: boolean) => act(async () => {
    if (brief.trim().length < 10) throw new Error("Write a brief first: a sentence or two is enough.");
    if (replace && !await ask("Re-plan: the director's new plan replaces unlocked draft shots without takes "
      + "(they go to Recently deleted). Locked, approved and rendered shots stay. Go ahead?")) return;
    if (dirty) await save();
    await api.plan(project.id, replace);
  });
  return (
    <details className="card brief" open={!project.shots.length}>
      <summary><strong>Brief</strong>{project.brief ? <span className="muted small"> · {project.brief.slice(0, 80)}{project.brief.length > 80 ? "…" : ""}</span>
        : <span className="muted small"> · what the film is about, in your words</span>}</summary>
      {project.style && <p className="muted small">Style ({project.template?.replace(/_/g, " ") ?? "custom"}): {project.style}</p>}
      <textarea rows={4} placeholder={TEMPLATE_HINT[project.template ?? ""] ?? "The story, tone, characters and places (use their Library names), anything that must happen…"}
        value={brief} maxLength={8000} onChange={(e) => setBrief(e.target.value)} />
      <div className="row">
        <label className="small">Target length (s)
          <input type="number" min={0} max={600} step={5} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
        </label>
        <span className="grow" />
        {dirty && <button className="ghost small" onClick={() => act(save)}>Save brief</button>}
        {project.shots.length > 0 && (
          <button className="ghost small" disabled={busy} title="Add the director's plan after the current shots"
            onClick={() => planNow(false)}>Plan more</button>
        )}
        <button className="primary small" disabled={busy}
          title={project.shots.length ? "Replace unlocked draft shots without takes with a new plan" : "The director plans scenes and shots from this brief"}
          onClick={() => planNow(project.shots.length > 0)}>{busy ? "Director is busy…" : project.shots.length ? "Re-plan drafts" : "Plan with director"}</button>
      </div>
      <p className="small muted">The director writes draft scenes and shots with references from the Library and a reason for each; you see every
        step in the chat and every change in a shot's History. Nothing renders until you start it.</p>
    </details>
  );
}

const ITEM_LABEL: Record<string, string> = {
  pending: "waiting", rendering: "rendering", done: "done", failed: "failed", skipped: "skipped", cancelled: "cancelled",
};

function BatchBar({ batch, project, act }: { batch: Batch; project: ProjectDetail; act: Act }) {
  const jobs = project.jobs;
  const now = useNow();
  const open = batch.status === "running" || batch.status === "paused";
  const problems = batch.items.filter((i) => ["failed", "skipped", "cancelled"].includes(i.status));
  if (!open && !problems.length) return null;
  const done = batch.items.filter((i) => i.status === "done").length;
  const current = batch.items.find((i) => i.status === "rendering");
  const job = current && jobs.find((j) => j.id === current.job_id);
  const shotOf = (id: string) => project.shots.find((s) => s.id === id);
  // what's left: the current render's remainder plus every waiting shot, from past renders of the same model
  const waiting = batch.items.filter((i) => i.status === "pending").map((i) => expected(shotOf(i.shot_id), project.render_rates));
  const cur = job && current ? (job.status === "running" ? jobEta(job, shotOf(current.shot_id), project.render_rates, now).left
    : expected(shotOf(current.shot_id), project.render_rates)) : 0;
  const parts = [cur, ...waiting];
  const known = parts.filter((x): x is number => x != null);
  const left = known.reduce((n, x) => n + Math.max(0, x), 0);
  const curEta = job && current && job.status === "running" ? etaText(job, shotOf(current.shot_id), project.render_rates, now) : null;
  return (
    <div className={`card batch ${batch.status}`}>
      <div className="row">
        <strong className="grow">Batch · {done} of {batch.items.length} done
          {batch.status === "paused" && " · paused"}{batch.status === "cancelled" && " · cancelled"}{batch.status === "done" && " · finished"}</strong>
        {batch.status === "running" && <button className="ghost small" title="Let the current render finish, then stop"
          onClick={() => act(() => api.batchAction(batch.id, "pause"))}>Pause</button>}
        {batch.status === "paused" && <button className="primary small" onClick={() => act(() => api.batchAction(batch.id, "resume"))}>Resume</button>}
        {open && <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask("Cancel the batch? The current render is cancelled and nothing more is started; finished shots keep their takes."))
            await api.batchAction(batch.id, "cancel");
        })}>Cancel</button>}
      </div>
      {batch.pause_reason && batch.status === "paused" && <p className="small warn">{batch.pause_reason}</p>}
      {current && <p className="small">Rendering shot {current.shot_position} “{current.title}”
        {job?.phase ? ` · ${job.phase.replace(/_/g, " ")}${job.total_steps ? ` ${job.current_step ?? 0}/${job.total_steps}` : ""}`
          : job?.queue_ahead ? ` · waiting in WanGP's queue: ${job.queue_ahead} ahead` : ""}</p>}
      {job?.total_steps ? <progress max={job.total_steps} value={job.current_step ?? 0} /> : current ? <progress /> : null}
      {batch.status === "running" && (curEta || known.length > 0) && (
        <p className="small muted eta">{curEta && <>This shot: {curEta}. </>}
          {known.length > 0 && (known.length === parts.length ? `The batch: about ${duration(left)} left.`
            : `The batch: at least ${duration(left)} left (${parts.length - known.length} shot${parts.length - known.length === 1 ? "" : "s"} use a model with no past renders here).`)}</p>
      )}
      <ol className="batch-items small">
        {batch.items.map((i) => (
          <li key={i.position} className={`bi-${i.status}`}>
            <span>{i.shot_position}. {i.title}</span> <span className="muted">· {ITEM_LABEL[i.status]}</span>
            {i.error && <span className="error block">{i.error}</span>}
            {["failed", "skipped", "cancelled"].includes(i.status) && batch.status !== "cancelled" && (
              <button className="ghost small" onClick={() => act(() => api.retryItem(batch.id, i.position))}>Retry</button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function RenderReview({ project, preselect, requestId, onClose, act }: {
  project: ProjectDetail; preselect?: string[]; requestId?: string; onClose: () => void; act: Act;
}) {
  const [data, setData] = useState<BatchReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  useEffect(() => {
    api.batchReview(project.id).then((d) => {
      setData(d);
      setPicked(new Set(d.shots.filter((r) => preselect ? preselect.includes(r.shot_id) && r.renderable && !r.blocked : r.default).map((r) => r.shot_id)));
    }).catch((e) => setError((e as Error).message));
  }, [project.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <div className="card review"><p className="error">{error}</p><button className="ghost" onClick={onClose}>Close</button></div>;
  if (!data) return <div className="card review"><p className="muted">Checking every shot with WanGP…</p></div>;
  const chosen = data.shots.filter((r) => picked.has(r.shot_id));
  const known = chosen.filter((r) => data.rates[r.model_type]);
  const gpu = known.reduce((n, r) => n + data.rates[r.model_type].s_per_frame * r.frames, 0);
  const samples = new Set(known.map((r) => r.model_type));
  const basedOn = [...samples].reduce((n, m) => n + data.rates[m].samples, 0);
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <div className="card review">
      <h3>Render storyboard</h3>
      <p className="small muted">Shots render one at a time, in storyboard order. A shot that fails is left for Retry and the rest carry on.
        Locked shots and shots that can't render are left out.</p>
      <ul className="review-list">
        {data.shots.map((r) => (
          <li key={r.shot_id} className={!r.renderable || r.blocked ? "off" : ""}>
            <label className="row">
              <input type="checkbox" checked={picked.has(r.shot_id)} disabled={!r.renderable || r.blocked} onChange={() => toggle(r.shot_id)} />
              <span className="grow"><strong>{r.position}. {r.title}</strong>
                <span className="muted small"> · {seconds(r.frames)} s · {r.template}
                  {r.locked ? " · 🔒 locked" : ""}{r.takes ? ` · ${r.takes} take${r.takes === 1 ? "" : "s"} already` : ""}
                  {!r.renderable && !r.locked ? ` · ${r.status}` : ""}</span></span>
            </label>
            {r.issues.map((i, n) => <p key={n} className={`small ${i.level === "block" ? "error" : "warn"}`}>{i.level === "block" ? "Can't render: " : ""}{i.message}</p>)}
          </li>
        ))}
      </ul>
      <p className="small">
        {chosen.length} shot{chosen.length === 1 ? "" : "s"} selected.
        {known.length ? ` About ${Math.max(1, Math.round(gpu / 60))} min of WanGP time for ${known.length === chosen.length ? "them" : `${known.length} of them`}, from ${basedOn} past render${basedOn === 1 ? "" : "s"} of the same model${known.length < chosen.length ? "; the others use a model not rendered here yet, so no estimate" : ""}.`
          : chosen.length ? " No estimate: these models haven't rendered on this machine yet." : ""}
        {data.queue?.total ? ` WanGP's queue has ${data.queue.total} task${data.queue.total === 1 ? "" : "s"} now (${data.queue.other} not FilmLab's); the batch waits behind them.` : ""}
      </p>
      <div className="actions">
        <button className="ghost" onClick={onClose}>Close</button>
        <button className="primary" disabled={!chosen.length} onClick={() => act(async () => {
          await api.createBatch(project.id, chosen.map((r) => r.shot_id), requestId);
          onClose();
        })}>Render {chosen.length} shot{chosen.length === 1 ? "" : "s"}</button>
      </div>
    </div>
  );
}
