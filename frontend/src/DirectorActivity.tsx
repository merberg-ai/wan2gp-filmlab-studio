/** The director's steps in plain words: what it is doing now while it answers, and what it did for each reply. */
import { useEffect, useState } from "react";

export interface Step { name: string; arguments?: string; ok: boolean | null }

const LABEL: Record<string, string> = {
  get_project: "Read the project", plan_storyboard: "Planned the storyboard", update_scene: "Updated a scene",
  move_shot: "Moved a shot", request_storyboard_render: "Asked you to render the storyboard",
  list_video_templates: "Checked the video templates", list_assets: "Looked through the Library",
  list_presets: "Checked the presets", apply_preset: "Applied a preset", get_asset: "Looked at a Library item",
  assign_refs: "Assigned references", request_image: "Asked for a new image", draft_shot: "Drafted a shot",
  update_shot: "Updated a shot", validate_shot: "Checked a shot with WanGP", request_render: "Asked you to render a shot",
  get_render_status: "Checked the renders", review_takes: "Reviewed takes", get_edit: "Read the edit",
  suggest_edit: "Suggested an edit change", inspect_take: "Inspected a take", propose_redo: "Proposed a redo",
};

function parse(args?: string): Record<string, unknown> {
  try { const v = JSON.parse(args || "{}"); return v && typeof v === "object" ? v : {}; } catch { return {}; }
}

/** A step's label, with detail from its arguments where that helps ("Planned 2 scenes, 6 shots"). */
export function stepLabel(s: Step): string {
  let a = parse(s.arguments);
  const inner = a[s.name];  // arguments the model nested under the tool's name; FilmLab unwraps them
  if (inner && typeof inner === "object") a = inner as Record<string, unknown>;
  const base = LABEL[s.name] ?? s.name.replace(/_/g, " ");
  if (s.name === "plan_storyboard" && Array.isArray(a.scenes)) {
    const scenes = a.scenes as { shots?: unknown[] }[];
    const shots = scenes.reduce((n, sc) => n + (Array.isArray(sc?.shots) ? sc.shots.length : 0), 0);
    return `Planned ${scenes.length} scene${scenes.length === 1 ? "" : "s"}, ${shots} shot${shots === 1 ? "" : "s"}`;
  }
  const title = typeof a.title === "string" ? a.title : null;
  if ((s.name === "draft_shot" || s.name === "update_shot") && title) return `${base}: “${title}”`;
  return base;
}

function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i} className={s.ok === false ? "bad" : s.ok ? "ok" : ""}
            title={s.ok === false ? "This step didn't work; the director saw the error." : s.name}>
          <span className="step-mark" aria-hidden>{s.ok === false ? "✗" : "✓"}</span>{stepLabel(s)}
          {s.ok === false && <span className="muted"> · didn't work</span>}
        </li>
      ))}
    </ol>
  );
}

/** A finished reply's steps, folded to one line. */
export function StepsDone({ steps }: { steps: Step[] }) {
  if (!steps.length) return null;
  const bad = steps.filter((s) => s.ok === false).length;
  const summary = steps.length <= 2 ? steps.map(stepLabel).join(" · ")
    : `${steps.length} steps · ${stepLabel(steps[steps.length - 1])}`;
  return (
    <details className="msg steps-done">
      <summary><span className="step-mark" aria-hidden>⚙</span>{summary}{bad ? <span className="warn"> · {bad} didn't work</span> : null}</summary>
      <StepList steps={steps} />
    </details>
  );
}

function useElapsed(since: number | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (since == null) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [since]);
  return since == null ? 0 : Math.max(0, Math.round((now - since) / 1000));
}

const clock = (s: number) => (s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`);

/** What the director is doing right now: its steps so far, then thinking (waiting on the model) or writing. */
export function LiveActivity({ steps, writing, since }: { steps: Step[]; writing: boolean; since: number | null }) {
  const secs = useElapsed(since);
  const last = steps[steps.length - 1];
  const now = writing ? "Writing the reply" : last ? (last.ok === false ? "Working around a failed step" : "Deciding the next step")
    : "Thinking";
  return (
    <div className="activity" role="status">
      {steps.length > 0 && <StepList steps={steps} />}
      <div className="activity-now">
        <span className="pulse" aria-hidden />{now}…<span className="muted"> {since != null ? clock(secs) : ""}</span>
        {!writing && secs >= 45 && <span className="muted small"> · cloud models can take a minute or two</span>}
      </div>
    </div>
  );
}
