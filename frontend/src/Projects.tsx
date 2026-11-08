import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ask, dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type Project, type ProjectTemplate } from "./api";
import type { Listener } from "./App";

type Subscribe = (l: Listener, r: () => void) => () => void;

export function Projects({ subscribe }: { subscribe: Subscribe }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<ListView>(readView);
  useEffect(() => { try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)); } catch { /* this visit only */ } }, [view]);

  const load = useCallback(() => { api.projects().then(setProjects).catch((e) => setError(e.message)); }, []);
  useEffect(() => { load(); return subscribe((e) => e.type === "project.updated" && load(), load); }, [load, subscribe]);

  const shown = useMemo(() => {
    if (!projects) return [];
    const q = view.q.trim().toLowerCase();
    const list = projects.filter((p) => (!q || `${p.title} ${p.logline}`.toLowerCase().includes(q))
      && (view.kind === "all" || kindOf(p) === view.kind)
      && (view.state === "all" || stateOf(p) === view.state));
    const by: Record<SortKey, (a: Project, b: Project) => number> = {
      updated: (a, b) => b.updated_at.localeCompare(a.updated_at),
      created: (a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""),
      name: (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
      shots: (a, b) => (b.shot_count ?? 0) - (a.shot_count ?? 0),
      length: (a, b) => (b.seconds ?? 0) - (a.seconds ?? 0),
    };
    return [...list].sort(by[view.sort]);
  }, [projects, view]);
  const kinds = useMemo(() => [...new Set((projects ?? []).map(kindOf))], [projects]);
  const filtered = !!view.q.trim() || view.kind !== "all" || view.state !== "all";
  const showAll = () => setView({ ...view, q: "", kind: "all", state: "all" });

  return (
    <main className="page">
      <section>
        <div className="projects-head">
          <h2>Projects</h2>
          {projects && projects.length > 0 && (
            <span className="muted small grow">{filtered ? `${shown.length} of ${projects.length}` : projects.length}</span>)}
          <button className="primary small" onClick={() => setCreating(true)}>+ New project</button>
        </div>
        {projects && projects.length > 1 && (
          <div className="projects-toolbar" role="search">
            <input type="search" className="pt-search" placeholder="Search projects" value={view.q} aria-label="Search projects"
              onChange={(e) => setView({ ...view, q: e.target.value })} />
            <select value={view.kind} aria-label="Type" onChange={(e) => setView({ ...view, kind: e.target.value })}>
              <option value="all">All types</option>
              {kinds.map((k) => <option key={k} value={k}>{k === "blank" ? "Blank" : STYLE_LABEL[k] ?? k}</option>)}
            </select>
            <select value={view.state} aria-label="Stage" onChange={(e) => setView({ ...view, state: e.target.value as StateKey })}>
              {(Object.keys(STATE_LABEL) as StateKey[]).map((k) => <option key={k} value={k}>{STATE_LABEL[k]}</option>)}
            </select>
            <select value={view.sort} aria-label="Sort by" title="Sort by" onChange={(e) => setView({ ...view, sort: e.target.value as SortKey })}>
              {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => <option key={k} value={k}>{SORT_LABEL[k]}</option>)}
            </select>
            {filtered && <button className="ghost small" onClick={showAll}>Clear</button>}
          </div>
        )}
        {error && <p className="error">{error}</p>}
        {projects === null ? <p className="muted">Loading…</p>
          : projects.length === 0 ? (
            <div className="card projects-empty">
              <p>No projects yet. Start one: give it a title and pick a starting point.</p>
              <button className="primary" onClick={() => setCreating(true)}>+ New project</button>
            </div>)
          : shown.length === 0 ? (
            <p className="muted">No projects match. <button className="ghost small" onClick={showAll}>Show all</button></p>)
          : (
            <ul className="project-list">
              {shown.map((p) => <li key={p.id}><ProjectCard p={p} /></li>)}
            </ul>
          )}
      </section>
      {creating && createPortal(<NewProjectModal onClose={() => setCreating(false)} />, document.body)}
    </main>
  );
}

// --- sort and filter (remembered in this browser) ---------------------------------------------------

type SortKey = "updated" | "created" | "name" | "shots" | "length";
type StateKey = "all" | "empty" | "planned" | "rendering" | "rendered";
interface ListView { q: string; kind: string; state: StateKey; sort: SortKey }
const VIEW_KEY = "filmlab:projects-view";
const SORT_LABEL: Record<SortKey, string> = {
  updated: "Updated", created: "Newest", name: "Name A–Z", shots: "Most shots", length: "Longest",
};
const STATE_LABEL: Record<StateKey, string> = {
  all: "Any stage", empty: "Not planned", planned: "Planned", rendering: "Rendering", rendered: "Has renders",
};
function kindOf(p: Project) { return (p.music_mode ? "music_video" : p.template) || "blank"; }
function stateOf(p: Project): StateKey {
  if (p.rendering) return "rendering";
  if (!p.shot_count) return "empty";
  return p.shots_rendered ? "rendered" : "planned";
}
function readView(): ListView {
  const base: ListView = { q: "", kind: "all", state: "all", sort: "updated" };
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null");
    if (v && typeof v === "object") return { ...base, ...v, q: "" };  // the search starts empty each visit
  } catch { /* nothing saved */ }
  return base;
}

