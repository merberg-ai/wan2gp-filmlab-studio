/** ✨ Enhance all: the owner picks the storyboard's shots (all by default), and the director enhances them one after
 *  another (backend/app/enhance_all.py); the storyboard shows how far it got. */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type ProjectDetail, type Shot } from "./api";
import { duration, useNow } from "./eta";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const EDITABLE = new Set(["draft", "failed", "pending_approval", "ready"]);

/** Why a shot can't be enhanced now (the backend's rule), or null. */
export function cantEnhance(s: Shot): string | null {
  if (s.locked) return "locked";
  if (!EDITABLE.has(s.status)) return s.status.replace(/_/g, " ");
  return null;
}

export function EnhanceAllModal({ project, onClose, act }: { project: ProjectDetail; onClose: () => void; act: Act }) {
  const shots = [...project.shots].sort((a, b) => a.position - b.position);
  const able = shots.filter((s) => !cantEnhance(s));
  const [picked, setPicked] = useState<Set<string>>(() => new Set(able.map((s) => s.id)));
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  useModalFrame(backdrop);
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); onClose(); } else trapTab(e, card.current);
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);

  const all = picked.size === able.length;
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const start = () => {
    const ids = able.filter((s) => picked.has(s.id)).map((s) => s.id);
    onClose();
    act(() => api.enhanceAll(project.id, ids));
  };

  return createPortal(  // on the page itself: the storyboard panel's glass would otherwise hold it inside the panel
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={card} className="modal glass settings-modal enhance-all" role="dialog" aria-modal="true" aria-labelledby="ea-title">
        <header className="sm-head">
          <h3 id="ea-title">✨ Enhance all</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={onClose}>✕</button>
        </header>
        <div className="sm-body">
          <p className="muted small">The director rewrites each chosen shot's prompt from your idea for it, one shot after
            another, and picks references for shots that have none. Every change shows in the shot's History.</p>
          <div className="row ea-all">
            <label className="ea-check"><input type="checkbox" checked={all} disabled={!able.length}
              ref={(el) => { if (el) el.indeterminate = picked.size > 0 && !all; }}
              onChange={() => setPicked(all ? new Set() : new Set(able.map((s) => s.id)))} /> <strong>All shots</strong></label>
            <span className="muted small grow">{picked.size} of {able.length} chosen</span>
          </div>
          <ul className="ea-list">
            {shots.map((s) => {
              const why = cantEnhance(s);
              return (
                <li key={s.id} className={why ? "muted" : ""}>
                  <label className="ea-check">
                    <input type="checkbox" checked={!why && picked.has(s.id)} disabled={!!why} onChange={() => toggle(s.id)} />
                    <span className="ea-num">{s.position}.</span>
                    <span className="ea-title">{s.title || "Untitled"}</span>
                    {why && <span className="small ea-why">{why}</span>}
                  </label>
                </li>);
            })}
          </ul>
        </div>
        <footer className="sm-foot">
          <div className="row modal-actions">
            <span className="grow" />
            <button type="button" className="ghost" onClick={onClose}>Cancel</button>
            <button type="button" className="primary" disabled={!picked.size} onClick={start}>
              ✨ Enhance{picked.size ? ` ${picked.size} shot${picked.size === 1 ? "" : "s"}` : ""}</button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

/** While the director enhances the chosen shots: which one, how many are done, Stop. */
export function EnhanceProgress({ project, act }: { project: ProjectDetail; act: Act }) {
  const e = project.enhancing!;
  const now = useNow(1000);
  const ran = Math.max(0, (now - Date.parse(e.started_at)) / 1000);
  const left = e.finished > 0 && e.finished < e.total ? (ran / e.finished) * (e.total - e.finished) : null;
  return (
    <div className="card plan-progress" role="status">
      <div className="row">
        <strong className="grow">Enhancing prompts with the director
          <span className="muted small"> · {e.current ? `shot ${e.current.position} “${e.current.title}”` : "starting"}
            {` · ${duration(ran)}`}{left ? ` · about ${duration(left)} left` : ""}</span></strong>
        <button className="ghost small danger-text" title="Stop now: the shots already enhanced keep their new prompts"
          onClick={() => act(() => api.stopEnhanceAll(project.id))}>Stop</button>
      </div>
      {e.finished ? <progress max={e.total} value={e.finished} aria-label="shots enhanced" /> : <progress aria-label="enhancing" />}
      <p className="small muted">{e.finished} of {e.total} shot{e.total === 1 ? "" : "s"} done
        {e.failed ? <span className="warn"> · {e.failed} not changed</span> : null}
        {e.skipped ? ` · ${e.skipped} skipped` : ""}; the director's work shows in the chat.</p>
    </div>
  );
}
