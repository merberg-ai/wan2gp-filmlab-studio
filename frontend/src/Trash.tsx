import { useCallback, useEffect, useState } from "react";
import { ask } from "./Dialog";
import { api, type Trash, type TrashKind } from "./api";
import { lib } from "./library-api";

const when = (ts: string) => new Date(ts).toLocaleString();

/** Recently deleted: restore, or delete forever (rows and the files nothing else uses). */
export function TrashPage() {
  const [trash, setTrash] = useState<Trash | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => { api.trash().then(setTrash).catch((e) => setError((e as Error).message)); }, []);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<string | void>) => {
    setError(null); setNotice(null);
    try { const n = await fn(); if (n) setNotice(n); } catch (e) { setError((e as Error).message); }
    load();
  };
  const restore = (kind: TrashKind, id: string, name: string) => act(async () => {
    await api.restore(kind, id);
    return `Restored ${name}.`;
  });
  const purge = (kind: TrashKind, id: string, name: string, extra = "") => act(async () => {
    if (!await ask(`Delete ${name} forever?${extra}\n\nThis removes it and its files from disk and can't be undone.`)) return;
    const r = await api.purge(kind, id);
    return `Deleted ${name} forever (${r.files_removed} file${r.files_removed === 1 ? "" : "s"} removed from disk).`;
  });
  const buttons = (kind: TrashKind, id: string, name: string, extra?: string) => (
    <div className="actions">
      <button className="ghost small" onClick={() => restore(kind, id, name)}>Restore</button>
      <button className="danger small" onClick={() => purge(kind, id, name, extra)}>Delete forever</button>
    </div>
  );

  if (!trash) return <main className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</main>;
  const empty = !trash.projects.length && !trash.shots.length && !trash.takes.length && !trash.assets.length && !trash.variants.length;
  return (
    <main className="page trash">
      <h2>Recently deleted</h2>
      <p className="muted small">Deleted projects, shots and library items stay here until you delete them forever.
        Restoring brings an item back as it was, except that a library item doesn't go back onto shots it was removed from.
        Nothing here touches WanGP's own gallery.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="ok-text small">{notice}</p>}
      {empty && <p className="muted">Nothing deleted.</p>}
      {trash.projects.length > 0 && <section><h3>Projects</h3><ul className="trash-list">
        {trash.projects.map((p) => (
          <li key={p.id} className="card row">
            <span className="grow"><strong>{p.title}</strong> <span className="muted small">· {p.shots} shot{p.shots === 1 ? "" : "s"} · deleted {when(p.deleted_at)}</span></span>
            {buttons("project", p.id, `project “${p.title}”`, " Its shots, takes, chat and media go with it.")}
          </li>
        ))}</ul></section>}
      {trash.shots.length > 0 && <section><h3>Shots</h3><ul className="trash-list">
        {trash.shots.map((s) => (
          <li key={s.id} className="card row">
            <span className="grow"><strong>{s.title}</strong> <span className="muted small">· {s.project_title}, shot {s.position} · {s.takes} take{s.takes === 1 ? "" : "s"} · deleted {when(s.deleted_at)}</span></span>
            {buttons("shot", s.id, `shot “${s.title}”`, s.takes ? ` Its ${s.takes} take(s) go with it.` : "")}
          </li>
        ))}</ul></section>}
      {trash.takes.length > 0 && <section><h3>Takes</h3><ul className="trash-list">
        {trash.takes.map((t) => (
          <li key={t.id} className="card row">
            {t.thumb_media_id ? <img className="thumb-sm" src={api.mediaUrl(t.thumb_media_id)} alt="" /> : <span className="noimg thumb-sm" />}
            <span className="grow"><strong>{t.title}</strong> <span className="muted small">· {t.project_title}, shot {t.position}
              {t.verdict ? ` · ${t.verdict}` : ""} · deleted {when(t.deleted_at)}</span></span>
            {buttons("take", t.id, `this take of “${t.title}”`)}
          </li>
        ))}</ul></section>}
      {trash.assets.length > 0 && <section><h3>Library items</h3><ul className="trash-list">
        {trash.assets.map((a) => (
          <li key={a.id} className="card row">
            {a.cover_file_id ? <img className="thumb-sm" src={lib.fileUrl(a.cover_file_id, true)} alt="" /> : <span className="noimg thumb-sm" />}
            <span className="grow"><strong>{a.name}</strong> <span className="muted small">· {a.kind} · deleted {when(a.deleted_at)}</span></span>
            {buttons("asset", a.id, `“${a.name}”`, " Its looks and generated candidates go with it; images another item also uses are kept.")}
          </li>
        ))}</ul></section>}
      {trash.variants.length > 0 && <section><h3>Looks</h3><ul className="trash-list">
        {trash.variants.map((v) => (
          <li key={v.id} className="card row">
            {v.cover_file_id ? <img className="thumb-sm" src={lib.fileUrl(v.cover_file_id, true)} alt="" /> : <span className="noimg thumb-sm" />}
            <span className="grow"><strong>{v.asset_name}</strong> · {v.name} v{v.version} <span className="muted small">· deleted {when(v.deleted_at)}</span></span>
            {buttons("variant", v.id, `the look “${v.name}” v${v.version} of ${v.asset_name}`)}
          </li>
        ))}</ul></section>}
    </main>
  );
}
