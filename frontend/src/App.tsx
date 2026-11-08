import { useCallback, useEffect, useState } from "react";
import { api, type Health } from "./api";
import { type FilmLabEvent, type StreamState, useEventStream } from "./events";
import { DialogHost } from "./Dialog";
import { LibraryPage } from "./Library";
import { Login } from "./Login";
import { SelfTestPage } from "./SelfTest";
import { SettingsPage } from "./Settings";
import { ContactSheet, SystemPage } from "./System";
import { GpuMeters, type GpuSample } from "./GpuMeters";
import { TrashPage } from "./Trash";
import { ProjectView } from "./ProjectView";
import { Projects } from "./Projects";
import { alertOwner, dismissToast, setNotify, showToast, useNotifyState, useToasts } from "./notify";
import { CommandPalette } from "./Palette";
import { useUploads } from "./uploads";
import { AboutPage } from "./About";
import { ConsoleDrawer, ConsoleView, useConsoleKey, useUnseenErrors } from "./Console";
import { CreatePage } from "./Creator";
import type { AssetKind } from "./library-api";
import mark from "./brand/filmlab-mark-64.webp";
import { WanGPDownBanner } from "./Startup";

// Hash routes: #/ (projects) and #/p/<id> (project workspace).
function useRoute() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const sheet = hash.match(/^#\/p\/([0-9a-f]+)\/sheet/);
  if (sheet) return { page: "sheet" as const, id: sheet[1] };
  const m = hash.match(/^#\/p\/([0-9a-f]+)(\/edit)?/);
  if (m) return { page: "project" as const, id: m[1], tab: m[2] ? "edit" as const : "storyboard" as const };
  // the Create page: #/create[/<kind>], #/create/c/<creation id> (old Library links to a draft land there too)
  const cr = hash.match(/^#\/(?:library\/create|create\/c)\/([0-9a-f]+)/);
  if (cr) return { page: "create" as const, kind: "character" as AssetKind, creation: cr[1] };
  const ck = hash.match(/^#\/create(?:\/(character|location|prop|style|sound))?/);
  if (ck) return { page: "create" as const, kind: (ck[1] ?? "character") as AssetKind, creation: null };
  const l = hash.match(/^#\/library(?:\/([0-9a-f]+))?/);
  if (l) return { page: "library" as const, id: l[1] ?? null, creation: null };
  if (hash.startsWith("#/settings")) return { page: "settings" as const };
  if (hash.startsWith("#/deleted")) return { page: "deleted" as const };
  if (hash.startsWith("#/system")) return { page: "system" as const };
  if (hash.startsWith("#/selftest")) return { page: "selftest" as const };
  if (hash.startsWith("#/about")) return { page: "about" as const };
  if (hash.startsWith("#/console")) return { page: "console" as const };
  return { page: "projects" as const };
}

export type Listener = (e: FilmLabEvent) => void;

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => { api.me().then(setAuthed).catch(() => setAuthed(false)); }, []);
  if (authed === null) return <div className="center muted">Loading…</div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <><Studio onLogout={() => setAuthed(false)} /><DialogHost /></>;
}

function Studio({ onLogout }: { onLogout: () => void }) {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [listeners] = useState(() => new Set<Listener>());
  const [resyncs] = useState(() => new Set<() => void>());
  const [gpu, setGpu] = useState<GpuSample | null>(null);

  const refreshHealth = useCallback(() => { api.health().then(setHealth).catch(() => setHealth(null)); }, []);
  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 15000);
    return () => clearInterval(t);
  }, [refreshHealth]);

  const stream = useEventStream(
    (e) => {
      if (e.type === "system.gpu") { setGpu(e.payload as GpuSample); return; }
      listeners.forEach((l) => l(e));
      if (e.event_id !== null && (e.type === "render.done" || e.type === "render.ended")) renderFinished(e);
      if (e.type === "wangp.status") refreshHealth();  // WanGP stopped or came back
    },
    () => { resyncs.forEach((r) => r()); refreshHealth(); },
  );

  const subscribe = useCallback((l: Listener, r: () => void) => {
    listeners.add(l); resyncs.add(r);
    return () => { listeners.delete(l); resyncs.delete(r); };
  }, [listeners, resyncs]);

  const [consoleOpen, setConsoleOpen] = useState(false);
  const toggleConsole = useCallback(() => setConsoleOpen((o) => !o), []);
  useConsoleKey(toggleConsole);
  const unseen = useUnseenErrors(subscribe, consoleOpen);

  return (
    <div className={`shell ${consoleOpen ? "console-open" : ""}`}>
      <header className="topbar">
        <a className="brand" href="#/"><img className="brand-mark" src={mark} alt="" width={26} height={26} />FilmLab <span>Studio</span></a>
        {health?.version && <span className="version muted" title="FilmLab version">v{health.version}</span>}
        <nav className="nav">
          <a href="#/" className={route.page === "projects" || route.page === "project" ? "on" : ""}>Projects</a>
          <a href="#/library" className={route.page === "library" ? "on" : ""}>Library</a>
          <a href="#/create" className={route.page === "create" ? "on" : ""}>Create</a>
          <a href="#/settings" className={route.page === "settings" ? "on" : ""}>Settings</a>
          <a href="#/system" className={route.page === "system" ? "on" : ""}>Health &amp; backups</a>
          <a href="#/deleted" className={route.page === "deleted" ? "on" : ""}>Recently deleted</a>
          <a href="#/about" className={route.page === "about" ? "on" : ""}>About</a>
        </nav>
        <StatusBar health={health} stream={stream} />
        <GpuMeters sample={stream === "live" ? gpu : null} />
        <button className="ghost palette-open" title="Go to… (Ctrl K)" aria-label="Go to a project, shot or page"
          onClick={() => window.dispatchEvent(new Event("filmlab:palette"))}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" d="M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5 20 20" /></svg>
          <kbd>Ctrl K</kbd>
        </button>
        <button className={`ghost console-toggle ${consoleOpen ? "on" : ""}`} aria-pressed={consoleOpen}
          title="Console: what FilmLab is doing in the background (key: `)" aria-label="Console"
          onClick={toggleConsole}>&gt;_{unseen > 0 && <span className="console-badge">{unseen}</span>}</button>
        <NotifyToggle />
        <button className="ghost" onClick={() => api.logout().finally(onLogout)}>Sign out</button>
      </header>
      {consoleOpen && route.page !== "console" && (
        <ConsoleDrawer subscribe={subscribe} projectId={route.page === "project" ? route.id : null}
          onClose={() => setConsoleOpen(false)} />)}
      <Toasts />
      <UploadProgress />
      {health && !health.wangp.connected && <WanGPDownBanner />}
      <CommandPalette route={route} />
      {route.page === "project" ? <ProjectView key={route.id} projectId={route.id} tab={route.tab} subscribe={subscribe} />
        : route.page === "library" ? <LibraryPage assetId={route.id} subscribe={subscribe} />
          : route.page === "settings" ? <SettingsPage />
          : route.page === "deleted" ? <TrashPage />
          : route.page === "system" ? <SystemPage gpu={stream === "live" ? gpu : null} />
          : route.page === "selftest" ? <SelfTestPage />
          : route.page === "about" ? <AboutPage version={health?.version} />
          : route.page === "console" ? (
            <main className="page console-page"><h2>Console</h2>
              <ConsoleView subscribe={subscribe} projectId={null} full /></main>)
          : route.page === "create" ? <CreatePage subscribe={subscribe} kind={route.kind} creationId={route.creation} />
          : route.page === "sheet" ? <ContactSheet projectId={route.id} />
            : <Projects subscribe={subscribe} />}
      <footer className="footer muted">Rendering by <a href="https://github.com/deepbeepmeep/Wan2GP" target="_blank" rel="noopener noreferrer">WanGP</a> via its MCP interface · <a href="#/about">About FilmLab Studio</a></footer>
    </div>
  );
}

