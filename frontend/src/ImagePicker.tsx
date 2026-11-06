import { useEffect, useState } from "react";
import { type Asset, type AssetDetail, KIND_LABEL, lib, type Suggestion } from "./library-api";

/** One chosen library image. Order matters: the first is <Picture 1>, the second <Picture 2>, … */
export type Pick = { variant_id: string; file_id: string; label: string };

/**
 * Picks images from the shared library: the chosen ones in order (reorder, remove) above a
 * browsable list of every asset; open an asset to see its looks and click images to add them.
 * With a shotId the list is ranked for that shot: suggestions first, each with why and the look to use.
 */
export function ImagePicker({ picks, onChange, max, openAssetId, hint, shotId }: {
  picks: Pick[]; onChange: (p: Pick[]) => void; max: number; openAssetId?: string; hint?: string; shotId?: string;
}) {
  const [q, setQ] = useState("");
  const [assets, setAssets] = useState<(Asset | Suggestion)[] | null>(null);
  const [open, setOpen] = useState<AssetDetail | null>(null);

  useEffect(() => {
    const load = shotId ? lib.suggest(shotId, q).then((r) => r.assets) : lib.search(q);
    const t = setTimeout(() => { load.then(setAssets).catch(() => setAssets([])); }, q ? 150 : 0);
    return () => clearTimeout(t);
  }, [q, shotId]);
  const suggested = (assets ?? []).filter((a): a is Suggestion => "score" in a && a.score > 0);
  const rest = (assets ?? []).filter((a) => !("score" in a) || a.score <= 0);
  const row = (a: Asset | Suggestion) => {
    const s = "score" in a && a.score > 0 ? a : null;
    const look = s?.look;
    return (
      <li key={a.id}>
        <button type="button" className={`asset-row ${open?.id === a.id ? "selected" : ""}`}
          onClick={() => lib.asset(a.id).then(setOpen)}>
          {a.cover_file_id ? <img src={lib.fileUrl(a.cover_file_id, true)} alt="" /> : <span className="noimg" />}
          <span><strong>{a.name}</strong> <span className="muted small">{KIND_LABEL[a.kind]}</span>
            {s && <span className="why small muted">{s.reasons.join(" · ")}{s.on_shot ? " · already on this shot" : ""}</span>}</span>
        </button>
        {look && !has(look.file_id) && picks.length < max && (
          <button type="button" className="ghost small use-look" title={`${look.name} v${look.version}: ${look.why}`}
            onClick={() => onChange([...picks, { variant_id: look.variant_id, file_id: look.file_id,
              label: `${a.name} · ${look.name} v${look.version}` }])}>+ {look.why}</button>
        )}
      </li>
    );
  };
  useEffect(() => { if (openAssetId) lib.asset(openAssetId).then(setOpen).catch(() => undefined); }, [openAssetId]);

  const has = (fid: string) => picks.some((p) => p.file_id === fid);
  const toggle = (p: Pick) => onChange(has(p.file_id) ? picks.filter((x) => x.file_id !== p.file_id)
    : picks.length < max ? [...picks, p] : picks);
  const move = (i: number, d: number) => { const n = [...picks]; const [x] = n.splice(i, 1); n.splice(i + d, 0, x); onChange(n); };

  return (
    <div className="image-picker">
      <ol className="picks">
        {picks.map((p, i) => (
          <li key={p.file_id} className="row">
            <img src={lib.fileUrl(p.file_id, true)} alt="" /><code>{`<Picture ${i + 1}>`}</code>
            <span className="grow small">{p.label}</span>
            <button type="button" className="ghost small" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
            <button type="button" className="ghost small" disabled={i === picks.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
            <button type="button" className="ghost small" onClick={() => onChange(picks.filter((_, j) => j !== i))}>Remove</button>
          </li>
        ))}
        {picks.length === 0 && <li className="muted small">Nothing chosen yet. Open an asset below and click its images.</li>}
      </ol>
      {hint && <p className="small muted">{hint}</p>}
      <input type="search" placeholder="Filter the library: character, location, prop, style" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="picker-browse">
        <ul className="asset-list compact">
          {suggested.length > 0 && <li className="list-head small muted">Suggested for this shot</li>}
          {suggested.map(row)}
          {suggested.length > 0 && rest.length > 0 && <li className="list-head small muted">Everything else</li>}
          {rest.map(row)}
          {assets && assets.length === 0 && (
            <li className="small muted">{q ? "No matches." : "The library is empty."} Add assets in the <a href="#/library">Library</a>.</li>
          )}
        </ul>
        <div className="picker-images">
          {!open && <p className="small muted">← Choose an asset to see its images.</p>}
          {open && open.variants.map((v) => {
            const images = v.files.filter((f) => f.kind === "image");
            return (
              <div key={v.id} className="small">
                <strong>{open.name}</strong> · {v.name} v{v.version}{v.locked && <span className="muted"> · locked</span>}
                <div className="pick">
                  {images.map((f) => {
                    const on = has(f.file_id);
                    return (
                      <button type="button" key={f.entry_id} className={`pick-item ${on ? "on" : ""}`} aria-pressed={on}
                        title={on ? "Click to remove" : f.caption || "Click to add"} disabled={!on && picks.length >= max}
                        onClick={() => toggle({ variant_id: v.id, file_id: f.file_id, label: `${open.name} · ${v.name} v${v.version}` })}>
                        <img src={lib.fileUrl(f.file_id, true)} alt={f.caption} />
                        {on && <span className="badge">{picks.findIndex((p) => p.file_id === f.file_id) + 1}</span>}
                      </button>
                    );
                  })}
                  {images.length === 0 && <span className="muted">no images in this look yet</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {picks.length >= max && <p className="small muted">That's the most this can take ({max}).</p>}
    </div>
  );
}
