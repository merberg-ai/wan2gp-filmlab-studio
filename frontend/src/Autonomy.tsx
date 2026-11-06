import { useState } from "react";
import { ask } from "./Dialog";
import { api, type Inspection, type ProjectDetail, type RedoRun, type Take } from "./api";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const CAP_LABEL: Record<string, string> = {
  max_retries: "Redos per shot", max_renders: "Renders in total", gpu_minutes: "GPU minutes", wall_minutes: "Time limit (min)",
};
const CAP_DEFAULT = { max_retries: 2, max_renders: 6, gpu_minutes: 20, wall_minutes: 60 };
const RUN_STATE: Record<string, string> = {
  asked: "asking the director", rendering: "rendering", inspecting: "inspecting", fixed: "fixed", given_up: "left for you",
  render_failed: "render failed",
};

/** FilmLab's inspection of one take: technical checks and the vision check of four keyframes. */
export function InspectionView({ take, inspection, act }: { take: Take; inspection?: Inspection; act: Act }) {
  const again = () => act(() => api.inspectTake(take.id));
  if (!inspection) {
    return <div className="inspection small muted row"><span className="grow">Not inspected.</span>
      <button className="ghost small" onClick={again}>Inspect</button></div>;
  }
  if (inspection.status === "running") return <div className="inspection small muted">Inspecting this take…</div>;
  if (inspection.status === "failed") {
    return <div className="inspection small row"><span className="grow error">Inspection failed.</span>
      <button className="ghost small" onClick={again}>Try again</button></div>;
  }
  const ok = inspection.verdict === "ok";
  return (
    <div className={`inspection small ${ok ? "ok" : "flagged"}`}>
      <div className="row">
        <span className="grow"><strong>{ok ? "✓ Looks right" : "⚠ Flagged"}</strong>
          {" · "}{ok ? inspection.vision_summary || inspection.summary : inspection.summary}</span>
        <button className="ghost small" title="Run the technical and vision checks again" onClick={again}>Inspect again</button>
      </div>
      {inspection.vision_error && <span className="muted block">{inspection.vision_error}; only the technical checks ran.</span>}
      {inspection.vision_skipped && <span className="muted block">Technical checks only: the vision check is off
        (<a className="link" href="#/settings">Settings → Take inspection</a>).</span>}
      {inspection.sheet && (
        <a href={api.sheetUrl(take.id)} target="_blank" rel="noreferrer" title="The four keyframes the check looked at">
          <img className="sheet" src={api.sheetUrl(take.id)} alt="Four keyframes of this take" loading="lazy" />
        </a>
      )}
    </div>
  );
}

/** Redo proposals, the bounded redo run (A3), and the GPU hold, at the top of the storyboard. */
export function RedoPanel({ project, act }: { project: ProjectDetail; act: Act }) {
  const [starting, setStarting] = useState(false);
  const redo = project.redo;
  if (!redo) return null;
  const pending = redo.proposals.filter((p) => p.status === "pending");
  const run = redo.run;
  const open = run && (run.status === "running" || run.status === "paused");
  const recentRun = run && !open && Date.now() - Date.parse(run.created_at) < 6 * 3600_000 ? run : null;
  const hold = (on: boolean) => act(async () => {
    if (on && !await ask("Hold all GPU work? Nothing new renders or generates (here or in other projects), "
      + "running batches pause and redo runs stop. Renders already running finish unless you also cancel them.")) return;
    const cancel = on && await ask("Also cancel FilmLab renders that are running now?");
    await api.setGpuHold(on, cancel);
  });

  return (
    <>
      {redo.hold ? (
        <div className="card hold row"><span className="grow small"><strong>⏸ {redo.hold}</strong></span>
          <button className="primary small" onClick={() => hold(false)}>Release</button></div>
      ) : null}
      {pending.map((p) => (
        <div key={p.id} className="card approval redo">
          <div className="row">
            <span className="grow small">Director proposes a redo of shot {p.position} “{p.title}”: <em>{p.reason}</em></span>
            {p.run_id ? <span className="chip">in the redo run</span> : <>
              <button className="ghost small" onClick={() => act(() => api.rejectRedo(p.id))}>Reject</button>
              <button className="primary small" disabled={!!redo.hold} onClick={() => act(() => api.acceptRedo(p.id))}>Accept &amp; render</button>
            </>}
          </div>
          {p.changes.prompt && (
            <details className="small"><summary>New prompt</summary>
              <p className="diff-new">{p.changes.prompt}</p><p className="diff-old muted">Was: {p.current_prompt}</p></details>
          )}
          {p.changes.seed != null && <span className="muted small">seed {p.changes.seed === -1 ? "new random" : p.changes.seed}</span>}
        </div>
      ))}
      {(open || recentRun) && <RunBar run={(open ? run : recentRun)!} project={project} act={act} />}
      {!open && redo.needs_redo.length > 0 && (
        <div className="card redo-hint row small">
          <span className="grow">⚠ {redo.needs_redo.length} shot{redo.needs_redo.length === 1 ? "" : "s"} flagged by inspection:{" "}
            {redo.needs_redo.map((n) => `${n.position}. ${n.title}`).join(", ")}.
            {project.autonomy === "A3" ? "" : " Ask the director to propose redos, or turn on bounded redo (A3) in Project settings."}</span>
          {project.autonomy === "A3" && <button className="primary small" disabled={!!redo.hold} onClick={() => setStarting(true)}>Fix flagged shots…</button>}
        </div>
      )}
      {starting && <StartRun project={project} onClose={() => setStarting(false)} act={act} />}
      {!redo.hold && (open || project.jobs.some((j) => ["submitting", "queued", "running"].includes(j.status))) && (
        <div className="row small"><span className="grow" />
          <button className="ghost small danger-text" title="Stop all new GPU work from FilmLab, everywhere" onClick={() => hold(true)}>⏸ Hold GPU work</button>
        </div>
      )}
    </>
  );
}