function Dot({ ok, label, title }: { ok: boolean | null; label: string; title?: string }) {
  return <span className="status-item" title={title ?? label}><i className={`dot ${ok === null ? "warn" : ok ? "ok" : "bad"}`} />
    <span className="status-text">{label}</span></span>;
}

function StatusBar({ health, stream }: { health: Health | null; stream: StreamState }) {
  const w = health?.wangp;
  const queue = !w?.connected ? "" : !w.queue_total ? " · queue empty"
    : ` · queue ${w.queue_total} (${w.queue_filmlab ?? 0} FilmLab / ${w.queue_other ?? 0} other), ${w.queue_running ?? 0} running`;
  return (
    <div className="statusbar" aria-live="polite">
      {/* Only when the live updates are down: the page then stops changing by itself, which is worth knowing. */}
      {stream !== "live" && <Dot ok={stream === "offline" ? false : null} label={`Events ${stream}`} />}
      <Dot ok={w ? w.connected : null} label={`WanGP${queue}`} title={w?.error ? `WanGP${queue}: ${w.error}` : undefined} />
    </div>
  );
}

/** A render finished: say so (a toast here, or a system notification when the cockpit isn't in view). Replayed
 *  events after a reconnect are old news, so only events newer than the page's first stream are announced. */
const loadedAt = Date.now();
function renderFinished(e: FilmLabEvent) {
  const p = e.payload as { status?: string; shot_title?: string; shot_position?: number; project_title?: string };
  if (p.status === "cancelled" || (e.timestamp && Date.parse(e.timestamp) < loadedAt)) return;
  const ok = e.type === "render.done";
  const shot = p.shot_title ? `${p.shot_position ? `${p.shot_position}. ` : ""}${p.shot_title}` : "A shot";
  void alertOwner({
    title: ok ? "Take ready" : "Render failed", kind: ok ? "ok" : "bad",
    body: `${shot}${p.project_title ? ` · ${p.project_title}` : ""}`,
    url: e.project_id ? `/#/p/${e.project_id}` : undefined,
  });
}

