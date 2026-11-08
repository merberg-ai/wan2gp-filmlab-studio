import { useEffect, useRef, useState } from "react";
import { ask, dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type ProjectDetail } from "./api";
import { type Capability, lib, templateLabel } from "./library-api";
import { AUTONOMY } from "./Autonomy";
import { ResolutionPicker } from "./ResolutionPicker";
import { MusicVideoSetupModal, setupDismissed } from "./MusicVideo";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
type Section = "basics" | "shots" | "models" | "director" | "danger";
type Autonomy = ProjectDetail["autonomy"];
const framesFor = (secs: number) => Math.round(Math.round(secs * 24) / 8) * 8 + 1;
const secsFor = (frames: number) => Number(((frames - 1) / 24).toFixed(1));
const BUSY = ["submitting", "queued", "running", "cancelling", "restored"];
const short = (t: string) => t.replace(/\.json$/, "");

/** The project header's settings: a gear button and chips that summarise them; each opens the settings modal. */
export function ProjectSettings({ project, act }: { project: ProjectDetail; act: Act }) {
  const [open, setOpen] = useState<Section | null>(null);
  // A new music-video project opens its setup by itself (until it's set up or put off with "Not now").
  const [music, setMusic] = useState(false);
  const musical = project.template === "music_video" || !!project.music_mode;
  useEffect(() => {
    if (project.template === "music_video" && !project.music_mode && !setupDismissed(project.id)) setMusic(true);
  }, [project.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const models = [project.video_refs_template, project.video_plain_template].filter(Boolean) as string[];
  const chip = (section: Section, label: string, text: string) => (
    <button type="button" className="chip setting-chip" title={`${label}: ${text}`} onClick={() => setOpen(section)}>{text}</button>
  );
  return (
    <div className="project-summary">
      <button type="button" className="ghost small gear" onClick={() => setOpen("basics")} aria-haspopup="dialog">
        <span aria-hidden="true">⚙</span> Settings
      </button>
      {chip("shots", "New shots", `${project.default_resolution} · ${secsFor(project.default_frames)} s`)}
      {chip("director", "Director autonomy", project.autonomy ?? "A2")}
      {chip("models", "Video models", models.length ? models.map(short).join(" / ") : "studio models")}
      {musical && <button type="button" className="chip setting-chip" title="Music video: the song and how the shots follow it"
        onClick={() => setMusic(true)}>🎵 {project.music_mode === "lipsync" ? "Lip-sync" : project.music_mode === "soundtrack"
          ? "Soundtrack" : "Set up the song"}</button>}
      {music && <MusicVideoSetupModal project={project} onClose={() => setMusic(false)} onSaved={() => act(async () => undefined)} />}
      {open && <SettingsModal project={project} act={act} section={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function SettingsModal({ project, act, section, onClose }: {
  project: ProjectDetail; act: Act; section: Section; onClose: () => void;
}) {
  const initial = () => ({
    title: project.title, logline: project.logline, size: project.default_resolution, secs: secsFor(project.default_frames),
    refs: project.video_refs_template ?? "", plain: project.video_plain_template ?? "", autonomy: (project.autonomy ?? "A2") as Autonomy,
  });
  const [f, setF] = useState(initial);  // a draft: nothing is saved until Save
  const [caps, setCaps] = useState<Capability[] | null>(null);
  const [defaults, setDefaults] = useState<{ video_refs: string; video_plain: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const before = useRef<Element | null>(document.activeElement);
  const sections = useRef<Partial<Record<Section, HTMLElement | null>>>({});
  useModalFrame(backdrop);

  useEffect(() => {
    lib.capabilities().then((c) => { setCaps(c.templates); setDefaults(c.settings); }).catch(() => setCaps([]));
  }, []);

  const frames = framesFor(f.secs);
  const projectChanged = f.title !== project.title || f.logline !== project.logline || f.size !== project.default_resolution
    || frames !== project.default_frames || f.autonomy !== (project.autonomy ?? "A2");
  const modelsChanged = f.refs !== (project.video_refs_template ?? "") || f.plain !== (project.video_plain_template ?? "");
  const dirty = projectChanged || modelsChanged;
  const validSecs = f.secs >= 0.5 && f.secs <= 20;

  const close = () => { onClose(); (before.current as HTMLElement | null)?.focus?.(); };
  const cancel = async () => {
    if (busy) return;
    if (dirty && !await ask("Discard your changes? The project settings stay as they were.",
      { confirm: "Discard", danger: true })) return;
    close();
  };
  const save = async () => {
    if (busy || !f.title.trim() || !validSecs) return;
    if (!dirty) { close(); return; }
    if (f.autonomy === "A3" && project.autonomy !== "A3" && !await ask("Turn on bounded redo (A3)? You can then start "
      + "redo runs in which FilmLab renders the director's fixes by itself, within limits you set for each run (redos per "
      + "shot, renders, GPU minutes, time). Nothing starts until you start a run.", { confirm: "Turn on" })) return;
    setBusy(true); setError(null);
    try {
      if (projectChanged) {
        await api.updateProject(project.id, {  // only what changed
          ...(f.title !== project.title && { title: f.title.trim() }), ...(f.logline !== project.logline && { logline: f.logline }),
          ...(f.size !== project.default_resolution && { default_resolution: f.size }),
          ...(frames !== project.default_frames && { default_frames: frames }),
          ...(f.autonomy !== (project.autonomy ?? "A2") && { autonomy: f.autonomy }),
        });
      }
      if (modelsChanged) {
        await lib.projectModels(project.id, { video_refs_template: f.refs, video_plain_template: f.plain });
      }
      await act(async () => undefined);  // reload the project
      close();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const remove = async () => {
    if (project.jobs.some((j) => BUSY.includes(j.status))) {
      setError("A render in this project is still running; cancel it first.");
      return;
    }
    if (!await ask(`Move “${project.title}” to Recently deleted?\n\nIts ${project.shots.length} shot(s), takes and chat go with it. `
      + "Nothing is removed from disk until you choose “Delete forever” there, so you can restore it.")) return;
    setBusy(true);
    try {
      await api.deleteProject(project.id);
      onClose();
      location.hash = "#/";
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };

  // Open at the section that was asked for; keys go to the confirm dialog while one is on top.
  useEffect(() => {
    const el = sections.current[section];
    el?.scrollIntoView({ block: "start" });
    el?.querySelector<HTMLElement>("input, select, textarea, button")?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); cancel(); }
    else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    else trapTab(e, card.current);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const video = (caps ?? []).filter((c) => c.task === "gen_video" || c.task === "gen_video_with_refs");
  const modelSelect = (value: string, set: (v: string) => void, fallback: string | undefined, fits: (c: Capability) => boolean) => (
    <select value={value} disabled={caps === null} onChange={(e) => set(e.target.value)}>
      <option value="">Studio default{fallback ? ` (${fallback})` : ""}</option>
      {value && !video.some((c) => c.template === value) && <option value={value}>{value}</option>}
      {video.filter(fits).map((c) => <option key={c.task + c.template} value={c.template} disabled={!c.valid}>{templateLabel(c)}</option>)}
    </select>
  );
  const reg = (s: Section) => (el: HTMLElement | null) => { sections.current[s] = el; };

  return (
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) cancel(); }}>
      <div ref={card} className="modal glass settings-modal" role="dialog" aria-modal="true" aria-labelledby="ps-title">
        <header className="sm-head">
          <h3 id="ps-title">Project settings</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={cancel}>✕</button>
        </header>
        <form className="sm-body" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <section ref={reg("basics")}>
            <h4>Basics</h4>
            <label>Title<input value={f.title} maxLength={120} required onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
            <label>Logline<textarea rows={3} maxLength={1000} value={f.logline} onChange={(e) => setF({ ...f, logline: e.target.value })} /></label>
          </section>
          <section ref={reg("shots")}>
            <h4>New shots</h4>
            <div className="sm-grid">
              <div className="sm-field sm-wide"><span>Size</span>
                <ResolutionPicker value={f.size} onChange={(size) => setF({ ...f, size })} />
              </div>
              <label>Length (s)
                <input type="number" min={0.5} max={20} step={0.5} value={f.secs} onChange={(e) => setF({ ...f, secs: Number(e.target.value) })} />
              </label>
            </div>
            <p className="muted small">{validSecs ? `${frames} frames. ` : "Length must be 0.5-20 s. "}
              Used by + Shot and by the director when a shot doesn't say. Existing shots keep their own.</p>
          </section>
          <section ref={reg("models")}>
            <h4>Video models</h4>
            <label>Shots with references{modelSelect(f.refs, (v) => setF({ ...f, refs: v }), defaults?.video_refs, (c) => c.refs !== "none")}</label>
            <label>Shots without references{modelSelect(f.plain, (v) => setF({ ...f, plain: v }), defaults?.video_plain, (c) => c.task === "gen_video")}</label>
            {caps === null && <p className="muted small">Loading WanGP's models…</p>}
          </section>
          <section ref={reg("director")}>
            <h4>Director</h4>
            <label>Autonomy
              <select value={f.autonomy} onChange={(e) => setF({ ...f, autonomy: e.target.value as Autonomy })}>
                {Object.entries(AUTONOMY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          </section>
          <section ref={reg("danger")} className="sm-danger">
            <h4>Danger zone</h4>
            <div className="row">
              <span className="muted small grow">Moves the project, its shots, takes and chat to Recently deleted.</span>
              <button type="button" className="danger small" disabled={busy} onClick={remove}>Delete project…</button>
            </div>
          </section>
          <button type="submit" hidden />
        </form>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="modal-actions">
            <span className="muted small grow sm-hint">{dirty ? "Unsaved changes" : "No changes"}</span>
            <button type="button" className="ghost dismiss" onClick={cancel} disabled={busy}>Cancel</button>
            <button type="button" className="primary confirm" onClick={save}
                    disabled={busy || !f.title.trim() || !validSecs}>{busy ? "Saving…" : "Save"}</button>
          </div>
        </footer>
      </div>
    </div>
  );
}
