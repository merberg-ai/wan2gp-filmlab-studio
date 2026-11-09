import { type FormEvent, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, type ChatMessage, type DownloadProgress, isDownloading, type Job, type ProjectDetail, type Shot, type Take,
  type Template,
} from "./api";
import type { Listener } from "./App";
import { EditView } from "./EditView";
import { ProjectSettings } from "./ProjectSettings";
import { CompareTakes, TakeReview } from "./Review";
import { InspectionView } from "./Autonomy";
import { ask, dialogOpen } from "./Dialog";
import { Markdown } from "./Markdown";
import { LiveActivity, type Step, StepsDone } from "./DirectorActivity";
import { ACTIVE_JOB } from "./Shots";
import { Timeline, Storyboard } from "./Storyboard";
import { usedTake } from "./FilmStrip";
import { etaText, useNow } from "./eta";
import { SELECT_SHOT } from "./Palette";
import { DownloadNote } from "./WanGPLink";
import { rememberProject } from "./Creator";
import { PANEL_LABEL, PANELS, type PanelName, PanelRail, useLayout } from "./layout";
import { ReviewMode } from "./Review";

type Subscribe = (l: Listener, r: () => void) => () => void;


/** OpenClaw writes its own one-line warning into a reply when one of its built-in tools rejects a call, e.g.
 *  "⚠️ Tool Search failed: invalid arguments" when the model searches for a tool with malformed arguments. The
 *  director then carries on (it finds the tool another way), so show it as a quiet note, not as a reply. */
function openclawHiccup(text: string): string | null {
  const m = text.trim().match(/^⚠️?\s*([A-Za-z][\w ]{1,40}) failed: ([^\n]{1,120})$/u);
  return m ? `The director's ${m[1].toLowerCase()} step failed (${m[2]}); it carried on.` : null;
}

/** OpenClaw's note when it hands one of FilmLab's tools back for FilmLab to run ("⚠️ Tool Call blocked"): the call
 *  itself goes ahead in FilmLab, so the note is noise. */
const handOff = (text: string) => /^⚠️?\s*Tool Call blocked\.?$/u.test(text.trim());

interface Turn { turnId: string; text: string; tools: Step[] }