function RunBar({ run, project, act }: { run: RedoRun; project: ProjectDetail; act: Act }) {
  const open = run.status === "running" || run.status === "paused";
  const title = (id: string) => project.shots.find((s) => s.id === id);
  const mins = Math.round((Date.now() - Date.parse(run.created_at)) / 60000);
  return (
    <div className={`card batch run ${run.status}`}>
      <div className="row">
        <strong className="grow">Redo run · {run.status === "running" ? "running" : run.status === "paused" ? "paused" : run.status === "done" ? "finished" : "stopped"}</strong>
        {run.status === "running" && <button className="ghost small" title="Let the current render finish, then stop"
          onClick={() => act(() => api.runAction(run.id, "pause"))}>Pause</button>}
        {run.status === "paused" && <button className="primary small" onClick={() => act(() => api.runAction(run.id, "resume"))}>Resume</button>}
        {open && <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask("Stop the redo run? A render in progress is cancelled; takes made so far stay.")) await api.runAction(run.id, "stop");
        })}>Stop</button>}
      </div>
      <p className="small muted">
        {run.used_renders} of {run.caps.max_renders} renders · {run.used_gpu_min} of {run.caps.gpu_minutes} GPU min
        · {open ? `${mins} of ${run.caps.wall_minutes} min` : ""} · up to {run.caps.max_retries} redo{run.caps.max_retries === 1 ? "" : "s"} a shot
      </p>
      {run.stop_reason && <p className={`small ${run.status === "done" ? "" : "warn"}`}>{run.stop_reason}</p>}
      <ol className="batch-items small">
        {run.shot_ids.map((id) => {
          const s = title(id), st = run.shot_state[id] ?? {};
          return <li key={id}>{s ? `${s.position}. ${s.title}` : "deleted shot"} <span className="muted">· {RUN_STATE[st.state ?? ""] ?? "waiting"}
            {st.attempts ? ` · ${st.attempts} redo${st.attempts === 1 ? "" : "s"}` : ""}{st.note && st.state === "given_up" ? ` · ${st.note}` : ""}</span></li>;
        })}
      </ol>
      <details className="small"><summary>What happened</summary>
        <ul className="run-log">{[...run.log].reverse().slice(0, 30).map((e, i) => (
          <li key={i}><span className="muted">{new Date(e.at).toLocaleTimeString()}</span> {e.text}</li>))}</ul>
      </details>
    </div>
  );
}

function StartRun({ project, onClose, act }: { project: ProjectDetail; onClose: () => void; act: Act }) {
  const [caps, setCaps] = useState<Record<string, number>>(CAP_DEFAULT);
  const [picked, setPicked] = useState<Set<string>>(new Set(project.redo.needs_redo.map((n) => n.shot_id)));
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <div className="card review" role="dialog" aria-label="Fix flagged shots">
      <h3>Fix flagged shots</h3>
      <p className="small muted">The director proposes a fix for each shot, FilmLab renders it and inspects the new take, and
        repeats until the take looks right or a limit is reached. Locked shots, takes you chose or marked good, and your verdicts
        are never touched. You can pause or stop at any time.</p>
      <ul className="review-list small">
        {project.redo.needs_redo.map((n) => (
          <li key={n.shot_id}><label><input type="checkbox" checked={picked.has(n.shot_id)} onChange={() => toggle(n.shot_id)} />
            {" "}{n.position}. {n.title} <span className="muted">· {n.why}</span></label></li>
        ))}
      </ul>
      <div className="row caps">
        {Object.keys(CAP_DEFAULT).map((k) => (
          <label key={k} className="small">{CAP_LABEL[k]}
            <input type="number" min={1} value={caps[k]} onChange={(e) => setCaps({ ...caps, [k]: Number(e.target.value) })} />
          </label>
        ))}
      </div>
      <div className="actions">
        <button className="ghost" onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!picked.size} onClick={() => act(async () => {
          await api.startRun(project.id, { shot_ids: [...picked], caps }); onClose();
        })}>Start ({picked.size} shot{picked.size === 1 ? "" : "s"})</button>
      </div>
    </div>
  );
}

export const AUTONOMY: Record<string, string> = {
  A0: "A0 · Draft: the director plans and proposes only",
  A1: "A1 · Approved shots: you approve each render",
  A2: "A2 · Approved batches: you start batches; the director proposes redos for you to accept",
  A3: "A3 · Bounded redo: you can start redo runs where FilmLab renders the director's fixes within your limits",
};
