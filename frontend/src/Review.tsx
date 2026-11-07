import { useEffect, useRef, useState } from "react";
import { ask } from "./Dialog";
import { api, type ProjectDetail, type Shot, type Take } from "./api";
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
        <Reshoot shot={shot} notes={notes} act={act} shotBusy={!!shotBusy} queueBusy={!!queueBusy} preview={!!take.preview} />
      )}
      {take.preview && take.verdict !== "reject" && shot && newest && (
        <FullQuality shot={shot} take={take} act={act} shotBusy={!!shotBusy} queueBusy={!!queueBusy} />
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

const RENDERABLE = ["draft", "pending_approval", "failed", "ready", "approved"];

/** A preview take that looks right: render the shot at full quality (same prompt, references, sound and seed). */
function FullQuality({ shot, take, act, shotBusy, queueBusy }: {
  shot: Shot; take: Take; act: Act; shotBusy: boolean; queueBusy: boolean;
}) {
  const seed = take.provenance?.seed;
  const full = () => act(async () => {
    // the full render repeats the preview's seed, so it keeps its composition as closely as the model allows
    if (seed != null && seed !== -1 && shot.seed === -1 && !shot.locked && ["draft", "failed", "pending_approval", "ready"].includes(shot.status))
      await api.updateShot(shot.id, { seed });
    if (await confirmRender(shot.id, () => undefined)) await api.approveAndRender(shot.id);
  });
  return (
    <div className="card preview-next" role="group" aria-label="Preview take">
      <p className="small"><span className="chip preview">Preview</span> {take.width}×{take.height}, quick and low resolution.
        {" "}Timing and framing right? Render it at full quality ({shot.resolution}).</p>
      <div className="row">
        {shotBusy ? <span className="chip s-rendering">A new take is rendering</span> : RENDERABLE.includes(shot.status) && (
          <button className="primary small" title={`Same prompt, references and sound${seed != null && seed !== -1 ? `, and this preview's seed (${seed})` : ""}`}
            onClick={full}>{queueBusy ? "Queue full quality" : "Render full quality"}</button>
        )}
        <span className="small muted">Or mark it 👎 to reshoot it.</span>
      </div>
    </div>
  );
}

/** After a take is rejected: render the shot again, edit it first, or ask the director for a fix. */
function Reshoot({ shot, notes, act, shotBusy, queueBusy, preview }: {
  shot: Shot; notes: string; act: Act; shotBusy: boolean; queueBusy: boolean; preview: boolean;
}) {
  const renderable = RENDERABLE.includes(shot.status);
  const editable = ["draft", "failed", "pending_approval", "ready"].includes(shot.status) && !shot.locked;
  const again = () => act(async () => {
    if (shot.seed !== -1 && editable) await api.updateShot(shot.id, { seed: -1 });  // a fresh roll, not the same take
    if (await confirmRender(shot.id, () => undefined)) await api.approveAndRender(shot.id, preview);
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
            : `Render the same shot again with a new random seed${preview ? ", as a preview" : ""}`} onClick={again}>
            {queueBusy ? "Add to queue" : preview ? "Preview again" : "Render again"}</button>
        )}
        {editable && <button className="ghost small" onClick={edit}>Edit the shot first</button>}
        {!shot.locked && <button className="ghost small" title="The director looks at the take and proposes a revised prompt; you approve it"
          onClick={askDirector}>Ask the director to fix it</button>}
      </div>
      {shot.locked && <p className="small muted">The shot is locked; unlock it to change it.</p>}
    </div>
  );
}

/** Full-screen review, made for phones: one take at a time, looping. Swipe right 👍 good, left 👎 reject, up 🤔 maybe
 *  (or the buttons); each verdict moves to the next take. Starts at the first take you haven't reviewed. */
export function ReviewMode({ project, takes, act, onClose }: {
  project: ProjectDetail; takes: Take[]; act: Act; onClose: () => void;
}) {
  const order = [...takes].reverse();  // newest first
  const [i, setI] = useState(() => Math.max(0, order.findIndex((t) => !t.verdict)));
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const take = order[Math.min(i, order.length - 1)];
  const shot = take ? project.shots.find((s) => s.id === take.shot_id) : undefined;
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", on);
    document.body.classList.add("modal-open");
    return () => { window.removeEventListener("keydown", on); document.body.classList.remove("modal-open"); };
  }, [onClose]);
  if (!take) return null;
  const judge = (v: "good" | "maybe" | "reject") => act(async () => {
    await api.reviewTake(take.id, { verdict: v });
    if (i < order.length - 1) setI(i + 1); else onClose();
  });
  const end = () => {
    const d = drag;
    start.current = null; setDrag(null);
    if (!d) return;
    if (d.x > 80 && Math.abs(d.x) > Math.abs(d.y)) judge("good");
    else if (d.x < -80 && Math.abs(d.x) > Math.abs(d.y)) judge("reject");
    else if (d.y < -80) judge("maybe");
  };
  const hint = drag ? (drag.x > 40 ? "good" : drag.x < -40 ? "reject" : drag.y < -40 ? "maybe" : null) : null;
  return (
    <div className="review-mode" role="dialog" aria-modal="true" aria-label="Review takes">
      <div className="rm-top">
        <button className="ghost small" onClick={onClose}>Close</button>
        <span className="small">{i + 1} of {order.length}{shot ? ` · ${shot.position}. ${shot.title}` : ""}{take.preview ? " · preview" : ""}
          {take.verdict ? ` · ${take.verdict}` : ""}</span>
        <span className="row">
          <button className="ghost small" disabled={i === 0} aria-label="Previous take" onClick={() => setI(i - 1)}>‹</button>
          <button className="ghost small" disabled={i >= order.length - 1} aria-label="Next take" onClick={() => setI(i + 1)}>›</button>
        </span>
      </div>
      <div className={`rm-stage ${hint ? `rm-${hint}` : ""}`}
        onPointerDown={(e) => { start.current = { x: e.clientX, y: e.clientY }; }}
        onPointerMove={(e) => { if (start.current) setDrag({ x: e.clientX - start.current.x, y: e.clientY - start.current.y }); }}
        onPointerUp={end} onPointerCancel={() => { start.current = null; setDrag(null); }}
        style={drag ? { transform: `translate(${drag.x}px, ${Math.min(0, drag.y)}px) rotate(${drag.x / 30}deg)` } : undefined}>
        <video key={take.id} src={api.mediaUrl(take.media_id)} autoPlay loop playsInline controls={false}
          poster={take.thumb_media_id ? api.mediaUrl(take.thumb_media_id) : undefined}
          onClick={(e) => { const v = e.currentTarget; if (v.paused) v.play().catch(() => undefined); else v.pause(); }} />
        {hint && <span className="rm-badge">{VERDICT[hint]}</span>}
      </div>
      {shot?.idea && <p className="small muted rm-idea">{shot.idea}</p>}
      <div className="rm-actions">
        <button className="ghost" onClick={() => judge("reject")}>{VERDICT.reject}</button>
        <button className="ghost" onClick={() => judge("maybe")}>{VERDICT.maybe}</button>
        <button className="primary" onClick={() => judge("good")}>{VERDICT.good}</button>
      </div>
      <p className="small muted rm-help">Swipe right for good, left for reject, up for maybe. Tap the video to pause. Notes and reshoots are in the Renders panel.</p>
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