export function ProjectView({ projectId, tab, subscribe }: { projectId: string; tab: "storyboard" | "edit"; subscribe: Subscribe }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Turn | null>(null);
  const [downloads, setDownloads] = useState<Record<string, DownloadProgress>>({});
  const [templates, setTemplates] = useState<Template[]>([]);
  const [pane, setPane] = useState<"storyboard" | "director" | "renders">("storyboard");  // phones show one panel at a time
  const [selectedShot, setSelectedShot] = useState<string | null>(null);
  const [selectedTake, setSelectedTake] = useState<string | null>(null);
  const lay = useLayout();
  useEffect(() => rememberProject(projectId), [projectId]);  // the Create page sizes pictures for it
  const [error, setError] = useState<string | null>(null);
  const reloadTimer = useRef<number>();

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([api.project(projectId), api.chat(projectId)]);
      setProject(p); setChat(c); setError(null);
    } catch (e) { setError((e as Error).message); }
  }, [projectId]);
  const scheduleLoad = useCallback(() => {
    window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(load, 120);
  }, [load]);

  useEffect(() => {  // the Ctrl+K palette opens a shot here
    const on = (e: Event) => {
      const { projectId: pid, shotId, takeId } = (e as CustomEvent<{ projectId: string; shotId: string; takeId?: string }>).detail;
      if (pid !== projectId) return;
      setSelectedShot(shotId); setSelectedTake(takeId ?? null); setPane(takeId ? "renders" : "storyboard");
      requestAnimationFrame(() => document.getElementById(`shot-${shotId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
    };
    window.addEventListener(SELECT_SHOT, on);
    return () => window.removeEventListener(SELECT_SHOT, on);
  }, [projectId]);

  useEffect(() => { load(); api.templates().then(setTemplates).catch(() => setTemplates([])); }, [load]);

  useEffect(() => subscribe((e) => {
    if (e.project_id !== projectId) return;
    if (e.type === "chat.delta") {
      const { turn_id, text } = e.payload as { turn_id: string; text: string };
      setLive((t) => t && t.turnId === turn_id ? { ...t, text: t.text + text } : { turnId: turn_id, text, tools: t?.tools ?? [] });
      return;
    }
    if (e.type === "chat.tool") {
      const { turn_id, name, ok, arguments: args } = e.payload as { turn_id: string; name: string; ok: boolean; arguments?: string };
      // Text streamed before a tool call is commentary for that call; start fresh for the next round.
      setLive((t) => ({ turnId: turn_id, text: "", tools: [...(t?.turnId === turn_id ? t.tools : []), { name, ok, arguments: args }] }));
    }
    if (e.type === "chat.message" && (e.payload.role === "assistant" || e.payload.role === "error")) setLive(null);
    if (e.type === "chat.done") setLive(null);
    if (e.type === "render.progress") {  // WanGP downloading a model first: how much has arrived (not kept in the job)
      const p = e.payload as { job_id: string; download?: DownloadProgress | null };
      if (p.download) setDownloads((d) => ({ ...d, [p.job_id]: p.download! }));
    }
    scheduleLoad();
  }, load), [projectId, subscribe, load, scheduleLoad]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
    scheduleLoad();
  };

  const shot = project?.shots.find((s) => s.id === selectedShot) ?? null;
  const takes = useMemo(() => project?.takes.filter((t) => !selectedShot || t.shot_id === selectedShot) ?? [], [project, selectedShot]);
  const take = takes.find((t) => t.id === selectedTake) ?? takes[takes.length - 1] ?? null;
  // A new take arrived (a render finished): show it, rather than the take picked earlier.
  const newest = project?.takes[project.takes.length - 1]?.id;
  const seenNewest = useRef<string | undefined>();
  useEffect(() => {
    if (seenNewest.current !== undefined && newest !== seenNewest.current) setSelectedTake(null);
    seenNewest.current = newest ?? "";
  }, [newest]);

  if (!project) return <main className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</main>;

  return (
    <main className={`workspace show-${pane} ${tab !== "edit" ? "resizable" : ""}`}
      style={tab !== "edit" ? { ["--ws-cols" as string]: lay.template } : undefined}>
      <div className="ws-head">
        <h1>{project.title}</h1>
        <nav className="tabs" aria-label="Project views">
          <a href={`#/p/${project.id}`} className={tab === "storyboard" ? "on" : ""}>Storyboard</a>
          <a href={`#/p/${project.id}/edit`} className={tab === "edit" ? "on" : ""}>Edit &amp; export</a>
          <a href={`#/p/${project.id}/sheet`}>Contact sheet</a>
        </nav>
        {project.logline && <p className="muted logline" title={project.logline}>{project.logline}</p>}
        <ProjectSettings project={project} act={act} />
        {tab !== "edit" && (
          <div className="layout-controls small" role="group" aria-label="Panels">
            <span className="muted">Panels:</span>
            {PANELS.map((p) => (
              <button key={p} className={`ghost small ${lay.hidden(p) ? "" : "on"}`} aria-pressed={!lay.hidden(p)}
                title={lay.hidden(p) ? `Show ${PANEL_LABEL[p]}` : `Hide ${PANEL_LABEL[p]}`} onClick={() => lay.toggle(p)}>{PANEL_LABEL[p]}</button>
            ))}
            <select className="small" value="" aria-label="Focus on one panel" onChange={(e) => e.target.value && lay.focus(e.target.value as PanelName)}>
              <option value="">Focus…</option>
              {PANELS.map((p) => <option key={p} value={p}>{PANEL_LABEL[p]} only</option>)}
            </select>
            <button className="ghost small" title="Equal panels, all shown" onClick={lay.reset}>Reset</button>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
      {tab !== "edit" && (
        <nav className="phone-tabs" aria-label="Show">
          {(["storyboard", "director", "renders"] as const).map((p) => (
            <button key={p} className={pane === p ? "on" : ""} aria-pressed={pane === p} onClick={() => setPane(p)}>
              {p === "storyboard" ? "Storyboard" : p === "director" ? <>Director{(project.director_busy || live) && <i className="dot warn" />}</> : "Renders"}
            </button>
          ))}
        </nav>
      )}
      {tab === "edit" ? <EditView project={project} subscribe={subscribe} /> : <>
        {lay.wide && (
          <section className="panel timeline-card" aria-label="Timeline">
            <Timeline project={project} selected={selectedShot} onSelect={(id) => { setSelectedShot(id); setSelectedTake(null); }} act={act} />
          </section>
        )}
        {lay.hidden("storyboard") ? <PanelRail name="storyboard" onShow={() => lay.toggle("storyboard")} />
          : <Storyboard project={project} templates={templates} selected={selectedShot} timeline={!lay.wide}
            onSelect={(id) => { setSelectedShot(id); setSelectedTake(null); }} act={act} />}
        <div className="col-handle" role="separator" aria-orientation="vertical" aria-label="Resize the storyboard and director panels"
          onPointerDown={(e) => lay.drag(0, e)} onDoubleClick={lay.reset} />
        {lay.hidden("director") ? <PanelRail name="director" onShow={() => lay.toggle("director")} />
          : <ChatPanel projectId={projectId} messages={chat} live={live} busy={project.director_busy || live !== null} act={act} />}
        <div className="col-handle" role="separator" aria-orientation="vertical" aria-label="Resize the director and renders panels"
          onPointerDown={(e) => lay.drag(1, e)} onDoubleClick={lay.reset} />
        {lay.hidden("renders") ? <PanelRail name="renders" onShow={() => lay.toggle("renders")} />
          : <MonitorPanel project={project} shot={shot} takes={takes} take={take} onTake={setSelectedTake} act={act}
            downloads={downloads}
            onShowAll={() => { setSelectedShot(null); setSelectedTake(null); }} />}
      </>}
    </main>
  );
}

// --- director chat ----------------------------------------------------------

function ChatPanel({ projectId, messages, live, busy, act }: {
  projectId: string; messages: ChatMessage[]; live: Turn | null; busy: boolean;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);  // follow new messages only while the owner is at the bottom
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;  // scrolls the chat only, never the page
  }, [messages.length, live?.text, live?.tools.length, busy]);

  // Messages in order, with each reply's tool steps folded into one line before it. Steps after the last reply
  // belong to the turn in progress and show in the live activity instead.
  const { items, trailing } = useMemo(() => {
    const out: ({ key: string; steps: Step[] } | { key: string; m: ChatMessage })[] = [];
    let pending: Step[] = [];
    const flush = (key: string) => { if (pending.length) out.push({ key: `s${key}`, steps: pending }); pending = []; };
    for (const m of messages) {
      if (!m.content.trim() || (m.steps?.length && handOff(m.content))) { pending.push(...(m.steps ?? [])); continue; }
      flush(String(m.id));
      out.push({ key: String(m.id), m });
      pending = [...(m.steps ?? [])];  // a reply with text and tool calls: its commentary comes before its steps
    }
    const trailing = pending;
    if (!busy) flush("end");
    return { items: out, trailing };
  }, [messages, busy]);
  const liveSteps = live && live.tools.length >= trailing.length ? live.tools : trailing;
  const t = live?.text.trim() ?? "";  // also while that note is still streaming in
  const liveText = t && !handOff(t) && !"⚠️ Tool Call blocked".startsWith(t) ? live!.text : "";
  // the turn started with the owner's last message (a reload mid-turn keeps the right clock)
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const started = useRef<number | null>(null);
  if (!busy) started.current = null;
  else if (started.current == null) {
    const t = lastUser ? Date.parse(lastUser.created_at) : NaN;
    started.current = Date.now() - t < 10 * 60_000 ? t : Date.now();  // not an old message (a turn with none)
  }
  const since = started.current;

  const send = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setText("");
    pinned.current = true;
    act(() => api.sendChat(projectId, t));
  };

  return (
    <section className="panel chat">
      <div className="panel-head"><h2>Director</h2>
        <span className="grow">{busy && <span className="chip s-rendering">thinking…</span>}</span>
        {busy && <button className="ghost small danger-text" title="Stop the director now; what it already saved stays"
          onClick={() => act(() => api.stopChat(projectId))}>Stop</button>}
        <button className="ghost small" disabled={busy} title="The director forgets this chat; shots, takes and the edit stay"
          onClick={() => act(async () => {
            if (await ask("Start a fresh conversation? The director forgets this chat and starts from here. Shots, scenes, "
              + "takes and the edit don't change, and the messages stay for you to read.", { confirm: "Start fresh" }))
              await api.freshChat(projectId);
          })}>Start fresh</button>
      </div>
      <div className="messages" aria-live="polite" ref={box}
           onScroll={(e) => { const el = e.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
        {messages.length === 0 && !live && <p className="muted">Describe the film you want. The director drafts shots; nothing renders until you approve it.</p>}
        {items.map((it) => {
          if ("steps" in it) return <StepsDone key={it.key} steps={it.steps} />;
          const m = it.m;
          const hiccup = m.role === "assistant" ? openclawHiccup(m.content) : null;
          return hiccup ? <div key={m.id} className="msg note" title={m.content}>{hiccup}</div>
            : <div key={m.id} className={`msg ${m.role}`}>{m.role === "assistant" ? <Markdown text={m.content} /> : m.content}</div>;
        })}
        {liveText && <div className="msg assistant live"><Markdown text={liveText} /><span className="cursor" /></div>}
        {busy && <LiveActivity steps={liveSteps} writing={!!liveText} since={since} />}
      </div>
      <form className="composer" onSubmit={send}>
        <textarea rows={Math.min(6, Math.max(1, text.split("\n").length))}
          placeholder={busy ? "Director is answering…" : "Message the director…"} title="Enter sends · Shift+Enter for a new line"
          value={text} disabled={busy} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(e); } }} />
        <button className="primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </section>
  );
}

// --- render monitor + player ------------------------------------------------

const RUNNING_OWNER: Record<string, string> = {
  ui: "a WanGP UI task", agent: "another agent's task", filmlab: "another FilmLab render", this: "this render",
};

function queueText(j: Job) {
  if (j.queue_ahead == null) return "Queued in WanGP";
  if (j.queue_ahead === 0) return "Next in WanGP's queue";
  const running = j.queue_running && j.queue_running !== "this" ? ` · running now: ${RUNNING_OWNER[j.queue_running]}` : "";
  return `Waiting in WanGP's queue: ${j.queue_ahead} ahead${running}`;
}

function progressText(j: Job) {
  if (j.status === "cancelling") return "Cancelling…";
  if (j.status === "restored") {
    return j.phase === "waiting for WanGP" ? "WanGP restarted; waiting for it to restore the task"
      : "Restored by WanGP after a restart";
  }
  if (j.status === "submitting") return "Submitting…";
  if (j.status === "queued" && !j.phase) return queueText(j);
  const phase = (j.phase ?? "").replace(/_/g, " ");
  return j.total_steps ? `${phase} ${j.current_step ?? 0}/${j.total_steps}` : phase || j.status;
}

function MonitorPanel({ project, shot, takes, take, onTake, act, onShowAll, downloads }: {
  project: ProjectDetail; shot: Shot | null; takes: Take[]; take: Take | null; downloads: Record<string, DownloadProgress>;
  onTake: (id: string) => void; act: (fn: () => Promise<unknown>) => Promise<void>; onShowAll: () => void;
}) {
  const active = project.jobs.filter((j) => ACTIVE_JOB.has(j.status));
  const now = useNow();
  const finished = project.jobs.filter((j) => !ACTIVE_JOB.has(j.status) && (!shot || j.shot_id === shot.id));
  const recent = (shot ? finished : finished.slice(-5)).reverse();
  const title = (id: string) => project.shots.find((s) => s.id === id)?.title ?? "shot";
  const [comparing, setComparing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const takeShot = take ? project.shots.find((s) => s.id === take.shot_id) ?? null : null;
  useReviewKeys({ takes, take, onTake, act, player, onCompare: () => shotTakes.length > 1 && setComparing(true) });
  const shotTakes = take ? project.takes.filter((t) => t.shot_id === take.shot_id) : [];
  const used = takeShot ? usedTake(takeShot, shotTakes) : undefined;
  // while one shot is selected, say when another shot gets a newer take (it would otherwise stay out of view)
  const last = project.takes[project.takes.length - 1];
  const mineLast = shot ? [...project.takes].reverse().find((t) => t.shot_id === shot.id) : undefined;
  const newestElsewhere = shot && last && last.shot_id !== shot.id && (!mineLast || last.created_at > mineLast.created_at)
    ? project.shots.find((s) => s.id === last.shot_id) ?? null : null;

  return (
    <section className="panel monitor">
      <div className="panel-head"><h2 className="grow">Renders</h2>
        {(active.length > 1 || (project.batch && ["running", "paused"].includes(project.batch.status))) && (
          <button className="danger small" title="Stop the batch and cancel every render of this project, waiting or running"
            onClick={() => act(async () => {
              const n = active.filter((j) => j.status !== "cancelling").length;
              if (await ask(`Cancel all renders? ${n ? `${n} render${n === 1 ? "" : "s"} of this project stop` : "Nothing more starts"}`
                + `${project.batch && ["running", "paused"].includes(project.batch.status) ? ", and the batch starts no more shots" : ""}. `
                + "Takes already made stay.", { confirm: "Cancel all", dismiss: "Keep rendering" })) await api.cancelAllRenders(project.id);
            })}>Cancel all</button>)}
      </div>
      {active.length === 0 && <p className="muted small">Nothing rendering.</p>}
      {active.map((j) => (
        <div key={j.id} className="card job">
          <div className="row"><strong>{title(j.shot_id)}</strong>{j.preview && <span className="chip preview">Preview</span>}
            <span className="chip s-rendering">{j.status}</span></div>
          {isDownloading(j) ? <DownloadNote text={j.status_text!} progress={downloads[j.id]} />
            : <div className="small">{progressText(j)}</div>}
          {j.status === "running" && (() => {
            const eta = etaText(j, project.shots.find((s) => s.id === j.shot_id), project.render_rates, now);
            return eta && <div className="small muted eta">{eta}</div>;
          })()}
          {j.stalled && <p className="warn small">No progress from WanGP for 10 minutes. Check WanGP; FilmLab won't cancel it.</p>}
          {j.total_steps ? <progress max={j.total_steps} value={j.current_step ?? 0} aria-label="phase progress" /> : <progress aria-label="in progress" />}
          {j.status !== "cancelling" && <button className="ghost small" onClick={() => act(() => api.cancelJob(j.id))}>Cancel</button>}
        </div>
      ))}
      {recent.length > 0 && (
        <details className="small" open={!!shot}>
          <summary>{shot ? `History · ${shot.title} (${recent.length})` : "Recent jobs"}</summary>
          <ul className="history">
            {recent.map((j) => <HistoryItem key={j.id} job={j} title={shot ? null : title(j.shot_id)} />)}
          </ul>
        </details>
      )}

      {reviewing && <ReviewMode project={project} takes={takes} act={act} onClose={() => setReviewing(false)} />}
      <div className="panel-head"><h2 className="grow">{shot ? `Takes · ${shot.title}` : "Latest takes"}</h2>
        {takes.length > 0 && <button className="ghost small" title="Full-screen review: swipe right for good, left for reject, up for maybe"
          onClick={() => setReviewing(true)}>Review mode</button>}
        {shot && <button className="ghost small" title="Show every shot's takes and renders, not just this shot's" onClick={onShowAll}>Show all</button>}
      </div>
      {shot && newestElsewhere && (
        <p className="small new-elsewhere">New take for {newestElsewhere.position}. {newestElsewhere.title} ·{" "}
          <button className="link" onClick={onShowAll}>show all takes</button></p>
      )}
      {take ? (
        <>
          <video key={take.id} ref={player} className="player" controls preload="metadata" src={api.mediaUrl(take.media_id)}
            poster={take.thumb_media_id ? api.mediaUrl(take.thumb_media_id) : undefined} />
          <p className="muted small">
            {take.width}×{take.height} · {take.fps} fps · {take.frames} f · {take.duration_s?.toFixed(2)} s
            {take.has_audio ? " · audio" : ""} · {(take.bytes / 1e6).toFixed(1)} MB{take.preview ? " · preview" : ""}
          </p>
          <TakeReview take={take} shot={takeShot} used={used?.id === take.id} act={act}
            playhead={() => player.current?.currentTime ?? 0}
            cover={{ isCover: project.cover_take_id === take.id, set: (id) => api.setCover(project.id, id) }}
            newest={shotTakes[shotTakes.length - 1]?.id === take.id}
            shotBusy={project.jobs.some((j) => j.shot_id === take.shot_id && ACTIVE_JOB.has(j.status))}
            queueBusy={active.length > 0}
            onCompare={shotTakes.length > 1 ? () => setComparing(true) : undefined} />
          <InspectionView take={take} inspection={project.inspections?.[take.id]} act={act} />
          {comparing && shotTakes.length > 1 && (
            <CompareTakes takes={shotTakes} shot={takeShot} act={act} onClose={() => setComparing(false)}
              initial={[take.id, (shotTakes.find((t) => t.id !== take.id) ?? take).id]} />
          )}
          <TakeDetails take={take} />
        </>
      ) : <p className="muted small">No takes yet.</p>}
      {take && <p className="small muted keys-hint">Keys: <kbd>J</kbd>/<kbd>K</kbd> previous/next take · <kbd>Space</kbd> play ·{" "}
        <kbd>A</kbd> good · <kbd>M</kbd> maybe · <kbd>R</kbd> reject · <kbd>C</kbd> compare</p>}
      <div className="thumbs">
        {takes.map((t) => (
          <button key={t.id} className={`thumb ${t.id === take?.id ? "selected" : ""} v-${t.verdict ?? "none"}`} onClick={() => onTake(t.id)}
            title={`${new Date(t.created_at).toLocaleString()}${t.verdict ? ` · ${t.verdict}` : ""}${t.notes ? ` · ${t.notes}` : ""}`}>
            {t.thumb_media_id ? <ScrubThumb take={t} alt={`take of ${title(t.shot_id)}`} /> : "take"}
            {t.verdict && <span className="verdict-dot" aria-label={t.verdict} />}
            {t.preview && <span className="preview-mark" title="Preview: quick and low resolution">P</span>}
            {project.inspections?.[t.id]?.verdict === "flagged" && <span className="insp-flag" title={project.inspections[t.id].summary ?? ""}>⚠</span>}
          </button>
        ))}
      </div>
    </section>
  );
}

/** A take's thumbnail that scrubs through its filmstrip (8 frames) as the pointer moves over it. */
function ScrubThumb({ take, alt }: { take: Take; alt: string }) {
  const [frame, setFrame] = useState<number | null>(null);
  const strip = api.filmstripUrl(take.media_id);
  return (
    <span className="scrub" onPointerMove={(e) => {
      if (e.pointerType === "touch") return;
      const r = e.currentTarget.getBoundingClientRect();
      setFrame(Math.max(0, Math.min(7, Math.floor(((e.clientX - r.left) / r.width) * 8))));
    }} onPointerLeave={() => setFrame(null)}>
      <img src={api.mediaUrl(take.thumb_media_id!)} alt={alt} loading="lazy" style={frame == null ? undefined : { visibility: "hidden" }} />
      {frame != null && <span className="scrub-frame" aria-hidden
        style={{ backgroundImage: `url(${strip})`, backgroundPosition: `${(frame * 100) / 7}% 0` }} />}
    </span>
  );
}

/** Review from the keyboard while the Renders panel shows a take (not while typing). */
function useReviewKeys({ takes, take, onTake, act, player, onCompare }: {
  takes: Take[]; take: Take | null; onTake: (id: string) => void; act: (fn: () => Promise<unknown>) => Promise<void>;
  player: RefObject<HTMLVideoElement>; onCompare: () => void;
}) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!take || e.ctrlKey || e.metaKey || e.altKey || dialogOpen()) return;
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable=true]")) return;
      const panel = document.querySelector<HTMLElement>(".panel.monitor");  // hidden on a phone's other tabs
      if (!panel || panel.offsetParent === null) return;
      const i = takes.findIndex((t) => t.id === take.id);
      const verdict = (v: string) => act(() => api.reviewTake(take.id, { verdict: take.verdict === v ? "" : v }));
      const k = e.key.toLowerCase();
      if (k === "j" && i > 0) onTake(takes[i - 1].id);
      else if (k === "k" && i < takes.length - 1) onTake(takes[i + 1].id);
      else if (k === " " && player.current && el.tagName !== "BUTTON" && el.tagName !== "VIDEO") {
        if (player.current.paused) player.current.play().catch(() => undefined); else player.current.pause();
      } else if (k === "a" || k === "g") verdict("good");
      else if (k === "m") verdict("maybe");
      else if (k === "r") verdict("reject");
      else if (k === "c") onCompare();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [takes, take, onTake, act, player, onCompare]);
}

