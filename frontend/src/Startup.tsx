/** Settings > Startup & WanGP, and the banner when WanGP isn't running. FilmLab's Windows helper
 *  (scripts/filmlab-host.ps1) finds WanGP, starts it and applies these settings; the cockpit asks it through the
 *  server and shows what it reports. */
import { useCallback, useEffect, useState } from "react";
import { ask } from "./Dialog";
import { api, type HostSettings, type HostStatus } from "./api";
import { WanGPLink } from "./WanGPLink";

/** The helper's status, looked up again every few seconds while shown. */
function useHost(everyMs = 4000) {
  const [host, setHost] = useState<HostStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.host().then((h) => { setHost(h); setError(null); }).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, everyMs);
    return () => clearInterval(t);
  }, [load, everyMs]);
  return { host, error, load, setError };
}

const SETUP_HINT = "FilmLab's Windows helper isn't running, so FilmLab can't start WanGP for you. Start FilmLab with "
  + "filmlabctl start (or turn on “Start FilmLab when I sign in” below once it runs), or start WanGP yourself.";

export function StartupSection() {
  const { host, error, load, setError } = useHost();
  const [busy, setBusy] = useState(false);
  const [folder, setFolder] = useState<string | null>(null);  // typed, not saved yet
  const send = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); setTimeout(load, 1500); setTimeout(load, 6000); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const set = (s: HostSettings) => send(() => api.hostSettings(s));
  const w = host?.wangp;
  const st = host?.settings;
  const usable = !!host?.helper_running;

  return (
    <section>
      <h2>Startup &amp; WanGP</h2>
      <p className="muted small">FilmLab's helper on Windows finds WanGP by itself (the running WanGP, the folder you choose
        here, or the usual places) and can start it with FilmLab.</p>
      {error && <p className="error small" role="alert">{error}</p>}
      <div className="settings card startup">
        <div className="row">
          <span className={`dot ${host?.wangp_connected ? "ok" : w?.starting ? "warn" : "bad"}`} />
          <strong className="grow">{host?.wangp_connected ? "WanGP is connected"
            : w?.starting ? "Starting WanGP… (it can take a minute or two)" : w?.running ? "WanGP is starting up"
              : "WanGP isn't running"}</strong>
          {!host?.wangp_connected && usable && w?.found && !w.starting && (
            <button className="primary small" disabled={busy} onClick={() => send(api.startWanGP)}>Start WanGP</button>)}
          <WanGPLink label="Open WanGP ↗" title="WanGP's own page" />
        </div>
        {host && !usable && <p className="small warn">{SETUP_HINT}</p>}
        {usable && w && (
          <dl className="startup-facts small">
            <dt>Folder</dt><dd>{w.path ?? "not found"}{w.found_by && <span className="muted"> · {w.found_by}</span>}</dd>
            <dt>Started with</dt><dd>{w.launcher ?? (w.python ? `${w.python} wgp.py` : "—")}</dd>
            <dt>Ports</dt><dd>MCP {w.mcp_port ?? "?"} · web page {w.webui_port ?? "?"}
              {w.mcp_enabled === false && <span className="warn"> · WanGP's MCP server is off: turn on Configuration › Agents › Enable MCP with Web UI</span>}</dd>
          </dl>)}
        {usable && (
          <div className="row startup-folder">
            <input className="grow" placeholder={w?.path ?? "WanGP folder (the one with wgp.py), e.g. D:\\Wan2GP\\app"}
              value={folder ?? st?.wangp_path_chosen ?? ""} onChange={(e) => setFolder(e.target.value)}
              aria-label="WanGP folder" />
            <button className="ghost small" disabled={busy || folder === null}
              onClick={() => send(async () => { await api.hostSettings({ wangp_path: folder ?? "" }); setFolder(null); })}>
              {folder ? "Use this folder" : "Find it by itself"}</button>
            <button className="ghost small" disabled={busy} onClick={() => send(api.detectWanGP)}>Detect again</button>
          </div>)}
        {usable && st && <>
          <label className="check"><input type="checkbox" className="switch" checked={st.wangp_autostart} disabled={busy}
            onChange={(e) => set({ wangp_autostart: e.target.checked })} />
            Start WanGP automatically when FilmLab starts (if it isn't running)</label>
          <label className="check"><input type="checkbox" className="switch" checked={st.wangp_restart} disabled={busy}
            onChange={(e) => set({ wangp_restart: e.target.checked })} />
            Start WanGP again if it stops (after a minute without it)</label>
          <label className="check"><input type="checkbox" className="switch" checked={st.start_at_logon} disabled={busy}
            onChange={async (e) => {
              const on = e.target.checked;
              if (on && !await ask("Start FilmLab when you sign in to Windows? A Windows scheduled task “FilmLab Studio” "
                + "starts it (and WanGP, if set above) shortly after you sign in, so it comes back after a restart. "
                + "Turn this off to remove the task.", { confirm: "Turn on" })) return;
              set({ start_at_logon: on });
            }} />
            Start FilmLab when I sign in to Windows (survives a restart)</label>
        </>}
        {host?.note && usable && <p className="small muted">{host.note}</p>}
        {host?.error && <p className="small error">{host.error}</p>}
      </div>
    </section>
  );
}

/** Across the top while WanGP is down (once FilmLab has heard from the server): say so, and offer to start it. */
export function WanGPDownBanner() {
  const { host, error, load } = useHost(5000);
  const [asked, setAsked] = useState(false);
  if (!host || host.wangp_connected) return null;
  const w = host.wangp;
  const starting = asked || w?.starting || w?.running;
  return (
    <div className="wangp-banner" role="status">
      <strong>WanGP isn't running.</strong>{" "}
      <span className="muted">Renders and batches wait for it; everything else works.</span>{" "}
      {host.helper_running && w?.found ? (
        starting ? <span>Starting WanGP… it can take a minute or two.</span>
          : <button className="primary small" onClick={async () => { setAsked(true); try { await api.startWanGP(); } catch { setAsked(false); } setTimeout(load, 2000); }}>Start WanGP</button>
      ) : <a href="#/settings">Startup &amp; WanGP settings</a>}
      {error && <span className="error small"> {error}</span>}
    </div>
  );
}
