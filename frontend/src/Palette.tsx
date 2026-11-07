/** Ctrl+K (⌘K on a Mac): jump to any page, project, Library item or shot by typing a few letters. */
import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, type Project, type Shot } from "./api";
import { dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { lib } from "./library-api";
import type { Asset } from "./library-api";
import { notifyState, setNotify } from "./notify";

interface Item { key: string; group: string; label: string; hint?: string; run: () => void }
type Route = { page: string; id?: string | null };

/** Open a shot in the project view (ProjectView listens), after switching to its project if needed. */
export const SELECT_SHOT = "filmlab:select-shot";
export function goShot(projectId: string, shotId: string, takeId?: string) {
  const target = `#/p/${projectId}`;
  if (location.hash !== target) location.hash = target;
  // the project view may still be mounting: ask twice, it ignores repeats
  const send = () => window.dispatchEvent(new CustomEvent(SELECT_SHOT, { detail: { projectId, shotId, takeId } }));
  window.setTimeout(send, 50); window.setTimeout(send, 600);
}

/** Matches when every word of the query starts a word in the text, or appears in it ("ant and" → "Anthony the Android"). */
function score(text: string, q: string): number {
  const t = text.toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  let s = 0;
  for (const w of words) {
    const i = t.indexOf(w);
    if (i < 0) return -1;
    s += i === 0 ? 3 : /\W/.test(t[i - 1]) ? 2 : 1;
  }
  return s;
}

export function CommandPalette({ route }: { route: Route }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        if (dialogOpen()) return;
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("filmlab:palette", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("filmlab:palette", onOpen); };
  }, []);
  return open ? <PaletteDialog route={route} onClose={() => setOpen(false)} /> : null;
}

function PaletteDialog({ route, onClose }: { route: Route; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  useModalFrame(backdrop);
  const projectId = route.page === "project" || route.page === "sheet" ? route.id ?? null : null;

  useEffect(() => {
    api.projects().then(setProjects).catch(() => setProjects([]));
    lib.search().then(setAssets).catch(() => setAssets([]));
    if (projectId) api.project(projectId).then((p) => setShots(p.shots)).catch(() => setShots([]));
  }, [projectId]);

  const items = useMemo(() => {
    const go = (hash: string) => () => { location.hash = hash; };
    const all: Item[] = [];
    if (projectId) {
      const title = projects.find((p) => p.id === projectId)?.title ?? "this project";
      all.push(
        { key: "t-sb", group: title, label: "Storyboard", run: go(`#/p/${projectId}`) },
        { key: "t-ed", group: title, label: "Edit & export", run: go(`#/p/${projectId}/edit`) },
        { key: "t-cs", group: title, label: "Contact sheet", run: go(`#/p/${projectId}/sheet`) },
        ...shots.map((s) => ({ key: `s-${s.id}`, group: title, label: `${s.position}. ${s.title}`,
          hint: `shot · ${((s.frames - 1) / 24).toFixed(1)} s`, run: () => goShot(projectId, s.id) })),
      );
    }
    all.push(
      { key: "p-home", group: "Go to", label: "Projects", run: go("#/") },
      { key: "p-lib", group: "Go to", label: "Library", run: go("#/library") },
      { key: "p-set", group: "Go to", label: "Settings", run: go("#/settings") },
      { key: "p-sys", group: "Go to", label: "Health & backups", run: go("#/system") },
      { key: "p-del", group: "Go to", label: "Recently deleted", run: go("#/deleted") },
      ...projects.filter((p) => p.id !== projectId).map((p) => ({ key: `pr-${p.id}`, group: "Projects", label: p.title,
        hint: p.logline || undefined, run: go(`#/p/${p.id}`) })),
      ...assets.map((a) => ({ key: `a-${a.id}`, group: "Library", label: a.name, hint: a.kind, run: go(`#/library/${a.id}`) })),
    );
    const n = notifyState();
    if (n === "on" || n === "off") {
      all.push({ key: "x-notify", group: "Actions", label: n === "on" ? "Turn off render notifications" : "Turn on render notifications",
        hint: "this device", run: () => void setNotify(n !== "on") });
    }
    if (!q.trim()) return all.filter((i) => i.group !== "Library").slice(0, 40);  // the Library can be long; type to search it
    return all.map((i) => ({ i, s: Math.max(score(i.label, q), score(`${i.label} ${i.hint ?? ""} ${i.group}`, q) - 1) }))
      .filter((x) => x.s >= 0).sort((a, b) => b.s - a.s).slice(0, 40).map((x) => x.i);
  }, [q, projects, assets, shots, projectId]);

  useEffect(() => setAt(0), [q]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${at}"]`)?.scrollIntoView({ block: "nearest" });
  }, [at]);

  const choose = (i: Item | undefined) => { if (!i) return; onClose(); i.run(); };
  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setAt((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[at]); }
    else trapTab(e.nativeEvent, card.current);
  };

  let lastGroup = "";
  const grouped = !q.trim();  // results of a search are ranked across groups, so the group goes in the hint
  return (
    <div ref={backdrop} className="modal-backdrop top palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={card} className="modal glass palette" role="dialog" aria-modal="true" aria-label="Go to" onKeyDown={onKey}>
        <input autoFocus className="palette-input" placeholder="Go to a project, shot, page or Library item…" value={q}
          onChange={(e) => setQ(e.target.value)} aria-controls="palette-list"
          aria-activedescendant={items[at] ? `pal-${items[at].key}` : undefined} role="combobox" aria-expanded="true" />
        <ul id="palette-list" ref={list} className="palette-list" role="listbox">
          {items.length === 0 && <li className="muted small palette-empty">Nothing matches “{q}”.</li>}
          {items.map((it, n) => {
            const head = grouped && it.group !== lastGroup ? (lastGroup = it.group) : null;
            const hint = grouped ? it.hint : [it.hint, it.group].filter(Boolean).join(" · ");
            return (
              <li key={it.key} role="presentation">
                {head && <div className="palette-group">{head}</div>}
                <button id={`pal-${it.key}`} data-i={n} role="option" aria-selected={n === at} className={n === at ? "on" : ""}
                  onMouseMove={() => setAt(n)} onClick={() => choose(it)} tabIndex={-1}>
                  <span className="palette-label">{it.label}</span>{hint && <span className="muted small palette-hint">{hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="palette-foot muted small"><span>↑↓ to move · Enter to open · Esc to close</span><span>Ctrl K</span></div>
      </div>
    </div>
  );
}
