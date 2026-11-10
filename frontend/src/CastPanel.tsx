/** 🎭 Cast: the library assets a film uses (characters, locations, props, styles, sounds), picked in a panel that
 *  slides in from the right. With "Only this cast" the director sees and may use only these (backend/app/storyboard.py
 *  cast_ids); a scene it leaves without a place gets the cast location its description names. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ask, dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type ProjectDetail } from "./api";
import { type Asset, type AssetKind, KIND_LABEL, KINDS, lib } from "./library-api";

type Mode = "cast" | "fit" | "none";
const MODES: { id: Mode; title: string; text: string }[] = [
  { id: "cast", title: "Only this cast", text: "The director sees and uses only the assets below." },
  { id: "fit", title: "Whole library, where the brief calls for it", text: "Every asset is offered; the brief decides." },
  { id: "none", title: "Fresh start: no Library", text: "Nothing from the Library; new designs only." },
];

export function CastPanel({ project, onClose, onSaved }: { project: ProjectDetail; onClose: () => void; onSaved: () => void }) {
  const [all, setAll] = useState<Asset[] | null>(null);
  const [picked, setPicked] = useState<string[]>(project.cast.map((a) => a.id));
  const [mode, setMode] = useState<Mode>((project.plan_library as Mode) ?? "fit");
  const [kind, setKind] = useState<AssetKind | "">("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const backdrop = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  useModalFrame(backdrop);
  useEffect(() => { lib.search("", "").then(setAll).catch((e) => setError((e as Error).message)); }, []);

  const initial = useMemo(() => new Set(project.cast.map((a) => a.id)), [project.cast]);
  const pickedSet = new Set(picked);
  const changed = mode !== (project.plan_library ?? "fit") || picked.length !== initial.size || picked.some((id) => !initial.has(id));
  const byId = useMemo(() => Object.fromEntries((all ?? []).map((a) => [a.id, a])), [all]);
  const castAssets = picked.map((id) => byId[id] ?? project.cast.find((a) => a.id === id)).filter(Boolean) as Asset[];
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (all ?? []).filter((a) => (!kind || a.kind === kind)
    && words.every((w) => `${a.name} ${a.tags} ${a.description}`.toLowerCase().replace(/_/g, " ").includes(w.replace(/_/g, " "))));
  const notIn = shown.filter((a) => !pickedSet.has(a.id));

  const close = async () => {
    if (busy) return;
    if (changed && !(await ask("Close without saving the cast?", { confirm: "Discard changes", danger: true }))) return;
    setClosing(true);
    window.setTimeout(onClose, 160);
  };
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e) => {
    if (dialogOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); void close(); } else trapTab(e, panel.current);
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api.setCast(project.id, picked);
      if (mode !== (project.plan_library ?? "fit")) await api.updateProject(project.id, { plan_library: mode });
      onSaved();
      setClosing(true);
      window.setTimeout(onClose, 160);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };

  const grouped = KINDS.map((k) => [k, castAssets.filter((a) => a.kind === k)] as const).filter(([, list]) => list.length);
  return createPortal(
    <div ref={backdrop} className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={(e) => { if (e.target === e.currentTarget) void close(); }}>
      <aside ref={panel} className={`drawer glass cast-panel ${closing ? "closing" : ""}`} role="dialog" aria-modal="true" aria-labelledby="cast-title">
        <header className="sm-head">
          <h3 id="cast-title">🎭 Cast · {project.title}</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={() => void close()} disabled={busy}>✕</button>
        </header>
        <div className="drawer-body">
          <section className="cast-mode" role="radiogroup" aria-label="What the director may use">
            {MODES.map((m) => (
              <label key={m.id} className={`np-option ${mode === m.id ? "on" : ""}`}>
                <input type="radio" name="cast-mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
                <span><strong>{m.title}</strong><span className="block muted small">{m.text}</span></span>
              </label>))}
          </section>

          <section className={`cast-current ${mode === "none" ? "dim" : ""}`}>
            <h4>In this film <span className="muted">({picked.length})</span></h4>
            {!picked.length ? <p className="muted small">Nothing picked yet: tick assets below.
              {mode === "cast" ? " With “Only this cast” and no cast, the director plans without references." : ""}</p>
              : grouped.map(([k, list]) => (
                <div key={k} className="cast-group">
                  <span className="small muted">{KIND_LABEL[k]}s</span>
                  <ul>{list.map((a) => (
                    <li key={a.id} className="cast-chip" title={a.description}>
                      {a.cover_file_id ? <img src={lib.fileUrl(a.cover_file_id, true)} alt="" loading="lazy" /> : <span className="noimg" />}
                      <span className="cast-name">{a.name}</span>
                      <button type="button" className="ghost small" aria-label={`Remove ${a.name}`} onClick={() => toggle(a.id)}>✕</button>
                    </li>))}</ul>
                </div>))}
          </section>

          <section className="cast-browse">
            <h4>Library</h4>
            <input type="search" placeholder="Search names, tags, descriptions (e.g. heavy metal)" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="row small lib-kinds" role="group" aria-label="Kind">
              {(["", ...KINDS] as const).map((k) => (
                <button key={k} type="button" className={`ghost small ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>
                  {k ? KIND_LABEL[k] + "s" : "All"}</button>))}
            </div>
            {notIn.length > 1 && words.length > 0 && (
              <button type="button" className="ghost small cast-add-all" onClick={() => setPicked((p) => [...p, ...notIn.map((a) => a.id)])}>
                + Add all {notIn.length} shown</button>)}
            {all === null ? <p className="muted small">Loading the library…</p> : (
              <ul className="cast-list">
                {shown.map((a) => (
                  <li key={a.id}>
                    <label className={`cast-row ${pickedSet.has(a.id) ? "on" : ""}`}>
                      <input type="checkbox" checked={pickedSet.has(a.id)} onChange={() => toggle(a.id)} />
                      {a.cover_file_id ? <img src={lib.fileUrl(a.cover_file_id, true)} alt="" loading="lazy" /> : <span className="noimg" />}
                      <span className="cast-text"><strong>{a.name}</strong>
                        <span className="block muted small">{KIND_LABEL[a.kind]}{a.description ? ` · ${a.description}` : ""}</span></span>
                    </label>
                  </li>))}
                {!shown.length && <li className="muted small">Nothing matches.</li>}
              </ul>)}
          </section>
        </div>
        <footer className="sm-foot">
          {error && <p className="error small" role="alert">{error}</p>}
          <div className="row modal-actions">
            <span className="grow muted small">{changed ? "Unsaved changes" : ""}</span>
            <button type="button" className="ghost" onClick={() => void close()} disabled={busy}>Cancel</button>
            <button type="button" className="primary" onClick={save} disabled={busy || !changed}>{busy ? "Saving…" : "Save cast"}</button>
          </div>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}
