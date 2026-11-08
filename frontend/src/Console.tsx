/** The Console: what FilmLab does in the background, live, one line each (backend/app/console.py). A drawer on every
 *  page (the >_ button or the ` key) and a full page (#/console). */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ConsoleLine } from "./api";
import type { Listener } from "./App";
import type { FilmLabEvent } from "./events";

type Subscribe = (l: Listener, resync: () => void) => () => void;
const SOURCES = ["Director", "Plan", "Render", "Create", "WanGP", "System"] as const;
const LEVELS = [["", "All"], ["warn", "Warnings"], ["error", "Errors"]] as const;
const MAX_LINES = 1000;

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const passes = (l: ConsoleLine, level: string, source: string, projectId: string | null, q: string) =>
  (!level || (level === "warn" ? l.level !== "info" : l.level === "error")) && (!source || l.source === source)
  && (!projectId || l.project_id === projectId) && (!q || l.message.toLowerCase().includes(q.toLowerCase()));

/** The lines with their filters; projectId: the project open now (offered as a filter). */
export function ConsoleView({ subscribe, projectId, full }: { subscribe: Subscribe; projectId: string | null; full?: boolean }) {
  const [lines, setLines] = useState<ConsoleLine[]>([]);
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [onlyProject, setOnlyProject] = useState(false);
  const [q, setQ] = useState("");
  const [paused, setPaused] = useState(false);
  const [held, setHeld] = useState<ConsoleLine[]>([]);
  const [more, setMore] = useState(true);
  const [open, setOpen] = useState<Record<number, string | null>>({});
  const box = useRef<HTMLDivElement>(null);
  const pid = onlyProject ? projectId : null;
  const filters = { level, source, project_id: pid ?? undefined, q: q.trim() || undefined };

  const load = useCallback(() => {
    api.consoleLines({ ...filters, limit: 200 }).then((r) => { setLines(r.reverse()); setMore(r.length === 200); })
      .catch(() => undefined);
  }, [level, source, pid, q]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);

  const live = useRef({ level, source, pid, q, paused });
  live.current = { level, source, pid, q, paused };
  useEffect(() => subscribe((e: FilmLabEvent) => {
    if (e.type !== "console.entry") return;
    const l = e.payload as unknown as ConsoleLine;
    const f = live.current;
    if (!passes(l, f.level, f.source, f.pid, f.q.trim())) return;
    if (f.paused) setHeld((h) => [...h, l]);
    else setLines((x) => [...x, l].slice(-MAX_LINES));
  }, load), [subscribe, load]);

  // stay at the bottom while new lines arrive, unless the reader scrolled up
  const stick = useRef(true);
  useEffect(() => { const b = box.current; if (b && stick.current) b.scrollTop = b.scrollHeight; }, [lines]);
  const onScroll = () => { const b = box.current; if (b) stick.current = b.scrollHeight - b.scrollTop - b.clientHeight < 40; };

  const older = async () => {
    if (!lines.length) return;
    const r = await api.consoleLines({ ...filters, limit: 200, before_id: lines[0].id });
    setLines((x) => [...r.reverse(), ...x]); setMore(r.length === 200);
  };
  const resume = () => { setLines((x) => [...x, ...held].slice(-MAX_LINES)); setHeld([]); setPaused(false); stick.current = true; };
  const toggle = async (l: ConsoleLine) => {
    if (l.id in open) { const o = { ...open }; delete o[l.id]; setOpen(o); return; }
    setOpen({ ...open, [l.id]: null });
    const d = await api.consoleEntry(l.id).catch(() => null);
    setOpen((o) => (l.id in o ? { ...o, [l.id]: d?.detail ?? "(no details)" } : o));
  };
  const copy = (text: string) => { navigator.clipboard?.writeText(text).catch(() => undefined); };

  return (
    <div className={`console ${full ? "console-full" : ""}`}>
      <div className="console-bar" role="toolbar" aria-label="Console filters">
        <span className="console-seg" role="radiogroup" aria-label="Level">
          {LEVELS.map(([v, label]) => (
            <button key={v} type="button" role="radio" aria-checked={level === v} className={level === v ? "on" : ""} onClick={() => setLevel(v)}>{label}</button>))}
        </span>
        <select value={source} aria-label="Source" onChange={(e) => setSource(e.target.value)}>
          <option value="">All sources</option>
          {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {projectId && (
          <label className="console-check"><input type="checkbox" className="switch" checked={onlyProject}
            onChange={(e) => setOnlyProject(e.target.checked)} /> This project</label>)}
        <input type="search" className="console-search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the console" />
        <span className="grow" />
        {paused ? <button type="button" className="ghost small" onClick={resume}>▶ Resume{held.length ? ` (${held.length} new)` : ""}</button>
          : <button type="button" className="ghost small" onClick={() => setPaused(true)}>⏸ Pause</button>}
        <button type="button" className="ghost small" title="Copy the lines shown" onClick={() => copy(lines.map((l) =>
          `${l.ts} ${l.level.toUpperCase()} [${l.source}] ${l.message}`).join("\n"))}>Copy</button>
      </div>
      <div className="console-lines" ref={box} onScroll={onScroll} role="log" aria-live="polite">
        {more && lines.length > 0 && <button type="button" className="console-older" onClick={older}>Load older lines</button>}
        {lines.length === 0 && <p className="console-empty">Nothing here yet. Lines appear as FilmLab works: renders, the director's steps, WanGP, warnings.</p>}
        {lines.map((l) => (
          <div key={l.id} className={`console-line lvl-${l.level}`}>
            <button type="button" className="console-row" onClick={() => l.has_detail && toggle(l)} aria-expanded={l.has_detail ? l.id in open : undefined}
              disabled={!l.has_detail}>
              <span className="c-time">{time(l.ts)}</span>
              <span className={`c-lvl c-${l.level}`}>{l.level === "info" ? "info" : l.level === "warn" ? "warn" : "error"}</span>
              <span className={`c-src c-src-${l.source.toLowerCase()}`}>{l.source}</span>
              <span className="c-msg">{l.message}</span>
              {l.has_detail && <span className="c-more" aria-hidden>{l.id in open ? "▾" : "▸"}</span>}
            </button>
            {l.id in open && (
              <div className="console-detail">
                {open[l.id] === null ? <span className="c-dim">Loading…</span> : <>
                  <button type="button" className="ghost small console-copy" onClick={() => copy(open[l.id]!)}>Copy</button>
                  <pre>{open[l.id]}</pre>
                </>}
              </div>)}
          </div>))}
      </div>
    </div>
  );
}

/** The drawer at the bottom of every page. */
export function ConsoleDrawer({ subscribe, projectId, onClose }: { subscribe: Subscribe; projectId: string | null; onClose: () => void }) {
  return (
    <section className="console-drawer glass" aria-label="Console">
      <header className="console-head">
        <strong>&gt;_ Console</strong>
        <span className="muted small grow">What FilmLab is doing in the background</span>
        <a className="small" href="#/console" onClick={onClose}>Full page ↗</a>
        <button type="button" className="ghost small" aria-label="Close the console" onClick={onClose}>✕</button>
      </header>
      <ConsoleView subscribe={subscribe} projectId={projectId} />
    </section>
  );
}

/** The ` key opens and closes it (not while typing). */
export function useConsoleKey(toggle: () => void) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== "`" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]")) return;
      e.preventDefault(); toggle();
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, [toggle]);
}

/** A badge on the >_ button: errors that arrived while the console was closed. */
export function useUnseenErrors(subscribe: Subscribe, open: boolean) {
  const [n, setN] = useState(0);
  const isOpen = useRef(open);
  isOpen.current = open;
  useEffect(() => { if (open) setN(0); }, [open]);
  useEffect(() => subscribe((e: FilmLabEvent) => {
    if (e.type === "console.entry" && (e.payload as { level?: string }).level === "error" && !isOpen.current) setN((x) => x + 1);
  }, () => undefined), [subscribe]);
  return n;
}
