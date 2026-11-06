import { useEffect, useRef, useState } from "react";
import { ask } from "./Dialog";
import { api, type Shot, type Take } from "./api";
import { confirmRender, EDIT_SHOT } from "./Shots";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const VERDICT: Record<string, string> = { good: "👍 Good", maybe: "🤔 Maybe", reject: "👎 Reject" };

/** Verdict, notes and "use in the edit" for one take. */
export function TakeReview({ take, shot, used, act, onCompare, newest, shotBusy, queueBusy }: {
  take: Take; shot: Shot | null; used: boolean; act: Act; onCompare?: () => void;
  newest?: boolean; shotBusy?: boolean; queueBusy?: boolean;
}) {
  const [notes, setNotes] = useState(take.notes ?? "");
  useEffect(() => setNotes(take.notes ?? ""), [take.id, take.notes]);
  return (
    <div className="take-review">
      {take.verdict === "reject" && shot && newest && (
        <Reshoot shot={shot} notes={notes} act={act} shotBusy={!!shotBusy} queueBusy={!!queueBusy} />
      )}
      <div className="row">
        {(["good", "maybe", "reject"] as const).map((v) => (
          <button key={v} className={`ghost small ${take.verdict === v ? "on" : ""}`} aria-pressed={take.verdict === v}
            onClick={() => act(() => api.reviewTake(take.id, { verdict: take.verdict === v ? "" : v }))}>{VERDICT[v]}</button>
        ))}
        <span className="grow" />
        {shot && (used
          ? <span className="chip s-ready" title="This is the take the edit uses for this shot">In the edit</span>
          : <button className="ghost small" disabled={take.verdict === "reject"} title="Use this take for the shot in the edit"
              onClick={() => act(() => api.selectTake(shot.id, take.id))}>Use in edit</button>)}
        {onCompare && <button className="ghost small" onClick={onCompare}>Compare A/B…</button>}
        <button className="ghost small danger-text" title="Move this take to Recently deleted (restorable)" onClick={() => act(async () => {
          if (await ask("Move this take to Recently deleted? The shot and its other takes stay; you can restore it from there."))
            await api.deleteTake(take.id);
        })}>Delete</button>
      </div>
      <textarea rows={2} className="notes" placeholder="Notes on this take (what works, what doesn't)" value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={() => notes !== (take.notes ?? "") && act(() => api.reviewTake(take.id, { notes }))} />
    </div>
  );
}

