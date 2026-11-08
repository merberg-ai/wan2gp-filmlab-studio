import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type Project, type ProjectTemplate } from "./api";
import type { Listener } from "./App";

type Subscribe = (l: Listener, r: () => void) => () => void;

export function Projects({ subscribe }: { subscribe: Subscribe }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [title, setTitle] = useState("");
  const [logline, setLogline] = useState("");
  const [template, setTemplate] = useState("");
  const [templates, setTemplates] = useState<ProjectTemplate[]>([]);
  useEffect(() => { api.projectTemplates().then(setTemplates).catch(() => setTemplates([])); }, []);
  const chosen = templates.find((t) => t.id === template);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => { api.projects().then(setProjects).catch((e) => setError(e.message)); }, []);
  useEffect(() => { load(); return subscribe((e) => e.type === "project.updated" && load(), load); }, [load, subscribe]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const p = await api.createProject(title.trim(), logline.trim(), template);
      location.hash = `#/p/${p.id}`;
    } catch (err) { setError((err as Error).message); }
  };

  return (
    <main className="page">
      <section className="card">
        <h2>New project</h2>
        <form className="row" onSubmit={create}>
          <input placeholder="Title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
          <input className="grow" placeholder="Logline (optional)" value={logline} maxLength={1000} onChange={(e) => setLogline(e.target.value)} />
          <select value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="Start from">
            <option value="">Blank project</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <button className="primary" disabled={!title.trim()}>Create</button>
        </form>
        {chosen && <p className="muted small">{chosen.description} {chosen.target_seconds} s target, {Math.round((chosen.default_frames - 1) / 24)} s shots
          at {chosen.default_resolution}. The director plans with this style: {chosen.style}</p>}
        {error && <p className="error">{error}</p>}
      </section>
      <section>
        <h2>Projects</h2>
        {projects === null ? <p className="muted">Loading…</p> : projects.length === 0 ? <p className="muted">No projects yet.</p> : (
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.id}><ProjectCard p={p} /></li>
            ))}
          </ul>
        )}
      </section>
    </main>
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
