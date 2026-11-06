import { useCallback, useEffect, useState } from "react";
import { ask } from "./Dialog";
import { api, type BackupRow, type SystemHealth } from "./api";
import { GpuMeters, type GpuSample } from "./GpuMeters";

const when = (ts?: string | null) => (ts ? new Date(ts).toLocaleString() : "");
const mb = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)} GB` : `${n.toFixed(n < 10 ? 2 : 0)} MB`);

/** Health, storage, backups and GPU sharing: the studio's housekeeping in one place. */
export function SystemPage({ gpu }: { gpu: GpuSample | null }) {
  const [h, setH] = useState<SystemHealth | null>(null);
  const [b, setB] = useState<{ backups: BackupRow[]; settings: { daily: boolean; keep: number; copy_dir: string }; dir: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.systemHealth().then(setH).catch((e) => setError((e as Error).message));
    api.backups().then(setB).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);
  const act = async (label: string, fn: () => Promise<string | void>) => {
    setBusy(label); setError(null); setNotice(null);
    try { const n = await fn(); if (n) setNotice(n); } catch (e) { setError((e as Error).message); }
    setBusy(null); load();
  };

  return (
    <main className="page system">
      <h2>Studio health</h2>
      <p className="small muted">Checking from another device? Open <a href="#/selftest">the LAN self-test</a> there.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="ok-text small">{notice}</p>}
      {!h ? <p className="muted">Checking…</p> : <>
        <div className={`card ${h.ok ? "ok-card" : "warn-card"}`}>
          <div className="row"><strong className="grow">{h.ok ? "✓ Everything checks out" : "⚠ Needs a look"}</strong>
            <span className="muted small">checked {when(h.at)}</span>
            <button className="ghost small" onClick={load}>Check again</button></div>
          {h.problems.map((p) => <p key={p} className="small warn">{p}</p>)}
        </div>
        <div className="grid-2">
          <section className="card small">
            <h3>Database and files</h3>
            <p>Database check: <strong>{h.database.integrity}</strong> · broken references: {h.database.foreign_key_problems}</p>
            <p>{h.files.referenced} files in use · {h.files.missing.length} missing · {h.files.orphans.length} not used by anything
              {h.files.orphan_bytes ? ` (${mb(h.files.orphan_bytes / 1e6)})` : ""}</p>
            {h.files.deleted_deliveries > 0 && <p>{h.files.deleted_deliveries} deleted deliver{h.files.deleted_deliveries === 1 ? "y" : "ies"} still
              on disk ({mb(h.files.deleted_delivery_bytes / 1e6)}){" "}
              <button className="ghost small danger-text" disabled={!!busy} onClick={() => act("deliv", async () => {
                if (!await ask("Remove the files of deliveries you deleted? They can't be downloaded or verified afterwards.")) return;
                const r = await api.removeDeletedDeliveries(); return `Removed ${r.removed} deleted deliveries, ${r.freed_mb} MB.`;
              })}>Remove their files</button></p>}
            {h.files.missing.length > 0 && <details><summary>Missing files</summary><ul>{h.files.missing.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
              <p className="muted">A backup may still have them: verify one below.</p></details>}
            {h.files.orphans.length > 0 && <details><summary>Unused files</summary><ul>{h.files.orphans.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
              <button className="ghost small danger-text" disabled={!!busy} onClick={() => act("orphans", async () => {
                if (!await ask(`Remove these ${h.files.orphans.length} file(s)? No project, take, delivery or library item uses them.`)) return;
                const r = await api.removeOrphans(h.files.orphans);
                return `Removed ${r.removed} file(s), ${r.freed_mb} MB.`;
              })}>Remove these files</button></details>}
          </section>
          <section className="card small">
            <h3>Storage</h3>
            <p><strong>{h.storage.free_gb} GB free</strong> of {h.storage.total_gb} GB on the FilmLab disk</p>
            <table className="matrix"><tbody>
              {([["Takes", h.storage.takes_mb], ["Deliveries", h.storage.deliveries_mb], ["Library", h.storage.library_mb],
                ["Caches (rebuilt on demand)", h.storage.caches_mb], ["Database", h.storage.database_mb], ["Backups", h.storage.backups_mb]] as [string, number][])
                .map(([k, v]) => <tr key={k}><td>{k}</td><td>{mb(v)}</td></tr>)}
            </tbody></table>
            <button className="ghost small" disabled={!!busy} onClick={() => act("caches", async () => {
              const r = await api.clearCaches(); return `Cleared ${r.freed_mb} MB of waveforms and filmstrips.`;
            })}>Clear caches</button>
            <p className="muted">Warn below {h.settings.warn_free_gb} GB free; renders are refused below {h.settings.min_free_gb} GB.</p>
          </section>
          <section className="card small">
            <h3>Jobs</h3>
            <p>{h.jobs.active} FilmLab job(s) active{h.jobs.checked ? ` · WanGP queue: ${h.jobs.wangp_tasks} task(s), ${h.jobs.filmlab_tasks} FilmLab's` : " · WanGP not checked"}</p>
            {h.jobs.error && <p className="warn">{h.jobs.error}</p>}
            {h.jobs.stale.length > 0 && <p className="warn">FilmLab thinks these are waiting but WanGP doesn't list them:
              {h.jobs.stale.map((j) => ` ${j.kind} ${j.id.slice(0, 8)} (${j.status})`).join(",")}. They settle when WanGP reports them, or cancel them on their shot.</p>}
            {h.jobs.unknown_in_wangp.length > 0 && <p className="warn">WanGP has FilmLab-tagged tasks FilmLab doesn't know:
              {h.jobs.unknown_in_wangp.map((t) => ` ${t.client_id} (${t.status})`).join(",")}. FilmLab never cancels these; check WanGP's queue.</p>}
          </section>
          <section className="card small">
            <h3>Startup and GPU</h3>
            <p>{h.startup.manual ? "FilmLab starts only when you run filmlabctl (nothing starts at boot)." : "⚠ A FilmLab unit is enabled at boot."}</p>
            <ul>{Object.entries(h.startup.units).map(([u, s]) => <li key={u}><code>{u}</code>: {s.active}{s.enabled_at_boot ? " · enabled at boot" : ""}</li>)}</ul>
            {gpu ? <>{gpu.gpu && <p>{gpu.gpu}, live:</p>}<GpuMeters sample={gpu} detailed /></>
              : h.gpu.vram && <p>{h.gpu.vram.gpu}: {h.gpu.vram.used_gb} of {h.gpu.vram.total_gb} GB in use</p>}
            <p>Ollama models on the GPU: {h.gpu.ollama_loaded.length ? h.gpu.ollama_loaded.map((m) => `${m.name} (${m.vram_gb} GB)`).join(", ") : "none"}</p>
            <label>Sharing the GPU with Ollama
              <select value={h.settings.gpu_share} onChange={(e) => act("share", async () => { await api.systemSettings({ gpu_share: e.target.value }); })}>
                <option value="off">Don't check</option>
                <option value="warn">Warn before a render (default)</option>
                <option value="wait">Wait until Ollama's local models are unloaded</option>
              </select></label>
            <p className="muted">FilmLab only reads Ollama's list of loaded models. It never unloads or stops another app's models.
              To pause FilmLab for other GPU work, use Hold GPU work in a project.</p>
          </section>
        </div>
      </>}

      <h2>Backups</h2>
      {!b ? <p className="muted">Loading…</p> : <>
        <div className="card small">
          <div className="row">
            <span className="grow">Backups go to <code>{b.dir}</code>{b.settings.copy_dir ? <> and are copied to <code>{b.settings.copy_dir}</code></> : ""}.
              Each holds the database and every media and library file; unchanged files are shared with the previous backup.
              Secrets are never included.</span>
            <button className="primary" disabled={!!busy} onClick={() => act("backup", async () => {
              const r = await api.createBackup(); return `Backed up: ${r.name}, ${r.files} files, ${mb(r.bytes / 1e6)} (${mb(r.new_bytes / 1e6)} new).`;
            })}>{busy === "backup" ? "Backing up…" : "Back up now"}</button>
          </div>
          <div className="row">
            <label className="check"><input type="checkbox" checked={b.settings.daily}
              onChange={(e) => act("set", async () => { await api.backupSettings({ daily: e.target.checked }); })} /> Daily backup while FilmLab runs</label>
            <label>Keep automatic backups
              <input type="number" min={1} max={100} value={b.settings.keep}
                onChange={(e) => act("set", async () => { await api.backupSettings({ keep: Number(e.target.value) }); })} /></label>
            <label className="grow">Also copy to (optional, e.g. /mnt/d/FilmLabBackups)
              <input defaultValue={b.settings.copy_dir} onBlur={(e) => e.target.value !== b.settings.copy_dir
                && act("set", async () => { await api.backupSettings({ copy_dir: e.target.value }); })} /></label>
          </div>
          <p className="muted">A database copy is also taken automatically before every update that changes its layout.
            Restoring replaces the studio, so it isn't a button here: with FilmLab running, open a terminal in the FilmLab
            distro and run <code>bash &lt;FilmLab folder&gt;/scripts/restore.sh &lt;backup name&gt;</code>. It keeps the current
            studio aside first.</p>
        </div>
        <ul className="trash-list">
          {b.backups.map((x) => (
            <li key={x.name} className="card small">
              <div className="row">
                <span className="grow"><strong>{x.name}</strong> <span className="muted">· {x.kind} · {when(x.created_at)} · FilmLab {x.version}
                  · {x.files} files, {mb((x.bytes ?? 0) / 1e6)} ({mb((x.new_bytes ?? 0) / 1e6)} new)
                  · {x.counts?.projects} projects, {x.counts?.takes} takes{x.note ? ` · ${x.note}` : ""}</span></span>
                <button className="ghost small" disabled={!!busy} onClick={() => act("verify", async () => {
                  const r = await api.verifyBackup(x.name, false); return r.ok ? `${x.name}: all ${r.checked_files} files present.` : `${x.name}: ${r.problems.length} problem(s).`;
                })}>Verify</button>
                <button className="ghost small" disabled={!!busy} title="Recompute every file's SHA-256" onClick={() => act("deep", async () => {
                  const r = await api.verifyBackup(x.name, true); return r.ok ? `${x.name}: ${r.hashed} files rehashed, all identical.` : `${x.name}: ${r.problems.length} problem(s).`;
                })}>Deep verify</button>
                <button className="ghost small" disabled={!!busy} title="Rebuild a delivery from this backup alone and compare it frame by frame" onClick={() => act("drill", async () => {
                  const r = await api.drillBackup(x.name); return `Restore drill: ${r.detail}`;
                })}>{busy === "drill" ? "Drilling…" : "Restore drill"}</button>
                <button className="ghost small danger-text" disabled={!!busy} onClick={() => act("del", async () => {
                  if (!await ask(`Delete backup ${x.name}? This can't be undone.`)) return;
                  await api.deleteBackup(x.name);
                })}>Delete</button>
              </div>
              {x.verified && <p className={x.verified.ok ? "ok-text" : "warn"}>Verified {when(x.verified.at)}{x.verified.deep ? " (deep)" : ""}:
                {x.verified.ok ? " ok" : ` ${x.verified.problems.slice(0, 3).join("; ")}`}</p>}
              {x.drill && <p className={x.drill.ok ? "ok-text" : "warn"}>Restore drill {when(x.drill.at)}: {x.drill.detail}</p>}
            </li>
          ))}
          {!b.backups.length && <li className="muted">No backups yet.</li>}
        </ul>
      </>}
    </main>
  );
}