/** After a take is rejected: render the shot again, edit it first, or ask the director for a fix. */
function Reshoot({ shot, notes, act, shotBusy, queueBusy }: {
  shot: Shot; notes: string; act: Act; shotBusy: boolean; queueBusy: boolean;
}) {
  const renderable = ["draft", "pending_approval", "failed", "ready", "approved"].includes(shot.status);
  const editable = ["draft", "failed", "pending_approval", "ready"].includes(shot.status) && !shot.locked;
  const again = () => act(async () => {
    if (shot.seed !== -1 && editable) await api.updateShot(shot.id, { seed: -1 });  // a fresh roll, not the same take
    if (await confirmRender(shot.id, () => undefined)) await api.approveAndRender(shot.id);
  });
  const edit = () => window.dispatchEvent(new CustomEvent(EDIT_SHOT, { detail: { shotId: shot.id } }));
  const askDirector = () => act(() => api.sendChat(shot.project_id,
    `I rejected the latest take of shot ${shot.position} “${shot.title}”${notes.trim() ? `. What's wrong: ${notes.trim()}` : ""}. `
    + "Look at it (inspect_take) and propose a fix with propose_redo: what failed, what you changed and why."));
  return (
    <div className="card reshoot" role="group" aria-label="Reshoot this shot">
      <p className="small"><strong>Rejected.</strong> Reshoot “{shot.title}”?
        {!notes.trim() && <span className="muted"> A note on what's wrong (below) helps the director fix it.</span>}</p>
      <div className="row">
        {shotBusy ? <span className="chip s-rendering">A new take is rendering</span> : renderable && (
          <button className="primary small" title={queueBusy ? "WanGP is busy: this waits in its queue and starts after the current renders"
            : "Render the same shot again with a new random seed"} onClick={again}>{queueBusy ? "Add to queue" : "Render again"}</button>
        )}
        {editable && <button className="ghost small" onClick={edit}>Edit the shot first</button>}
        {!shot.locked && <button className="ghost small" title="The director looks at the take and proposes a revised prompt; you approve it"
          onClick={askDirector}>Ask the director to fix it</button>}
      </div>
      {shot.locked && <p className="small muted">The shot is locked; unlock it to change it.</p>}
    </div>
  );
}

/** Two takes side by side, played and scrubbed together. */
export function CompareTakes({ takes, initial, shot, act, onClose }: {
  takes: Take[]; initial: [string, string]; shot: Shot | null; act: Act; onClose: () => void;
}) {
  const [ids, setIds] = useState<[string, string]>(initial);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const a = useRef<HTMLVideoElement>(null);
  const b = useRef<HTMLVideoElement>(null);
  const left = takes.find((x) => x.id === ids[0]) ?? takes[0];
  const right = takes.find((x) => x.id === ids[1]) ?? takes[1] ?? takes[0];
  const dur = Math.max(left?.duration_s ?? 0, right?.duration_s ?? 0);

  useEffect(() => {  // keep B locked to A while playing
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const va = a.current, vb = b.current;
      if (va && vb) {
        setT(va.currentTime);
        if (Math.abs(vb.currentTime - va.currentTime) > 0.08 && va.currentTime < (vb.duration || 0)) vb.currentTime = va.currentTime;
        if (va.ended && (vb.ended || vb.currentTime >= (vb.duration || 0) - 0.05)) setPlaying(false);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const both = (fn: (v: HTMLVideoElement) => void) => { [a.current, b.current].forEach((v) => v && fn(v)); };
  const play = () => {
    if (playing) { both((v) => v.pause()); setPlaying(false); return; }
    if (t >= dur - 0.05) seek(0);
    Promise.all([a.current?.play(), b.current?.play()]).catch(() => undefined);
    setPlaying(true);
  };
  const seek = (x: number) => { setT(x); both((v) => { v.currentTime = Math.min(x, (v.duration || x)); }); };
  const pick = (side: 0 | 1, id: string) => { setIds((p) => (side === 0 ? [id, p[1]] : [p[0], id])); setPlaying(false); setT(0); };

  if (!left || !right) return null;
  return (
    <div className="card compare" role="dialog" aria-label="Compare takes">
      <div className="row"><h3 className="grow">Compare takes{shot ? ` · ${shot.title}` : ""}</h3>
        <button className="ghost small" onClick={onClose}>Close</button></div>
      <div className="compare-grid">
        {([left, right] as Take[]).map((tk, side) => (
          <figure key={side}>
            <select value={tk.id} aria-label={side ? "Right take" : "Left take"} onChange={(e) => pick(side as 0 | 1, e.target.value)}>
              {takes.map((x, i) => <option key={x.id} value={x.id}>Take {i + 1}{x.verdict ? ` · ${x.verdict}` : ""} · {new Date(x.created_at).toLocaleTimeString()}</option>)}
            </select>
            <video ref={side ? b : a} src={api.mediaUrl(tk.media_id)} preload="auto" muted={side === 1} playsInline
              poster={tk.thumb_media_id ? api.mediaUrl(tk.thumb_media_id) : undefined} />
            <figcaption className="small muted">seed {tk.provenance?.seed ?? "?"} · {tk.provenance?.model_type ?? ""}
              {tk.provenance?.requested?.prompt ? <span className="block">“{tk.provenance.requested.prompt.slice(0, 140)}{tk.provenance.requested.prompt.length > 140 ? "…" : ""}”</span> : null}
            </figcaption>
            {shot && <button className="ghost small" disabled={tk.verdict === "reject"}
              onClick={() => act(async () => { await api.selectTake(shot.id, tk.id); onClose(); })}>Keep this one</button>}
          </figure>
        ))}
      </div>
      <div className="row">
        <button className="primary small" onClick={play}>{playing ? "Pause" : "Play both"}</button>
        <input type="range" className="grow" min={0} max={dur || 0} step={1 / 24} value={t} aria-label="Scrub both"
          onChange={(e) => seek(Number(e.target.value))} />
        <span className="small muted">{t.toFixed(2)} / {dur.toFixed(2)} s · sound from the left take</span>
      </div>
    </div>
  );
}
