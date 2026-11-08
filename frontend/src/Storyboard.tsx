import { type DragEvent, type FormEvent, useEffect, useMemo, useState } from "react";
import { ask } from "./Dialog";
import { api, type Batch, type BatchReview, type PlanIssue, type ProjectDetail, type Scene, type Shot, type ShotIssue, type Template, seconds } from "./api";
import { lib } from "./library-api";
import { ACTIVE_JOB, PREVIEW_HINT, ShotCard, ShotEditor } from "./Shots";
import { RedoPanel } from "./Autonomy";
import { deleteWithUndo } from "./bulk";
import { ResolutionPicker } from "./ResolutionPicker";
import { showToast } from "./notify";
import { templateOptions } from "./ShotRefs";
import { FilmStrip } from "./FilmStrip";
import { Animatic } from "./Animatic";
import { duration, etaText, expected, jobEta, PREVIEW_SHARE, useNow } from "./eta";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const UNSORTED = "unsorted";

/** The project's shots grouped by scene, in film order: plan, edit, reorder, lock and render as a batch. */
/** The film at a glance: length against the target, and every shot as a frame in film order. On a wide screen it is
 *  its own card above the panels (always in view); narrower screens keep it at the top of the storyboard. */
export function Timeline({ project, selected, onSelect, act }: {
  project: ProjectDetail; selected: string | null; onSelect: (id: string | null) => void; act: Act;
}) {
  const sceneIds = new Set(project.scenes.map((s) => s.id));
  const groups = [
    ...project.scenes.map((sc) => ({ key: sc.id, title: sc.title, shots: project.shots.filter((s) => s.scene_id === sc.id) })),
    { key: UNSORTED, title: "Unsorted", shots: project.shots.filter((s) => !s.scene_id || !sceneIds.has(s.scene_id)) },
  ];
  const total = project.shots.reduce((n, s) => n + (s.frames - 1) / 24, 0);
  return <>
    <LengthMeter total={total} target={project.target_seconds} scenes={groups.filter((g) => g.shots.length).map((g) => ({
      key: g.key, title: g.title, seconds: g.shots.reduce((n, s) => n + (s.frames - 1) / 24, 0) }))} />
    <FilmStrip project={project} selected={selected} onSelect={onSelect} act={act} />
  </>;
}