/** The storyboard as a printable contact sheet, with a continuity checklist from the takes' inspections. */
export function ContactSheet({ projectId }: { projectId: string }) {
  const [s, setS] = useState<Awaited<ReturnType<typeof api.contactSheet>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.contactSheet(projectId).then(setS).catch((e) => setError((e as Error).message)); }, [projectId]);
  if (!s) return <main className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</main>;
  return (
    <main className="page contact-sheet">
      <div className="row no-print"><a className="muted" href={`#/p/${projectId}`}>← Storyboard</a><span className="grow" />
        <button className="ghost small" onClick={() => window.print()}>Print</button></div>
      {s.scenes.map((g) => (
        <section key={g.scene_id ?? "unsorted"}>
          <h2>{g.title}{g.time_place ? <span className="muted small"> · {g.time_place}</span> : null}</h2>
          <div className="sheet-grid">
            {g.shots.map((r) => (
              <figure key={r.shot_id} className={`card ${r.inspection?.verdict === "flagged" ? "flagged" : ""}`}>
                {r.take_id && r.sheet ? <img src={api.sheetUrl(r.take_id)} alt={`keyframes of shot ${r.position}`} loading="lazy" />
                  : <div className="noimg">{r.take_id ? "not inspected" : "no take yet"}</div>}
                <figcaption className="small"><strong>{r.position}. {r.title}</strong> · {r.seconds} s · into next: {r.transition || "cut"}
                  {r.inspection && <span className="block muted">{r.inspection.verdict === "ok" ? "✓" : "⚠"} {r.inspection.setting_seen}
                    {r.inspection.characters.length ? ` · ${r.inspection.characters.join(", ")}` : ""}</span>}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      ))}
      <section className="card">
        <h2>Continuity checklist</h2>
        {!s.checks.length ? <p className="muted small">Nothing to point out.</p> : <ul className="small">
          {s.checks.map((c, i) => <li key={i} className={c.level === "warn" ? "warn" : "muted"}><strong>{c.where}</strong>: {c.text}</li>)}</ul>}
        <p className="muted small">From the vision check of each shot's take: what it saw as the setting and who it found. Read it as hints.</p>
      </section>
    </main>
  );
}
