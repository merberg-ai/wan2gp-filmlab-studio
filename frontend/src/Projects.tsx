import { type FormEvent, useCallback, useEffect, useState } from "react";
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
              <li key={p.id}>
                <a className="card project-card" href={`#/p/${p.id}`}>
                  <strong>{p.title}</strong>
                  <span className="muted">{p.logline || "No logline"}</span>
                  <span className="muted small">{p.shot_count ?? 0} shots · updated {new Date(p.updated_at).toLocaleString()}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