function NotifyToggle() {
  const state = useNotifyState();
  if (state === "unsupported") return null;
  const title = state === "on" ? "Notifications on: this device is told when a render finishes while FilmLab isn't in view"
    : state === "blocked" ? "Notifications are blocked for this site in the browser's settings"
      : "Tell this device when a render finishes while FilmLab isn't in view";
  return (
    <button className={`ghost bell ${state}`} title={title} aria-label={title} aria-pressed={state === "on"}
      disabled={state === "blocked"} onClick={() => void setNotify(state !== "on")}>
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20a2 2 0 0 0 4 0" />
        {state !== "on" && <path stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" d="M4 4l16 16" />}</svg>
    </button>
  );
}

function UploadProgress() {
  const list = useUploads();
  if (!list.length) return null;
  const size = (b: number) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
  return (
    <div className="uploads" role="status" aria-live="polite">
      {list.map((u) => {
        const pct = u.total ? Math.min(100, Math.round((u.loaded / u.total) * 100)) : 0;
        const sent = u.total > 0 && u.loaded >= u.total;
        return (
          <div key={u.id} className="upload">
            <div className="row"><span className="grow upload-name" title={u.name}>{u.name}</span>
              <span className="muted">{sent ? "Checking…" : `${pct}% · ${size(u.loaded)} of ${size(u.total)}`}</span></div>
            <div className={`upload-bar ${sent ? "checking" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={pct} aria-label={`Uploading ${u.name}`}><i style={{ width: `${pct}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}

function Toasts() {
  const toasts = useToasts();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} role="status">
          <button className="toast-body" onClick={() => { if (t.url) location.hash = t.url.replace(/^\/?/, ""); dismissToast(t.id); }}>
            <strong>{t.title}</strong><span className="muted">{t.body}</span>
          </button>
          {t.action && <button className="primary small" onClick={async () => {
            dismissToast(t.id);
            try { await t.action!.run(); } catch (e) { showToast({ title: "Couldn't do that", body: (e as Error).message, kind: "bad" }); }
          }}>{t.action.label}</button>}
          <button className="ghost small" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>✕</button>
        </div>
      ))}
    </div>
  );
}