const OUTCOME: Record<string, string> = {
  done: "take ready", failed: "failed", cancelled: "cancelled", interrupted: "lost in a WanGP restart",
};

function HistoryItem({ job, title }: { job: Job; title: string | null }) {
  const when = new Date(job.submitted_at).toLocaleString();
  const secs = job.finished_at ? Math.round((Date.parse(job.finished_at) - Date.parse(job.submitted_at)) / 1000) : null;
  return (
    <li className={`hist hist-${job.status}`}>
      <span>{title && <strong>{title} · </strong>}{job.preview ? "preview " : ""}{OUTCOME[job.status] ?? job.status}{job.recovered ? " (recovered)" : ""}</span>
      <span className="muted"> · {when}{secs != null ? ` · ${secs} s` : ""}</span>
      {job.error_info && job.status !== "cancelled" && (
        <span className="block">{job.error_info.message} {job.error_info.hint}</span>
      )}
      {job.error && job.status !== "cancelled" && (
        <details><summary className="muted">WanGP details</summary><code className="raw">{job.error}</code></details>
      )}
    </li>
  );
}

function TakeDetails({ take }: { take: Take }) {
  const p = take.provenance;
  if (!p) return null;
  const rows: [string, string | number | undefined][] = [
    ["Seed", p.seed], ["Model", p.model_type], ["Steps", p.steps],
    ["Generation", p.generation_s != null ? `${p.generation_s} s in WanGP` : undefined],
    ["WanGP", p.wangp_version], ["Model file", p.model_file?.split("/").pop()],
    ["MCP server", p.mcp_server?.version ? `${p.mcp_server.name} ${p.mcp_server.version}` : undefined],
    ["Request", p.request_id], ["WanGP job", p.wangp_job_id], ["Gallery item", p.wangp_media_id],
    ["SHA-256", p.sha256 ? `${p.sha256.slice(0, 16)}…` : undefined],
  ];
  return (
    <details className="small take-details">
      <summary>Take details</summary>
      {p.settings_error && <p className="warn">WanGP's settings record wasn't available: {p.settings_error}</p>}
      {p.requested?.prompt && <p className="small"><span className="muted">Prompt used: </span>{p.requested.prompt}</p>}
      {p.references && p.references.length > 0 && (
        <p className="small"><span className="muted">References used: </span>
          {p.references.map((r) => `<Picture ${r.position}> ${r.asset_name} · ${r.variant_name} v${r.version}`).join(", ")}</p>
      )}
      <dl>
        {rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd title={k === "SHA-256" ? p.sha256 : undefined}>{v}</dd></div>
        ))}
      </dl>
    </details>
  );
}