// --- new project -------------------------------------------------------------------------------------

/** New project: title, logline and a starting point; Create opens it. */
function NewProjectModal({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [logline, setLogline] = useState("");
  const [template, setTemplate] = useState("");
  const [templates, setTemplates] = useState<ProjectTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const titleInput = useRef<HTMLInputElement>(null);
  useModalFrame(backdrop);
  useEffect(() => {
    api.projectTemplates().then(setTemplates).catch(() => setTemplates([]));
    titleInput.current?.focus();
  }, []);
  const dirty = !!(title.trim() || logline.trim() || template);
  const cancel = async () => {
    if (busy) return;
    if (dirty && !await ask("Discard this new project?", { confirm: "Discard", danger: true })) return;
    onClose();
  };
  const create = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const p = await api.createProject(title.trim(), logline.trim(), template);
      onClose();
      location.hash = `#/p/${p.id}`;
    } catch (err) { setError((err as Error).message); setBusy(false); }
  };
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); cancel(); } else trapTab(e, card.current);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const options = [
    { id: "", name: "Blank project", description: "No preset style: you set everything." },
    ...templates.map((t) => ({ id: t.id, name: t.name, description: t.description })),
  ];
  const chosen = templates.find((t) => t.id === template);

  return (
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) cancel(); }}>
      <div ref={card} className="modal glass settings-modal new-project" role="dialog" aria-modal="true" aria-labelledby="np-title">
        <header className="sm-head">
          <h3 id="np-title">New project</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={cancel}>✕</button>
        </header>
        <form className="sm-body" onSubmit={create}>
          <label>Title
            <input ref={titleInput} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. The Dead March" /></label>
          <label>Logline <span className="muted small">· optional: the film in one sentence</span>
            <textarea rows={2} value={logline} maxLength={1000} onChange={(e) => setLogline(e.target.value)} /></label>
          <div className="np-start" role="radiogroup" aria-label="Start from">
            <span className="small">Start from</span>
            {options.map((o) => (
              <label key={o.id || "blank"} className={`np-option ${template === o.id ? "on" : ""}`}>
                <input type="radio" name="np-template" checked={template === o.id} onChange={() => setTemplate(o.id)} />
                <span><strong>{o.name}</strong><span className="block muted small">{o.description}</span></span>
              </label>))}
          </div>
          {chosen && (
            <p className="muted small">{chosen.target_seconds} s film, {Math.round((chosen.default_frames - 1) / 24)} s shots at
              {" "}{chosen.default_resolution}.{chosen.id === "music_video" ? " The music video setup opens next." : ""}</p>)}
          <button type="submit" hidden />
        </form>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={cancel} disabled={busy}>Cancel</button>
            <button type="button" className="primary" disabled={!title.trim() || busy} onClick={() => create()}>
              {busy ? "Creating…" : "Create and open"}</button>
          </div>
        </footer>
      </div>
    </div>
  );
}

const STYLE_LABEL: Record<string, string> = {
  horror_teaser: "Horror teaser", music_video: "Music video", illustrated_story: "Illustrated story",
  character_dialogue: "Dialogue",
};

function ago(iso: string) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

/** A project on the Projects page: its picture (hover plays the cover take, muted), title, logline and progress. */
function ProjectCard({ p }: { p: Project }) {
  const [playing, setPlaying] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const canPlay = !!p.cover?.video && typeof matchMedia === "function" && matchMedia("(hover: hover)").matches;
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    if (playing) v.play().catch(() => undefined); else { v.pause(); v.currentTime = 0; }
  }, [playing]);
  const style = (p.music_mode && "music_video") || p.template;
  const initials = p.title.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
  const facts = [`${p.shot_count ?? 0} shot${p.shot_count === 1 ? "" : "s"}`,
    p.shot_count ? `${p.shots_rendered ?? 0} rendered` : "", p.seconds ? `${Math.round(p.seconds)} s` : ""].filter(Boolean);
  return (
    <a className="card project-card" href={`#/p/${p.id}`} onMouseEnter={() => canPlay && setPlaying(true)}
      onMouseLeave={() => setPlaying(false)}>
      <div className={`pc-picture ${p.cover ? "" : "pc-empty"}`}>
        {p.cover ? <img src={p.cover.thumb} alt="" loading="lazy" />
          : <span className="pc-initials" aria-hidden>{style === "music_video" ? "🎵" : initials || "🎬"}</span>}
        {canPlay && playing && <video ref={video} src={p.cover!.video} muted loop playsInline preload="auto" />}
        {style && STYLE_LABEL[style] && <span className="pc-tag">{STYLE_LABEL[style]}</span>}
        {p.rendering && <span className="pc-live" title="Rendering now"><i className="pulse" /> Rendering</span>}
      </div>
      <div className="pc-text">
        <strong>{p.title}</strong>
        <span className="muted small pc-logline">{p.logline || "No logline"}</span>
        <span className="muted small">{facts.join(" · ")} · updated {ago(p.updated_at)}</span>
      </div>
    </a>
  );
}
