import { useEffect, useState } from "react";
import { ImagePicker, type Pick } from "./ImagePicker";
import type { Shot, Template } from "./api";
import {
  type Issue, KIND_LABEL, lib, type ShotRef,
} from "./library-api";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const REFS_EDITABLE = new Set(["draft", "failed", "pending_approval", "approved", "ready"]);

/** The <Picture N> legend for a shot, plus the picker to change it. */
export function ShotReferences({ shot, active, selected, act }: {
  shot: Shot; active: boolean; selected: boolean; act: Act;
}) {
  const [picking, setPicking] = useState(false);
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const refs = shot.refs ?? [];
  const canEdit = REFS_EDITABLE.has(shot.status) && !active;

  useEffect(() => {  // pre-render report for the selected shot (refreshes when its refs/template change)
    if (!selected) return;
    let live = true;
    lib.shotChecks(shot.id).then((c) => live && setIssues(c.issues)).catch(() => live && setIssues(null));
    return () => { live = false; };
  }, [selected, shot.id, shot.template, shot.updated_at, refs.length]);

  return (
    <div className="refs">
      {refs.length > 0 && (
        <ol className="legend">
          {refs.map((r) => (
            <li key={r.position} title={`${KIND_LABEL[r.kind]} · ${r.asset_name} · ${r.variant_name} v${r.version}`}>
              <img src={lib.fileUrl(r.file_id, true)} alt="" />
              <span className="small"><code>{`<Picture ${r.position}>`}</code> {r.asset_name}
                <span className="muted"> · {r.variant_name} v{r.version}{r.locked ? " 🔒" : ""}</span></span>
            </li>
          ))}
        </ol>
      )}
      {selected && issues && issues.length > 0 && (
        <ul className="issues">
          {issues.map((i, n) => <li key={n} className={i.level === "block" ? "error small" : i.level === "info" ? "muted small" : "warn small"}>
            {i.level === "block" ? "Can't render: " : ""}{i.message}</li>)}
        </ul>
      )}
      {!picking && (
        <div className="row refs-row">
          <span className="small muted grow">{refs.length ? `${refs.length} reference image${refs.length === 1 ? "" : "s"} from the library`
            : "No reference images: renders from the prompt alone."}</span>
          {canEdit && <button className="ghost" onClick={() => setPicking(true)}>{refs.length ? "Change references" : "+ Add references from library"}</button>}
        </div>
      )}
      {picking && <RefPicker shot={shot} initial={refs} onClose={() => setPicking(false)} act={act} />}
    </div>
  );
}

export function refsToPicks(refs: ShotRef[]): Pick[] {
  return refs.map((r) => ({ variant_id: r.variant_id, file_id: r.file_id, label: `${r.asset_name} · ${r.variant_name} v${r.version}` }));
}

function RefPicker({ shot, initial, onClose, act }: { shot: Shot; initial: ShotRef[]; onClose: () => void; act: Act }) {
  const [picks, setPicks] = useState<Pick[]>(refsToPicks(initial));
  return (
    <div className="card picker">
      <strong>References for “{shot.title}”</strong>
      <ImagePicker picks={picks} onChange={setPicks} max={8} shotId={shot.id}
        hint="In the prompt, the first image is <Picture 1>, the second <Picture 2>, and so on." />
      <div className="actions">
        <button type="button" className="ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" onClick={() => act(async () => {
          await lib.setShotRefs(shot.id, picks.map(({ variant_id, file_id }) => ({ variant_id, file_id })));
          onClose();
        })}>Save references</button>
      </div>
    </div>
  );
}

/** Per-project override of the two video defaults. */
export function templateOptions(templates: Template[]) {
  const plain = templates.filter((t) => (t.task ?? "gen_video") === "gen_video");
  const refs = templates.filter((t) => t.task === "gen_video_with_refs");
  return { plain, refs };
}