export function Storyboard({ project, templates, selected, onSelect, act, timeline = true }: {
  project: ProjectDetail; templates: Template[]; selected: string | null;
  onSelect: (id: string | null) => void; act: Act; timeline?: boolean;
}) {
  const [adding, setAdding] = useState<string | null>(null);  // scene id, UNSORTED, or null (closed)
  const [review, setReview] = useState<{ preselect?: string[]; requestId?: string } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  // Select mode: shots (and whole scenes) for a bulk action
  const [picking, setPicking] = useState(false);
  const [animatic, setAnimatic] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pickedScenes, setPickedScenes] = useState<Set<string>>(new Set());
  useEffect(() => {  // drop what's gone (deleted elsewhere, or by the bulk action itself)
    const live = new Set(project.shots.map((s) => s.id)), scenes = new Set(project.scenes.map((s) => s.id));
    setPicked((p) => new Set([...p].filter((x) => live.has(x))));
    setPickedScenes((p) => new Set([...p].filter((x) => scenes.has(x))));
  }, [project.shots, project.scenes]);
  const togglePick = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleScene = (sc: Scene) => {
    const members = project.shots.filter((s) => s.scene_id === sc.id).map((s) => s.id);
    const on = !pickedScenes.has(sc.id);
    setPickedScenes((p) => { const n = new Set(p); if (on) n.add(sc.id); else n.delete(sc.id); return n; });
    setPicked((p) => { const n = new Set(p); members.forEach((m) => (on ? n.add(m) : n.delete(m))); return n; });
  };
  const clearPicks = () => { setPicked(new Set()); setPickedScenes(new Set()); };
  const sceneIds = new Set(project.scenes.map((s) => s.id));
  const groups = useMemo(() => [
    ...project.scenes.map((sc) => ({ key: sc.id, scene: sc as Scene | null, shots: project.shots.filter((s) => s.scene_id === sc.id) })),
    { key: UNSORTED, scene: null, shots: project.shots.filter((s) => !s.scene_id || !sceneIds.has(s.scene_id)) },
  ], [project]);  // eslint-disable-line react-hooks/exhaustive-deps
  const total = project.shots.reduce((n, s) => n + (s.frames - 1) / 24, 0);
  const issues = useShotIssues(project);
  const fixable = project.shots.filter((s) => !s.locked && ["draft", "failed", "pending_approval", "ready"].includes(s.status)
    && issues[s.id]?.some((i) => i.level === "warn")).map((s) => s.id);
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
    const movable = !busy && !picking;
    return (
      <ShotCard key={s.id} shot={s} templates={templates} selected={s.id === selected} job={job} issues={issues[s.id]}
        takes={project.takes.filter((t) => t.shot_id === s.id)}
        approval={project.approvals.find((a) => a.shot_id === s.id)}
        onSelect={() => onSelect(s.id === selected ? null : s.id)} act={act}
        pick={picking ? { on: picked.has(s.id), toggle: () => togglePick(s.id) } : undefined}
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
          {project.shots.length} shot{project.shots.length === 1 ? "" : "s"}
          {Object.keys(issues).length > 0 && <span className="warn"> · ⚠ {Object.keys(issues).length} with warnings</span>}
        </span>
        {fixable.length > 0 && (
          <button className="ghost" disabled={project.director_busy}
            title="The director fixes only what's flagged in each prompt (a missing place or time of day, a picture it names that the shot doesn't have, …) and keeps the rest; every change shows in the shot's History"
            onClick={() => act(() => api.fixPrompts(project.id, fixable))}>✨ Fix {fixable.length} with director</button>
        )}
        {project.shots.length > 0 && (
          <button className={`ghost ${picking ? "on" : ""}`} aria-pressed={picking}
            title="Select shots or whole scenes to render, lock, change or delete them together"
            onClick={() => { setPicking((v) => !v); clearPicks(); }}>{picking ? "Done" : "Select"}</button>
        )}
        {project.shots.length > 0 && <button className="ghost" title="Play the storyboard as timed stills, with transitions and the edit's music"
          onClick={() => setAnimatic(true)}>▶ Animatic</button>}
        <button className="ghost" onClick={() => act(() => api.createScene(project.id, { title: `Scene ${project.scenes.length + 1}` }))}>+ Scene</button>
        {(project.shots.length > 0 || project.scenes.length > 0) && (
          <button className="ghost danger-text" disabled={project.director_busy}
            title="Start over: every shot and scene goes to Recently deleted with its takes (locked shots stay), and the director forgets the old plan"
            onClick={() => act(() => clearStoryboard(project))}>Clear…</button>
        )}
        <button className="primary" disabled={!project.shots.length || !!(project.batch && ["running", "paused"].includes(project.batch.status))}
          onClick={() => setReview({})}>Render storyboard…</button>
      </div>
      {timeline && <Timeline project={project} selected={selected} onSelect={onSelect} act={act} />}
      {project.director_busy && project.planning && <PlanProgress project={project} act={act} />}
      {animatic && <Animatic project={project} onClose={() => setAnimatic(false)} />}
      <BriefPanel project={project} act={act} />
      {project.batch_requests.map((r) => (
        <div key={r.id} className="card approval row">
          <span className="grow small">Director asks to render {r.shot_ids.length} shot{r.shot_ids.length === 1 ? "" : "s"} as a batch
            {r.note ? `: “${r.note}”` : "."}</span>
          <button className="ghost small" onClick={() => act(() => api.rejectBatchRequest(r.id))}>Reject</button>
          <button className="primary small" onClick={() => setReview({ preselect: r.shot_ids, requestId: r.id })}>Review…</button>
        </div>
      ))}
      {picking && (
        <BulkBar project={project} templates={templates} shotIds={[...picked]} sceneIds={[...pickedScenes]} act={act}
          onPick={(ids) => { setPicked(new Set(ids)); setPickedScenes(new Set()); }} onClear={clearPicks}
          onRender={(ids) => setReview({ preselect: ids })} />
      )}
      {project.batch && <BatchBar batch={project.batch} project={project} act={act} />}
      <RedoPanel project={project} act={act} />
      {review && <RenderReview project={project} {...review} onClose={() => setReview(null)} act={act} />}
      {groups.map((g) => (g.scene || g.shots.length || !project.scenes.length) && (
        <div key={g.key} className={`scene ${dragging ? "drop-target" : ""}`}
          onDragOver={(e) => { if (dragging) e.preventDefault(); }} onDrop={(e) => drop(e, g.key)}>
          {g.scene ? <SceneHeader scene={g.scene} index={project.scenes.indexOf(g.scene)} count={project.scenes.length}
            shots={g.shots} act={act} onAdd={() => setAdding(g.key)} filmSeconds={total} target={project.target_seconds}
            pick={picking ? { on: pickedScenes.has(g.scene.id), toggle: () => toggleScene(g.scene!) } : undefined} />
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

function SceneHeader({ scene, index, count, shots, act, onAdd, pick, filmSeconds, target }: {
  scene: Scene; index: number; count: number; shots: Shot[]; act: Act; onAdd: () => void;
  pick?: { on: boolean; toggle: () => void }; filmSeconds: number; target: number | null;
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
      {pick && <input type="checkbox" checked={pick.on} onChange={pick.toggle} aria-label={`Select the whole scene ${scene.title}`}
        title="The whole scene: deleting it removes the scene too" />}
      <h3>{scene.title}</h3>
      <span className="muted small grow">{[scene.time_place, scene.mood].filter(Boolean).join(" · ")}
        {shots.length ? ` · ${shots.length} shot${shots.length === 1 ? "" : "s"}, ${secs.toFixed(1)} s` : " · empty"}
        {shots.length > 0 && (target || filmSeconds) ? ` (${Math.round(100 * secs / (target || filmSeconds))}% of the ${target ? "target" : "film"})` : ""}</span>
      <button className="ghost small" disabled={index === 0} aria-label="Move scene up" onClick={() => act(() => api.moveScene(scene.id, index - 1))}>↑</button>
      <button className="ghost small" disabled={index === count - 1} aria-label="Move scene down" onClick={() => act(() => api.moveScene(scene.id, index + 1))}>↓</button>
      <button className="ghost small" onClick={() => setEditing(true)}>Edit</button>
      <button className="ghost small" onClick={onAdd}>+ Shot</button>
      <button className="ghost small danger-text" title="The scene goes and its shots to Recently deleted (Undo right after; locked shots stay)"
        onClick={() => act(() => deleteWithUndo(scene.project_id, { scene_ids: [scene.id] },
          `Deleted scene “${scene.title}”${shots.length ? ` and its ${shots.length} shot${shots.length === 1 ? "" : "s"}` : ""}`))}>Delete</button>
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
  const check = usePlanCheck(project);
  const save = () => api.updateProject(project.id, { brief, target_seconds: target || 0 });
  const planNow = (replace: boolean) => act(async () => {
    if (brief.trim().length < 10) throw new Error("Write a brief first: a sentence or two is enough.");
    if (dirty) await save();
    const open = (await check.refresh()).filter((i) => i.level === "warn");
    if (open.length && !await ask(open.map((i) => `• ${i.message}`).join("\n")
      + "\n\nCancel to use the fixes in the Brief first.", { title: "Plan anyway?", confirm: "Plan anyway" })) return;
    if (replace && !await ask("Re-plan: the director's new plan replaces unlocked draft shots without takes "
      + "(they go to Recently deleted). Locked, approved and rendered shots stay. Go ahead?")) return;
    await api.plan(project.id, replace);
  });
  return (
    <details className="card brief" open={!project.shots.length || check.issues.some((i) => i.level === "warn")}>
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
      {check.issues.length > 0 && (
        <div className="plan-check" role="group" aria-label="Before you plan">
          <strong className="small">Before you plan</strong>
          {check.issues.map((i) => (
            <div key={i.id} className={`plan-issue ${i.level}`}>
              <p className="small">{i.message}</p>
              <div className="row">
                {i.fixes.map((f) => (
                  <button key={f.label} className="primary small" disabled={busy}
                    onClick={() => act(async () => { await api.updateProject(project.id, f.patch); })}>{f.label}</button>
                ))}
                <button className="ghost small" title="Don't mention this again for this project" onClick={() => check.dismiss(i.id)}>Keep as is</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="small muted">The director writes draft scenes and shots with references from the Library and a reason for each; you see every
        step in the chat and every change in a shot's History. Nothing renders until you start it.</p>
    </details>
  );
}

/** The film's length against the target, with each scene's share; over the target shows as a warning. */
function LengthMeter({ total, target, scenes }: {
  total: number; target: number | null; scenes: { key: string; title: string; seconds: number }[];
}) {
  if (!total && !target) return null;
  const scale = Math.max(total, target ?? 0) || 1;
  const over = !!target && total > target * 1.1;
  const under = !!target && total < target * 0.9;
  return (
    <div className="length-meter" title="The film's length, scene by scene, against the target">
      <div className={`lm-bar ${over ? "over" : ""}`} role="img"
        aria-label={`${total.toFixed(1)} seconds${target ? ` of a ${target} second target` : ""}`}>
        {scenes.map((s, i) => (
          <span key={s.key} className={`lm-scene c${i % 4}`} style={{ width: `${(100 * s.seconds) / scale}%` }}
            title={`${s.title}: ${s.seconds.toFixed(1)} s`} />
        ))}
        {target ? <span className="lm-target" style={{ left: `${Math.min(100, (100 * target) / scale)}%` }} /> : null}
      </div>
      <span className={`small ${over ? "warn" : "muted"}`}>
        {total.toFixed(1)} s{target ? ` of ${target} s` : ""}
        {over ? ` · ${(total - target!).toFixed(0)} s over` : under ? ` · ${(target! - total).toFixed(0)} s to go` : target ? " · on target" : ""}
      </span>
    </div>
  );
}

/** While the director writes a plan in parts: how far it got, and Stop (what's saved stays). */
function PlanProgress({ project, act }: { project: ProjectDetail; act: Act }) {
  const p = project.planning!;
  const now = useNow(1000);
  const target = project.target_seconds || 0;
  const ran = Math.max(0, (now - Date.parse(p.started_at)) / 1000);
  return (
    <div className="card plan-progress" role="status">
      <div className="row">
        <strong className="grow">Planning with the director
          <span className="muted small"> · {p.parts ? `part ${p.parts} saved, writing the next` : "writing the first part"}
            {` · ${duration(ran)}`}</span></strong>
        <button className="ghost small danger-text" title="Stop now: the parts already saved stay, and Plan more carries on from them"
          onClick={() => act(() => api.stopChat(project.id))}>Stop</button>
      </div>
      {target ? <progress max={target} value={Math.min(p.seconds, target)} aria-label="seconds planned" /> : <progress />}
      <p className="small muted">{p.seconds.toFixed(0)} s planned{target ? ` of ${target} s` : ""} in {p.scenes} scene{p.scenes === 1 ? "" : "s"};
        new scenes appear below as each part is saved.</p>
    </div>
  );
}

/** Every shot's pre-render warnings (backend /checks), refreshed when shots change. */
function useShotIssues(project: ProjectDetail) {
  const [issues, setIssues] = useState<Record<string, ShotIssue[]>>({});
  const key = project.shots.map((s) => `${s.id}:${s.updated_at}`).join(",") + project.scenes.map((s) => s.time_place).join("|");
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.projectChecks(project.id).then((r) => setIssues(r.shots)).catch(() => undefined);
    }, 400);
    return () => window.clearTimeout(t);
  }, [project.id, key]);
  return issues;
}

const KEEP = "";
const DEFAULTS = "__defaults";

/** Select mode: what to do with the chosen shots (and whole scenes) at once. */
function BulkBar({ project, templates, shotIds, sceneIds, act, onPick, onClear, onRender }: {
  project: ProjectDetail; templates: Template[]; shotIds: string[]; sceneIds: string[]; act: Act;
  onPick: (ids: string[]) => void; onClear: () => void; onRender: (ids: string[]) => void;
}) {
  const [changing, setChanging] = useState(false);
  const [f, setF] = useState({ template: KEEP, resolution: KEEP, seconds: "" });
  const n = shotIds.length;
  const hasTakes = (id: string) => project.takes.some((t) => t.shot_id === id);
  const drafts = project.shots.filter((s) => s.status === "draft" && !s.locked && !hasTakes(s.id)).map((s) => s.id);
  const order = project.shots.map((s) => s.id);
  const ids = order.filter((id) => shotIds.includes(id));  // storyboard order
  const report = (what: string, r: { done: string[]; skipped: { position: number; title: string; reason: string }[] }) =>
    showToast({ title: `${what} ${r.done.length} shot${r.done.length === 1 ? "" : "s"}`, kind: r.skipped.length ? "bad" : "ok",
      body: r.skipped.length ? `Skipped ${r.skipped.map((s) => `${s.position}. ${s.title} (${s.reason})`).join("; ")}` : "" });
  const lock = (on: boolean) => act(async () => report(on ? "Locked" : "Unlocked",
    await api.bulk(project.id, { action: on ? "lock" : "unlock", shot_ids: ids })));
  const apply = () => act(async () => {
    const fields: { template?: string; resolution?: string; frames?: number } = {};
    if (f.template) fields.template = f.template === DEFAULTS ? "" : f.template;
    if (f.resolution) fields.resolution = f.resolution;
    if (f.seconds) fields.frames = Math.round(Math.round(Number(f.seconds) * 24) / 8) * 8 + 1;
    if (!Object.keys(fields).length) throw new Error("Choose a model, a size or a length to change.");
    report("Changed", await api.bulk(project.id, { action: "set", shot_ids: ids, fields }));
    setChanging(false);
    setF({ template: KEEP, resolution: KEEP, seconds: "" });
  });
  const remove = () => act(async () => {
    await deleteWithUndo(project.id, { shot_ids: ids, scene_ids: sceneIds },
      `Deleted ${n} shot${n === 1 ? "" : "s"}${sceneIds.length ? ` and ${sceneIds.length} scene${sceneIds.length === 1 ? "" : "s"}` : ""}`);
    onClear();
  });
  const { plain, refs } = templateOptions(templates);
  return (
    <div className="card bulk-bar" role="toolbar" aria-label="Selected shots">
      <div className="row">
        <strong className="grow">{n ? `${n} shot${n === 1 ? "" : "s"} selected` : "Select shots"}
          {sceneIds.length ? <span className="muted small"> · {sceneIds.length} whole scene{sceneIds.length === 1 ? "" : "s"}</span> : null}</strong>
        <span className="small muted">Select:</span>
        <button className="ghost small" onClick={() => onPick(order)}>All</button>
        <button className="ghost small" disabled={!drafts.length} title="Unlocked draft shots that have no takes yet: what a re-plan would replace"
          onClick={() => onPick(drafts)}>Unlocked drafts ({drafts.length})</button>
        <button className="ghost small" disabled={!n && !sceneIds.length} onClick={onClear}>None</button>
      </div>
      <div className="row">
        <button className="primary small" disabled={!n} onClick={() => onRender(ids)}>Render…</button>
        <button className="ghost small" disabled={!n} onClick={() => lock(true)}>Lock</button>
        <button className="ghost small" disabled={!n} onClick={() => lock(false)}>Unlock</button>
        <button className={`ghost small ${changing ? "on" : ""}`} disabled={!n} aria-expanded={changing}
          onClick={() => setChanging((v) => !v)}>Change…</button>
        <span className="grow" />
        <button className="ghost small danger-text" disabled={!n && !sceneIds.length} onClick={remove}>Delete</button>
      </div>
      {changing && n > 0 && (
        <div className="row bulk-change">
          <label className="small">Model
            <select value={f.template} onChange={(e) => setF({ ...f, template: e.target.value })}>
              <option value={KEEP}>Keep each shot's</option>
              <option value={DEFAULTS}>Follow the defaults</option>
              {plain.length > 0 && <optgroup label="Without references">{plain.map((t) => <option key={t.template} value={t.template}>{t.template}</option>)}</optgroup>}
              {refs.length > 0 && <optgroup label="With references">{refs.map((t) => <option key={t.template} value={t.template}>{t.template}</option>)}</optgroup>}
            </select>
          </label>
          <div className="small sm-field">Size
            <ResolutionPicker value={f.resolution} keep={{ value: KEEP, label: "Keep each shot's" }}
              onChange={(resolution) => setF({ ...f, resolution })} /></div>
          <label className="small">Length (s)
            <input type="number" min={1} max={20} step={0.5} placeholder="keep" value={f.seconds}
              onChange={(e) => setF({ ...f, seconds: e.target.value })} />
          </label>
          <button className="primary small" onClick={apply}>Apply to {n}</button>
          <p className="small muted block">Locked, approved and rendering shots are skipped; every change shows in each shot's History.</p>
        </div>
      )}
    </div>
  );
}

/** The brief check (backend plan_check.py), refreshed when the brief or the settings change. "Keep as is" hides an
 *  issue for this project in this browser; a changed brief or setting gives it a new id, so it shows again. */
function usePlanCheck(project: ProjectDetail) {
  const key = `filmlab:plan-check:${project.id}`;
  const [all, setAll] = useState<PlanIssue[]>([]);
  const [hidden, setHidden] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem(key) ?? "[]"); } catch { return []; } });
  const refresh = async () => {
    try { const { issues } = await api.planCheck(project.id); setAll(issues); return issues.filter((i) => !hidden.includes(i.id)); }
    catch { return []; }  // a check that can't run never blocks planning
  };
  useEffect(() => { refresh(); }, [project.id, project.brief, project.target_seconds, project.style, project.template]);  // eslint-disable-line react-hooks/exhaustive-deps
  const dismiss = (id: string) => {
    const next = [...hidden, id];
    setHidden(next);
    try { localStorage.setItem(key, JSON.stringify(next.slice(-50))); } catch { /* private window: only for now */ }
  };
  return { issues: all.filter((i) => !hidden.includes(i.id)), refresh, dismiss };
}

const ITEM_LABEL: Record<string, string> = {
  pending: "waiting", rendering: "rendering", done: "done", failed: "failed", skipped: "skipped", cancelled: "cancelled",
};

/** Start over: every unlocked shot and every scene to Recently deleted (Undo right after), and a fresh director
 *  conversation so the next plan doesn't build on the old one. */
async function clearStoryboard(project: ProjectDetail) {
  const locked = project.shots.filter((s) => s.locked).length;
  const n = project.shots.length - locked;
  if (!await ask(`Clear the storyboard? ${n} shot${n === 1 ? "" : "s"} and ${project.scenes.length} scene`
    + `${project.scenes.length === 1 ? "" : "s"} go to Recently deleted, with their takes`
    + `${locked ? `; the ${locked} locked shot${locked === 1 ? "" : "s"} and ${locked === 1 ? "its scene stay" : "their scenes stay"}` : ""}.`
    + " The director starts a fresh conversation, so your next plan starts clean. You can Undo right after.",
  { confirm: "Delete" })) return;
  await deleteWithUndo(project.id, { shot_ids: project.shots.map((s) => s.id), scene_ids: project.scenes.map((s) => s.id) },
    "Storyboard cleared");
  await api.freshChat(project.id).catch(() => undefined);  // the director may be answering; the plan still works
}

function BatchBar({ batch, project, act }: { batch: Batch; project: ProjectDetail; act: Act }) {
  const jobs = project.jobs;
  const now = useNow();
  const open = batch.status === "running" || batch.status === "paused";
  const problems = batch.items.filter((i) => ["failed", "skipped", "cancelled"].includes(i.status));
  if (!open && !problems.length) return null;
  const done = batch.items.filter((i) => i.status === "done").length;
  const pendingCount = batch.items.filter((i) => i.status === "pending").length;
  const current = batch.items.find((i) => i.status === "rendering");
  const job = current && jobs.find((j) => j.id === current.job_id);
  const shotOf = (id: string) => project.shots.find((s) => s.id === id);
  // what's left: the current render's remainder plus every waiting shot, from past renders of the same model
  const waiting = batch.items.filter((i) => i.status === "pending").map((i) => expected(shotOf(i.shot_id), project.render_rates, !!batch.preview));
  const cur = job && current ? (job.status === "running" ? jobEta(job, shotOf(current.shot_id), project.render_rates, now).left
    : expected(shotOf(current.shot_id), project.render_rates, !!batch.preview)) : 0;
  const parts = [cur, ...waiting];
  const known = parts.filter((x): x is number => x != null);
  const left = known.reduce((n, x) => n + Math.max(0, x), 0);
  const curEta = job && current && job.status === "running" ? etaText(job, shotOf(current.shot_id), project.render_rates, now) : null;
  return (
    <div className={`card batch ${batch.status}`}>
      <div className="row">
        <strong className="grow">{batch.preview ? "Preview batch" : "Batch"} · {done} of {batch.items.length} done
          {batch.status === "paused" && " · paused"}{batch.status === "cancelled" && " · cancelled"}{batch.status === "done" && " · finished"}</strong>
        {batch.status === "running" && <button className="ghost small" title="Let the current render finish, then stop"
          onClick={() => act(() => api.batchAction(batch.id, "pause"))}>Pause</button>}
        {batch.status === "paused" && <button className="primary small" onClick={() => act(() => api.batchAction(batch.id, "resume"))}>
          {batch.start_at ? "Start now" : "Resume"}</button>}
        {open && <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask("Cancel the batch? The current render is cancelled and nothing more is started; finished shots keep their takes."))
            await api.batchAction(batch.id, "cancel");
        })}>Cancel</button>}
      </div>
      {batch.status === "paused" && batch.start_at ? (
        <p className="small">Starts by itself at <strong>{new Date(batch.start_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</strong>
          {" "}({duration(Math.max(0, (Date.parse(batch.start_at) - now) / 1000))} from now). FilmLab must be running then.</p>
      ) : batch.pause_reason && batch.status === "paused" && <p className="small warn">{batch.pause_reason}</p>}
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
            {i.status === "pending" && open && pendingCount > 1 && (
              <span className="move-controls">
                <button className="ghost small" aria-label="Render sooner" title="Render sooner"
                  onClick={() => act(() => api.moveBatchItem(batch.id, i.position, "up"))}>↑</button>
                <button className="ghost small" aria-label="Render later" title="Render later"
                  onClick={() => act(() => api.moveBatchItem(batch.id, i.position, "down"))}>↓</button>
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A datetime-local value for a Date (the browser's own time zone). */
function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 11 PM tonight, or in an hour if that has passed. */
function tonight(): string {
  const d = new Date();
  d.setHours(23, 0, 0, 0);
  return localInput(d.getTime() > Date.now() ? d : new Date(Date.now() + 3600_000));
}

/** "Done around 9:10 PM": the batch's estimate from now (or from its start time). */
function finishText(seconds: number, startAt: string, partial: boolean): string {
  const start = startAt ? new Date(startAt).getTime() : Date.now();
  const end = new Date(start + seconds * 1000);
  const day = end.toDateString() === new Date().toDateString() ? "" : `${end.toLocaleDateString([], { weekday: "short" })} `;
  return `${partial ? "At the earliest done" : "Done"} around ${day}${end.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`;
}

function RenderReview({ project, preselect, requestId, onClose, act }: {
  project: ProjectDetail; preselect?: string[]; requestId?: string; onClose: () => void; act: Act;
}) {
  const [data, setData] = useState<BatchReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState(false);
  const [startAt, setStartAt] = useState("");  // datetime-local, empty = now
  useEffect(() => {
    api.batchReview(project.id).then((d) => {
      setData(d);
      setPicked(new Set(d.shots.filter((r) => preselect ? preselect.includes(r.shot_id) && r.renderable && !r.blocked : r.default).map((r) => r.shot_id)));
    }).catch((e) => setError((e as Error).message));
  }, [project.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <div className="card review"><p className="error">{error}</p><button className="ghost" onClick={onClose}>Close</button></div>;
  if (!data) return <div className="card review"><p className="muted">Checking every shot with WanGP…</p></div>;
  const chosen = data.shots.filter((r) => picked.has(r.shot_id));
  // a preview batch uses past preview renders of the model, else a share of its full-quality rate (eta.ts)
  const rateOf = (m: string) => (preview && data.rates[`${m}:preview`]) || data.rates[m];
  const share = (m: string) => (preview && !data.rates[`${m}:preview`] ? PREVIEW_SHARE : 1);
  const known = chosen.filter((r) => rateOf(r.model_type));
  const gpu = known.reduce((n, r) => n + rateOf(r.model_type).s_per_frame * r.frames * share(r.model_type), 0);
  const samples = new Set(known.map((r) => r.model_type));
  const basedOn = [...samples].reduce((n, m) => n + rateOf(m).samples, 0);
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
        {preview && known.some((r) => share(r.model_type) !== 1) ? " (A guess for previews: none of these models has rendered a preview here yet.)" : ""}
        {known.length > 0 && !data.queue?.total ? ` ${finishText(gpu + known.length * 15, startAt, known.length < chosen.length)}` : ""}
        {data.queue?.total ? ` WanGP's queue has ${data.queue.total} task${data.queue.total === 1 ? "" : "s"} now (${data.queue.other} not FilmLab's); the batch waits behind them.` : ""}
      </p>
      <label className="row small start-at">
        <span>Start</span>
        <select value={startAt ? "later" : "now"} onChange={(e) => setStartAt(e.target.value === "later" ? tonight() : "")} aria-label="When to start">
          <option value="now">now</option>
          <option value="later">at a set time (render overnight)</option>
        </select>
        {startAt && <input type="datetime-local" value={startAt} min={localInput(new Date())} onChange={(e) => setStartAt(e.target.value)} />}
      </label>
      <label className="row small preview-toggle" title={PREVIEW_HINT}>
        <input type="checkbox" className="switch" checked={preview} onChange={(e) => setPreview(e.target.checked)} />
        <span><strong>Preview first</strong>: quick takes at half the width and height, to check timing, framing and continuity
          across the film before spending full-quality render time.</span>
      </label>
      <div className="actions">
        <button className="ghost" onClick={onClose}>Close</button>
        <button className="primary" disabled={!chosen.length} onClick={() => act(async () => {
          await api.createBatch(project.id, chosen.map((r) => r.shot_id), requestId, preview,
            startAt ? new Date(startAt).toISOString() : null);
          onClose();
        })}>{startAt ? "Schedule" : preview ? "Preview" : "Render"} {chosen.length} shot{chosen.length === 1 ? "" : "s"}</button>
      </div>
    </div>
  );
}
