/** ✨ Plan transitions: the director picks a transition and length for each cut (or each scene change) from the mood
 *  of the shots on either side. The modal shows it thinking (its chat turn, streamed), then a compact list to adjust;
 *  Accept applies them (shot transitions and clip lengths in the edit), Cancel discards the proposal.
 *  Backend: backend/app/transitions.py. */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type TransitionCut, type TransitionPlan, type TransitionProposal, transitionLabel } from "./api";
import type { Listener } from "./App";
import { TransitionSelect } from "./TransitionSelect";

type Subscribe = (l: Listener, r: () => void) => () => void;
type Row = TransitionProposal;
type Step = { kind: "text" | "tool" | "note" | "error"; text: string };

const TOOL_LABEL: Record<string, string> = {
  get_edit: "Read the edit", get_project: "Read the storyboard", review_takes: "Looked at the takes",
  propose_transitions: "Proposed the transitions",
};

export function TransitionPlannerModal({ projectId, subscribe, onClose, onApplied }: {
  projectId: string; subscribe: Subscribe; onClose: () => void; onApplied: () => void;
}) {
  const [plan, setPlan] = useState<TransitionPlan | null | undefined>(undefined);
  const [cuts, setCuts] = useState<TransitionCut[] | null>(null);
  const [scope, setScope] = useState<"scenes" | "all">("scenes");
  const [note, setNote] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const turn = useRef<string | null>(null);
  const log = useRef<HTMLDivElement>(null);

  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  useModalFrame(backdrop);
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); if (!busy) onClose(); } else trapTab(e, card.current);
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);

  const loadPlan = () => api.transitionPlan(projectId).then(({ plan: p }) => {
    setPlan(p);
    if (p) turn.current = p.turn_id;
    if (p?.status === "ready") setRows((r) => (r.length && r[0].shot_id === p.items[0]?.shot_id ? r : p.items.map((i) => ({ ...i }))));
  }).catch((e) => setError((e as Error).message));
  useEffect(() => {
    loadPlan();
    api.transitionCuts(projectId).then(({ cuts: c }) => {
      setCuts(c);
      if (!c.some((x) => x.scene_change)) setScope("all");
    }).catch((e) => setError((e as Error).message));
  }, [projectId]);  // eslint-disable-line react-hooks/exhaustive-deps

  // The director's turn, as it happens: its words stream in, each tool call is a line.
  useEffect(() => subscribe((e) => {
    if (e.project_id !== projectId) return;
    const p = e.payload as { turn_id?: string; text?: string; name?: string; ok?: boolean; role?: string; content?: string };
    if (e.type === "transitions.plan") { loadPlan(); return; }
    if (!turn.current || p.turn_id !== turn.current) return;
    if (e.type === "chat.delta" && p.text) {
      setSteps((s) => s.length && s[s.length - 1].kind === "text"
        ? [...s.slice(0, -1), { kind: "text", text: s[s.length - 1].text + p.text }] : [...s, { kind: "text", text: p.text! }]);
    } else if (e.type === "chat.tool" && p.name) {
      setSteps((s) => [...s, { kind: "tool", text: `${p.ok ? "✓" : "✗"} ${TOOL_LABEL[p.name!] ?? p.name}` }]);
    } else if (e.type === "chat.message" && (p.role === "note" || p.role === "error") && p.content) {
      setSteps((s) => [...s, { kind: p.role === "error" ? "error" : "note", text: p.content! }]);
    }
  }, () => { loadPlan(); }), [subscribe, projectId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [steps]);

  const start = async () => {
    setBusy(true); setError(null); setSteps([]); setRows([]);
    try {
      const { plan: p } = await api.planTransitions(projectId, scope, note);
      turn.current = p.turn_id;
      setPlan(p);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  const cancel = async () => {
    setBusy(true);
    try {
      if (plan?.status === "thinking") await api.stopChat(projectId).catch(() => undefined);
      if (plan) await api.discardTransitions(projectId);
      onClose();
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };
  const accept = async () => {
    setBusy(true); setError(null);
    try {
      await api.applyTransitions(projectId, rows.map((r) => ({ shot_id: r.shot_id, transition: r.transition, seconds: r.seconds })));
      onApplied();
      onClose();
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };
  const setRow = (i: number, c: Partial<Row>) => setRows((rs) => rs.map((r, n) => (n === i ? { ...r, ...c } : r)));

  const phase = plan === undefined ? "loading" : !plan ? "setup" : plan.status === "thinking" ? "thinking"
    : plan.status === "ready" ? "review" : "failed";
  const sceneCuts = cuts?.filter((c) => c.scene_change).length ?? 0;
  const changes = rows.filter((r) => r.transition !== r.current.transition
    || (r.transition !== "cut" && Math.abs(r.seconds - r.current.seconds) > 0.001)).length;

  return createPortal(
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div ref={card} className="modal glass settings-modal tplan" role="dialog" aria-modal="true" aria-labelledby="tp-title">
        <header className="sm-head">
          <h3 id="tp-title">✨ Plan transitions</h3>
          <button type="button" className="ghost small" aria-label="Close" title="Close (a plan in progress is kept)"
            onClick={onClose} disabled={busy}>✕</button>
        </header>
        <div className="sm-body">
          {phase === "loading" && <p className="muted small">Loading…</p>}

          {phase === "setup" && (cuts === null ? <p className="muted small">Reading the edit…</p> : cuts.length === 0
            ? <p className="muted">The edit needs at least two clips with takes before there's a cut to plan.</p> : <>
              <p className="small muted">The director reads the shots on either side of each cut and picks a transition and
                its length to suit the moment: calm passages ease out with a dissolve or a fade, action gets a hard cut or
                something quick and dramatic. Nothing changes until you accept.</p>
              <div className="st-replace" role="radiogroup" aria-label="Which cuts to plan">
                <label className={`np-option ${scope === "scenes" ? "on" : ""} ${sceneCuts ? "" : "off"}`}>
                  <input type="radio" checked={scope === "scenes"} disabled={!sceneCuts} onChange={() => setScope("scenes")} />
                  <span><strong>Scene changes only</strong> <span className="muted small">({sceneCuts})</span>
                    <span className="block muted small">{sceneCuts ? "Cuts inside a scene keep what they have." : "Every clip is in one scene."}</span></span></label>
                <label className={`np-option ${scope === "all" ? "on" : ""}`}>
                  <input type="radio" checked={scope === "all"} onChange={() => setScope("all")} />
                  <span><strong>Every cut</strong> <span className="muted small">({cuts.length})</span>
                    <span className="block muted small">Most cuts inside a scene will usually stay cuts.</span></span></label>
              </div>
              <label className="small">Note for the director <span className="muted">(optional)</span>
                <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. fade through black between chapters, keep the fight scene snappy" /></label>
            </>)}

          {(phase === "thinking" || (phase !== "setup" && steps.length > 0)) && (
            <div className={`tp-think ${phase === "thinking" ? "live" : ""}`} ref={log} aria-live="polite">
              {phase === "thinking" && <p className="tp-status small"><span className="spinner" aria-hidden /> The director is
                choosing transitions…</p>}
              {steps.length === 0 && phase === "thinking" && <p className="muted small">Waiting for its first words…</p>}
              {steps.map((s, i) => <p key={i} className={`tp-step ${s.kind}`}>{s.text}</p>)}
            </div>
          )}

          {phase === "failed" && <p className="error small" role="alert">{plan?.error || "The plan didn't finish."}</p>}

          {phase === "review" && <>
            {plan?.summary && <p className="tp-summary small">“{plan.summary}”</p>}
            <ol className="tp-list">
              {rows.map((r, i) => (
                <li key={r.shot_id} className={`tp-row ${r.transition !== r.current.transition ? "changed" : ""}`}>
                  <div className="tp-cut">
                    <span className="tp-num">{r.from.position}→{r.to.position}</span>
                    <span className="tp-names" title={`${r.from.title} → ${r.to.title}`}>{r.from.title} <span className="muted">→</span> {r.to.title}</span>
                    {r.scene_change && <span className="tag tp-scene" title={`${r.from.scene ?? "no scene"} → ${r.to.scene ?? "no scene"}`}>scene</span>}
                  </div>
                  <div className="tp-ctl">
                    <TransitionSelect value={r.transition} onChange={(t) => setRow(i, {
                      transition: t, seconds: t === "cut" ? 0 : r.seconds || Math.min(0.5, r.max_s) })} />
                    {r.transition !== "cut" && <label className="tp-secs" title={`Up to ${r.max_s} s (half the shorter clip)`}>
                      <input type="number" min={0.1} max={r.max_s} step={0.05} value={r.seconds} aria-label="Length in seconds"
                        onChange={(e) => setRow(i, { seconds: Math.min(r.max_s, Math.max(0.1, Number(e.target.value) || 0.1)) })} />s</label>}
                  </div>
                  {(r.reason || r.transition !== r.current.transition) && <p className="tp-why small muted">
                    {r.reason}{r.transition !== r.current.transition &&
                      <span className="tp-was"> · was {transitionLabel(r.current.transition).toLowerCase()}</span>}</p>}
                </li>
              ))}
            </ol>
          </>}
        </div>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="row modal-actions">
            {(phase === "review" || phase === "failed") &&
              <button type="button" className="ghost" disabled={busy} onClick={async () => {
                setBusy(true); await api.discardTransitions(projectId).catch(() => undefined); setPlan(null); setSteps([]); setRows([]); setBusy(false);
              }}>Plan again</button>}
            <span className="grow" />
            <button type="button" className="ghost" onClick={phase === "setup" ? onClose : cancel} disabled={busy && phase !== "thinking"}>
              {phase === "thinking" ? "Stop" : "Cancel"}</button>
            {phase === "setup" && <button type="button" className="primary" disabled={busy || !cuts?.length} onClick={start}>
              {busy ? "Starting…" : `Plan ${scope === "scenes" ? sceneCuts : cuts?.length ?? 0} transition${(scope === "scenes" ? sceneCuts : cuts?.length) === 1 ? "" : "s"}`}</button>}
            {phase === "review" && <button type="button" className="primary" disabled={busy || rows.length === 0} onClick={accept}>
              {busy ? "Applying…" : changes ? `Accept (${changes} change${changes === 1 ? "" : "s"})` : "Accept"}</button>}
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
