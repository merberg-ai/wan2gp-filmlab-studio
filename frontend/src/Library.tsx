import { type DragEvent, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ask } from "./Dialog";
import type { Listener } from "./App";
import { ImagePicker, type Pick } from "./ImagePicker";
import { CreationView, CreatorPanel } from "./Creator";
import {
  type Asset, type AssetDetail, type AssetKind, type Capability, type ImageJob, KIND_LABEL, KINDS, lib, type LibFile,
  templateLabel, type TemplateSettings, type Usage, usageWarning, type Variant,
} from "./library-api";
import { goShot } from "./Palette";
import { showToast } from "./notify";

type Subscribe = (l: Listener, resync: () => void) => () => void;
type Act = (fn: () => Promise<unknown>) => Promise<void>;
const MAX_IMAGE_REFS = 4;
const ACTIVE = new Set(["submitting", "queued", "running", "cancelling", "restored"]);

export function LibraryPage({ assetId, creationId, subscribe }: { assetId: string | null; creationId?: string | null; subscribe: Subscribe }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<AssetKind | "">("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    lib.search(q, kind).then((a) => { setAssets(a); setError(null); }).catch((e) => setError((e as Error).message));
  }, [q, kind]);
  useEffect(() => { const t = setTimeout(load, 150); return () => clearTimeout(t); }, [load]);
  useEffect(() => subscribe((e) => { if (e.type === "library.updated") load(); }, load), [subscribe, load]);

  return (
    <main className="library">
      <section className="panel lib-list">
        <div className="panel-head"><h2>Library</h2></div>
        <input type="search" placeholder="Search names, tags, captions" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="row small">
          {(["", ...KINDS] as const).map((k) => (
            <button key={k} className={`ghost small ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>
              {k ? KIND_LABEL[k] + "s" : "All"}
            </button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        <CreatorPanel subscribe={subscribe} current={creationId ?? null} />
        <NewAsset onCreated={(a) => { location.hash = `#/library/${a.id}`; load(); }} />
        <ul className="asset-list">
          {assets.map((a) => (
            <li key={a.id}>
              <a className={`asset-row ${a.id === assetId ? "selected" : ""}`} href={`#/library/${a.id}`}>
                {a.cover_file_id ? <img src={lib.fileUrl(a.cover_file_id, true)} alt="" loading="lazy" /> : <span className="noimg" />}
                <span><strong>{a.name}</strong><span className="block muted small">{KIND_LABEL[a.kind]}
                  {a.variant_count && a.variant_count > 1 ? ` · ${a.variant_count} looks` : ""}</span></span>
              </a>
            </li>
          ))}
          {assets.length === 0 && <li className="muted small">Nothing here yet.</li>}
        </ul>
      </section>
      {creationId ? <CreationView key={creationId} id={creationId} subscribe={subscribe} />
        : assetId ? <AssetView key={assetId} id={assetId} subscribe={subscribe} />
        : <section className="panel"><p className="muted">Pick an asset, or create one: ✨ Create with the director drafts a
          character, location, prop, style or music from your description and makes its reference images with WanGP; or add one
          yourself and upload its images.</p></section>}
    </main>
  );
}

function NewAsset({ onCreated }: { onCreated: (a: AssetDetail) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AssetKind>("character");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { onCreated(await lib.createAsset({ kind, name: name.trim() })); setName(""); setError(null); }
    catch (err) { setError((err as Error).message); }
  };
  return (
    <form className="row" onSubmit={submit}>
      <select value={kind} onChange={(e) => setKind(e.target.value as AssetKind)} aria-label="Kind">
        {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
      </select>
      <input className="grow" placeholder="New asset name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
      <button className="primary" disabled={!name.trim()}>Add</button>
      {error && <span className="error small block">{error}</span>}
    </form>
  );
}

/** Every shot that uses the asset, by project and look; and putting one look in all of them. */
function AssetUsage({ asset, act }: { asset: AssetDetail; act: Act }) {
  const [usage, setUsage] = useState<Usage[] | null>(null);
  const [to, setTo] = useState("");
  const looks = asset.variants.filter((v) => v.files.some((f) => f.kind === "image"));
  const sig = asset.variants.map((v) => `${v.id}:${v.files.length}`).join(",");
  const load = useCallback(() => { lib.assetUsage(asset.id).then(setUsage).catch(() => setUsage([])); }, [asset.id]);
  useEffect(load, [load, sig]);
  if (!usage) return null;
  const byProject = usage.reduce<Record<string, Usage[]>>((m, u) => { (m[u.project_title] ??= []).push(u); return m; }, {});
  const target = looks.find((v) => v.id === to);
  const moving = target ? usage.filter((u) => u.variant_id !== target.id) : [];
  return (
    <div className="card asset-usage">
      <h2>Used in {usage.length ? `${new Set(usage.map((u) => u.shot_id)).size} shot${usage.length === 1 ? "" : "s"}` : "no shots yet"}</h2>
      {Object.entries(byProject).map(([title, rows]) => (
        <div key={title} className="usage-project">
          <strong className="small">{title}</strong>
          <ul className="usage-list small">
            {rows.map((u) => (
              <li key={`${u.shot_id}:${u.variant_id}`}>
                <button className="link" onClick={() => goShot(u.project_id, u.shot_id)}>Shot {u.position} “{u.title}”</button>
                <span className="muted"> · {u.variant_name} v{u.version}{u.locked ? " · 🔒" : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {usage.length > 0 && looks.length > 1 && (
        <div className="row small swap-look">
          <span>Use one look everywhere:</span>
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Look to use">
            <option value="">Choose a look…</option>
            {looks.map((v) => <option key={v.id} value={v.id}>{v.name} v{v.version}</option>)}
          </select>
          <button className="primary small" disabled={!target || !moving.length} onClick={() => act(async () => {
            if (!target || !await ask(`Use “${target.name}” v${target.version} in ${moving.length} shot${moving.length === 1 ? "" : "s"}?`
              + " Each keeps the reference in the same place; locked and rendering shots are skipped, and every change shows in the shot's History.",
              { confirm: "Use this look" })) return;
            const r = await lib.swapLook(asset.id, target.id);
            showToast({ title: `“${target.name}” v${target.version} in ${r.changed.length} shot${r.changed.length === 1 ? "" : "s"}`,
              kind: r.skipped.length ? "bad" : "ok",
              body: r.skipped.length ? `Skipped ${r.skipped.map((s) => `${s.project_title} ${s.position}. ${s.title} (${s.reason})`).join("; ")}` : "" });
            setTo("");
            load();
          })}>{target ? `Use in ${moving.length} shot${moving.length === 1 ? "" : "s"}` : "Use"}</button>
        </div>
      )}
    </div>
  );
}

function AssetView({ id, subscribe }: { id: string; subscribe: Subscribe }) {
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [caps, setCaps] = useState<{ templates: Capability[]; settings: TemplateSettings } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const timer = useRef<number>();

  const load = useCallback(() => {
    lib.asset(id).then((a) => { setAsset(a); }).catch((e) => setError((e as Error).message));
  }, [id]);
  const soon = useCallback(() => { window.clearTimeout(timer.current); timer.current = window.setTimeout(load, 120); }, [load]);
  useEffect(() => { load(); lib.capabilities().then(setCaps).catch(() => setCaps(null)); }, [load]);
  useEffect(() => subscribe((e) => {
    if (e.type === "library.updated" || e.type.startsWith("image.")) {
      const p = e.payload as { asset_id?: string };
      if (!p.asset_id || p.asset_id === id) soon();
    }
  }, load), [subscribe, load, soon, id]);

  const act: Act = async (fn) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
    soon();
  };

  if (!asset) return <section className="panel">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</section>;
  const allImages = asset.variants.flatMap((v) => v.files.filter((f) => f.kind === "image").map((f) => ({ ...f, variant: v })));

  return (
    <section className="panel asset-view">
      <AssetFields asset={asset} act={act} />
      {asset.kind === "character" && <VoiceCard asset={asset} act={act} />}
      <div className="actions">
        <button className="ghost danger-text small" onClick={() => act(async () => {
          if (asset.images.jobs.some((j) => ACTIVE.has(j.status))) throw new Error("An image is being generated for it; cancel that first.");
          const usage = await lib.assetUsage(asset.id);
          if (!await ask(usageWarning(`${KIND_LABEL[asset.kind].toLowerCase()} “${asset.name}” with all its looks`, usage))) return;
          await lib.deleteAsset(asset.id);
          location.hash = "#/library";
        })}>Delete {KIND_LABEL[asset.kind].toLowerCase()}…</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="ok-text small">{notice}</p>}
      {asset.image_requests.length > 0 && (
        <div className="card approval-list">
          <h2>Director asks for images</h2>
          {asset.image_requests.map((r) => (
            <div key={r.id} className="row">
              <span className="grow small">“{r.prompt}”{r.note && <span className="muted"> — {r.note}</span>}
                <span className="muted"> · {r.count} candidate{r.count === 1 ? "" : "s"}
                  {r.input_file_ids.length ? ` · from ${r.input_file_ids.length} reference(s)` : ""}</span></span>
              <button className="ghost small" onClick={() => act(() => lib.rejectRequest(r.id))}>Reject</button>
              <button className="primary small" onClick={() => act(async () => {
                if (await ask("Generate these images now? This uses the GPU.")) await lib.approveRequest(r.id);
              })}>Generate</button>
            </div>
          ))}
        </div>
      )}
      <ImageJobs jobs={asset.images.jobs} act={act} />
      {asset.images.candidates.length > 0 && (
        <div className="card">
          <h2>Candidates · keep the ones you want</h2>
          <div className="candidates">
            {asset.images.candidates.map((c) => {
              const v = asset.variants.find((x) => x.id === c.variant_id);
              return (
                <figure key={c.id} className="candidate">
                  {c.media === "audio" ? <audio controls preload="none" src={lib.fileUrl(c.file_id)} />
                    : <img src={lib.fileUrl(c.file_id)} alt="generated candidate" />}
                  <figcaption className="small muted">{c.template}{c.seed != null ? ` · seed ${c.seed}` : ""}
                    {v ? ` · for ${v.name} v${v.version}` : ""}</figcaption>
                  <div className="actions">
                    <button className="ghost small" onClick={() => act(() => lib.discard(c.id))}>Discard</button>
                    <button className="primary small" disabled={v?.locked} title={v?.locked ? "That look is locked" : undefined}
                      onClick={() => act(() => lib.keep(c.id))}>Keep</button>
                  </div>
                </figure>
              );
            })}
          </div>
        </div>
      )}
      <AssetUsage asset={asset} act={act} />
      {asset.variants.map((v) => (
        <VariantCard key={v.id} variant={v} assetName={asset.name} onlyLook={asset.variants.length === 1} caps={caps} allImages={allImages} act={act} setNotice={setNotice} />
      ))}
      <NewVariant assetId={asset.id} act={act} />
    </section>
  );
}

function AssetFields({ asset, act }: { asset: AssetDetail; act: Act }) {
  const [f, setF] = useState({ name: asset.name, description: asset.description, notes: asset.notes, tags: asset.tags });
  useEffect(() => setF({ name: asset.name, description: asset.description, notes: asset.notes, tags: asset.tags }), [asset]);
  const dirty = f.name !== asset.name || f.description !== asset.description || f.notes !== asset.notes || f.tags !== asset.tags;
  return (
    <form className="editor" onSubmit={(e) => { e.preventDefault(); act(() => lib.updateAsset(asset.id, f)); }}>
      <div className="row">
        <span className="chip s-approved">{KIND_LABEL[asset.kind]}</span>
        <input className="grow title-input" value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} aria-label="Name" />
      </div>
      <textarea rows={2} placeholder="Description: who or what this is, what must stay consistent" value={f.description}
        onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="row">
        <input className="grow" placeholder="Tags (space separated)" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />
      </div>
      <textarea rows={2} placeholder="Notes" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />
      {dirty && <div className="actions"><button className="primary small">Save</button></div>}
    </form>
  );
}

/** A character's voice sample: the voice the video model gives them whenever they speak in a shot. */
function VoiceCard({ asset, act }: { asset: AssetDetail; act: Act }) {
  const input = useRef<HTMLInputElement>(null);
  const v = asset.voice;
  return (
    <div className="card voice-card">
      <div className="row">
        <strong className="grow">Voice</strong>
        <button className="ghost small" onClick={() => input.current?.click()}>{v ? "Replace sample…" : "Upload a sample…"}</button>
        {v && <button className="ghost small danger-text" onClick={() => act(async () => {
          if (await ask(`Remove ${asset.name}'s voice sample? The model will pick a voice for them again.`)) await lib.clearVoice(asset.id);
        })}>Remove</button>}
        <input ref={input} type="file" accept="audio/wav,audio/x-wav,audio/mpeg,audio/flac,.wav,.mp3,.flac" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) act(() => lib.setVoice(asset.id, f)); }} />
      </div>
      {v ? <>
        <audio controls preload="none" src={lib.fileUrl(v.file_id)} className="voice-player" />
        <p className="small muted">{v.duration_s?.toFixed(1)} s · {asset.name} speaks in this voice in every shot where they have a
          line (up to three voices per shot).</p>
      </> : (
        <p className="small muted">Upload 5-15 s of {asset.name}'s voice (WAV, MP3 or FLAC; 2-30 s accepted): one speaker, clear
          speech, no music or background noise. Without one, the model picks a voice each time, so it can change from shot to shot.</p>
      )}
    </div>
  );
}

function NewVariant({ assetId, act }: { assetId: string; act: Act }) {
  const [name, setName] = useState("");
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); act(async () => { await lib.createVariant(assetId, name.trim()); setName(""); }); }}>
      <input className="grow" placeholder="New look, e.g. night, wounded, winter coat" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      <button className="ghost" disabled={!name.trim()}>Add look</button>
    </form>
  );
}

function VariantCard({ variant, assetName, onlyLook, caps, allImages, act, setNotice }: {
  variant: Variant; assetName: string; onlyLook: boolean; caps: { templates: Capability[]; settings: TemplateSettings } | null;
  allImages: (LibFile & { variant: Variant })[]; act: Act; setNotice: (s: string | null) => void;
}) {
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const locked = variant.locked;

  const upload = async (files: FileList | File[]) => {
    setBusy(true);
    const notes: string[] = [];
    await act(async () => {
      for (const file of Array.from(files)) {
        const r = await lib.upload(variant.id, file, { source_note: "Uploaded by the owner" });
        if (r.already_attached) notes.push(`${file.name} is already on this look`);
        else if (r.deduplicated) notes.push(`${file.name} was already in the library; reused`);
      }
    });
    setNotice(notes.length ? notes.join(" · ") : null);
    setBusy(false);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (!locked && e.dataTransfer.files.length) upload(e.dataTransfer.files); };

  return (
    <div className={`card variant ${locked ? "locked" : ""}`} onDragOver={(e) => { e.preventDefault(); if (!locked) setDrag(true); }}
      onDragLeave={() => setDrag(false)} onDrop={onDrop}>
      <div className="row">
        <strong className="grow">{variant.name} <span className="muted">v{variant.version}</span>
          {locked && <span className="chip s-ready">locked</span>}</strong>
        {locked
          ? <button className="ghost small" onClick={() => act(() => lib.newVersion(variant.id))}
              title="Copy this look to a new, editable version; shots using this one keep it">New version</button>
          : <button className="ghost small" onClick={() => act(() => lib.lock(variant.id, true))}
              title="Lock what you approved: nothing can change it, and shots keep exactly these images">Lock</button>}
        {locked && <button className="ghost small" onClick={() => act(async () => {
          if (await ask("Unlock? Shots that use this look would then see later changes.")) await lib.lock(variant.id, false);
        })}>Unlock</button>}
        {!locked && !onlyLook && <button className="ghost small danger-text" title="Move this look to Recently deleted" onClick={() => act(async () => {
          const usage = await lib.variantUsage(variant.id);
          if (await ask(usageWarning(`the look “${variant.name}” v${variant.version} of ${assetName}`, usage))) await lib.deleteVariant(variant.id);
        })}>Delete look</button>}
      </div>
      {variant.description && <p className="small muted">{variant.description}</p>}
      <div className="files">
        {variant.files.map((f) => <FileTile key={f.entry_id} f={f} locked={locked} act={act} />)}
        {!locked && (
          <button className={`dropzone ${drag ? "over" : ""}`} onClick={() => input.current?.click()} disabled={busy}>
            {busy ? "Uploading…" : "Drop images here or click to upload"}
            <span className="block small muted">PNG, JPEG, WebP (or WAV/MP3/FLAC voice) · up to 25 MB each</span>
          </button>
        )}
        <input ref={input} type="file" hidden multiple accept="image/png,image/jpeg,image/webp,audio/wav,audio/mpeg,audio/flac"
          onChange={(e) => { if (e.target.files?.length) upload(e.target.files); e.target.value = ""; }} />
      </div>
      {!locked && caps && <GeneratePanel variant={variant} assetName={assetName} caps={caps} allImages={allImages} act={act} />}
    </div>
  );
}

function FileTile({ f, locked, act }: { f: LibFile; locked: boolean; act: Act }) {
  const [edit, setEdit] = useState(false);
  const [m, setM] = useState({ caption: f.caption, source_note: f.source_note, license_note: f.license_note });
  const prov = f.provenance as { seed?: number; model_type?: string; template?: string; inputs?: unknown[] } | null;
  return (
    <figure className="file">
      {f.kind === "image"
        ? <a href={lib.fileUrl(f.file_id)} target="_blank" rel="noreferrer"><img src={lib.fileUrl(f.file_id, true)} alt={f.caption || "reference"} loading="lazy" /></a>
        : <audio controls src={lib.fileUrl(f.file_id)} preload="none" />}
      <figcaption className="small">
        <span className={`tag src-${f.source}`}>{f.source}</span> {f.caption || <span className="muted">no caption</span>}
        <span className="block muted">{f.width ? `${f.width}×${f.height}` : f.duration_s ? `${f.duration_s.toFixed(1)} s` : ""}
          {prov?.seed != null ? ` · seed ${prov.seed}` : ""}{prov?.inputs?.length ? ` · from ${prov.inputs.length} ref(s)` : ""}</span>
        {f.source_note && <span className="block muted" title="Source">{f.source_note}</span>}
        {f.license_note && <span className="block muted" title="Licence">{f.license_note}</span>}
      </figcaption>
      {!locked && !edit && (
        <div className="actions">
          <button className="ghost small" onClick={() => setEdit(true)}>Edit</button>
          <button className="ghost small" onClick={() => act(() => lib.removeEntry(f.entry_id))}>Remove</button>
        </div>
      )}
      {edit && (
        <form className="editor" onSubmit={(e) => { e.preventDefault(); act(async () => { await lib.updateEntry(f.entry_id, m); setEdit(false); }); }}>
          <input placeholder="Caption" value={m.caption} onChange={(e) => setM({ ...m, caption: e.target.value })} />
          <input placeholder="Source" value={m.source_note} onChange={(e) => setM({ ...m, source_note: e.target.value })} />
          <input placeholder="Licence / rights" value={m.license_note} onChange={(e) => setM({ ...m, license_note: e.target.value })} />
          <div className="actions"><button type="button" className="ghost small" onClick={() => setEdit(false)}>Cancel</button>
            <button className="primary small">Save</button></div>
        </form>
      )}
    </figure>
  );
}

function GeneratePanel({ variant, assetName, caps, allImages, act }: {
  variant: Variant; assetName: string; caps: { templates: Capability[]; settings: TemplateSettings };
  allImages: (LibFile & { variant: Variant })[]; act: Act;
}) {
  // Start from this look's own images; a look without any borrows the asset's first image.
  const initial = (): Pick[] => {
    const own = allImages.filter((f) => f.variant.id === variant.id);
    return (own.length ? own : allImages.slice(0, 1)).slice(0, MAX_IMAGE_REFS)
      .map((f) => ({ variant_id: f.variant.id, file_id: f.file_id, label: `${assetName} · ${f.variant.name} v${f.variant.version}` }));
  };
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [picks, setPicks] = useState<Pick[]>([]);
  const [count, setCount] = useState(caps.settings.image_candidates);
  const [size, setSize] = useState("1024x1024");
  const [template, setTemplate] = useState("");
  const usesRefs = picks.length > 0;
  const options = useMemo(() => caps.templates.filter((c) => (c.task === "gen_image" || c.task === "edit_image")
    && (usesRefs ? c.refs !== "none" : !c.requires_refs)), [caps, usesRefs]);
  const effective = template && options.some((o) => o.template === template) ? template
    : usesRefs ? caps.settings.image_refs : caps.settings.image_text;

  if (!open) return <button className="ghost" onClick={() => { setPicks(initial()); setOpen(true); }}>Generate image with WanGP…</button>;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const refs = usesRefs ? ` from ${picks.length} reference image${picks.length === 1 ? "" : "s"}` : " from text only (no reference image)";
    act(async () => {
      if (!await ask(`Generate ${count} candidate${count === 1 ? "" : "s"} with ${effective}${refs}? This uses the GPU.`)) return;
      await lib.generate(variant.id, { prompt, template: effective, input_file_ids: picks.map((p) => p.file_id), count, resolution: size });
      setOpen(false); setPrompt(""); setPicks([]);
    });
  };
  return (
    <form className="editor generate" onSubmit={submit}>
      <strong className="small">New image for {assetName} · {variant.name} v{variant.version}</strong>
      <textarea rows={3} placeholder={usesRefs ? "What to make from the references, e.g. side view, same face and uniform, plain background."
        : "Describe the image: subject, framing, light, style."}
        value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <fieldset className="refs-field">
        <legend className="small">Reference images · keep identity (uses {caps.settings.image_refs} by default)</legend>
        <ImagePicker picks={picks} onChange={setPicks} max={MAX_IMAGE_REFS} openAssetId={variant.asset_id}
          hint="Any library image can be a reference: this asset's own, or another (e.g. a location)." />
        {!usesRefs && <p className="warn small">No reference chosen: this generates from text alone and won't keep {assetName}'s look.</p>}
      </fieldset>
      <div className="row">
        <label className="small grow">Model
          <select value={effective} onChange={(e) => setTemplate(e.target.value)}>
            {options.map((c) => <option key={c.task + c.template} value={c.template} disabled={!c.valid}>{templateLabel(c)}</option>)}
          </select>
        </label>
        <label className="small">Size
          <select value={size} onChange={(e) => setSize(e.target.value)}>
            {["1024x1024", "1280x720", "720x1280", "832x1216", "1216x832"].map((r) => <option key={r}>{r}</option>)}
          </select>
        </label>
        <label className="small">Candidates
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>{[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select>
        </label>
      </div>
      <div className="actions">
        <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
        <button className="primary" disabled={prompt.trim().length < 3}>Generate</button>
      </div>
    </form>
  );
}

export function ImageJobs({ jobs, act }: { jobs: ImageJob[]; act: Act }) {
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const failed = jobs.filter((j) => j.status === "failed" || j.status === "interrupted").slice(0, 3);
  if (!active.length && !failed.length) return null;
  return (
    <div className="card">
      {active.map((j) => (
        <div key={j.id} className="job">
          <div className="row"><strong className="grow small">Generating {j.label ? `${j.label}: ` : ""}{j.count} × {j.template}</strong>
            {j.status !== "cancelling" && <button className="ghost small" onClick={() => act(() => lib.cancelImageJob(j.id))}>Cancel</button>}</div>
          <div className="small muted">“{j.prompt}” · {j.phase ? `${j.phase.replace(/_/g, " ")}${j.total_steps ? ` ${j.current_step ?? 0}/${j.total_steps}` : ""}`
            : j.queue_ahead ? `waiting in WanGP's queue: ${j.queue_ahead} ahead` : j.status}</div>
          {j.total_steps ? <progress max={j.total_steps} value={j.current_step ?? 0} /> : <progress />}
          {j.stalled ? <p className="warn small">No progress from WanGP for 10 minutes.</p> : null}
        </div>
      ))}
      {failed.map((j) => <p key={j.id} className="error small">Generation failed ({j.template}): {j.error}</p>)}
    </div>
  );
}
