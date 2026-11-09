/** 📖 Storybook: the film as an illustrated book, as a picture book (one picture a page, narration below) or a
 *  comic (panels with caption boxes and speech bubbles). The preview follows the server's pages
 *  (backend/app/storybook.py); the export is drawn by the server (storybook_render.py) as a PDF, PNG pages and a CBZ.
 *  ✨ Write the story asks the director for the narration, which you edit and accept. */
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ask, dialogOpen, trapTab, useModalFrame } from "./Dialog";
import { api, type BookPanel, type BookPanelDoc, type StoryPlan, type Storybook } from "./api";
import type { Listener } from "./App";
import { DirectorLog, useDirectorStream } from "./DirectorStream";
import { CropBox } from "./Extract";
import type { Crop } from "./library-api";

type Subscribe = (l: Listener, r: () => void) => () => void;
type Patch = Parameters<typeof api.patchStorybook>[1];

const frame = (p: Pick<BookPanel, "take_id" | "t" | "crop">, size: "small" | "full" = "small") =>
  p.take_id ? api.bookFrameUrl(p.take_id, p.t, p.crop, size) : "";
const comicRows = (n: number, portrait: boolean): number[] => (portrait
  ? { 1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [3, 2], 6: [3, 3] } : { 1: [1], 2: [1, 1], 3: [1, 1, 1], 4: [1, 2, 1], 5: [1, 2, 2], 6: [2, 2, 2] }
)[n as 1] ?? [n];
const mb = (b: number) => (b / 1048576).toFixed(b > 10485760 ? 0 : 1);

export function StorybookPage({ projectId, subscribe }: { projectId: string; subscribe: Subscribe }) {
  const [book, setBook] = useState<Storybook | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);  // a shot id, or "cover"
  const [writing, setWriting] = useState(false);
  const timer = useRef<number>();

  const load = useCallback(() => {
    api.storybook(projectId).then(setBook).catch((e) => setError((e as Error).message));
  }, [projectId]);
  useEffect(() => { load(); api.project(projectId).then((p) => setTitle(p.title)).catch(() => undefined); }, [load, projectId]);
  useEffect(() => subscribe((e) => {
    if (e.project_id !== projectId) return;
    if (["storybook.updated", "storybook.export", "storybook.write", "shot.updated", "render.done"].includes(e.type)) {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(load, 200);
    }
  }, load), [subscribe, load, projectId]);

  const patch = async (p: Patch) => {
    setError(null);
    try { setBook(await api.patchStorybook(projectId, p)); } catch (e) { setError((e as Error).message); }
  };
  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
    load();
  };

  const head = (
    <div className="ws-head sb-head">
      <h1>{title || "Storybook"}</h1>
      <nav className="tabs" aria-label="Project views">
        <a href={`#/p/${projectId}`}>Storyboard</a>
        <a href={`#/p/${projectId}/edit`}>Edit &amp; export</a>
        <a href={`#/p/${projectId}/storybook`} className="on">Storybook</a>
      </nav>
    </div>
  );
  if (!book) return <main className="page sb-page">{head}{error ? <p className="error">{error}</p> : <p className="muted">Opening the book…</p>}</main>;

  const doc = book.doc;
  const byId = Object.fromEntries(book.panels.map((p) => [p.shot_id, p]));
  const out = book.panels.filter((p) => p.skip || !p.take_id);
  const exporting = book.exports.some((e) => e.status === "queued" || e.status === "running");
  const coverPanel = (doc.cover.shot_id && byId[doc.cover.shot_id]) || (book.pages[0] && byId[book.pages[0].shot_ids[0]]);
  const stale = book.panels.filter((p) => p.new_take_id && !p.skip).length;
  const w = book.writer;
  return (
    <main className="page sb-page">
      {head}
      <div className="sb-toolbar">
        <div className="sb-seg" role="radiogroup" aria-label="Layout">
          <button className={doc.layout === "picture" ? "on" : ""} aria-pressed={doc.layout === "picture"}
            onClick={() => patch({ layout: "picture", note: "picture book layout" })}>📖 Picture book</button>
          <button className={doc.layout === "comic" ? "on" : ""} aria-pressed={doc.layout === "comic"}
            onClick={() => patch({ layout: "comic", note: "comic layout" })}>💬 Comic</button>
        </div>
        {doc.layout === "comic" && <label className="small sb-inline">Panels a page
          <select value={doc.per_page} onChange={(e) => patch({ per_page: Number(e.target.value), note: "panels a page" })}>
            {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
        <label className="small check sb-inline"><input type="checkbox" className="switch" checked={doc.chapters}
          onChange={(e) => patch({ chapters: e.target.checked, note: "chapters" })} /> Chapters per scene</label>
        <span className="grow" />
        <button className={`ghost ${w?.status === "ready" ? "tp-ready" : ""}`} onClick={() => setWriting(true)}
          title="The director writes the narration for every page; you edit and accept it">
          ✨ {w?.status === "ready" ? "Review the story" : w?.status === "thinking" ? "Writing the story…" : "Write the story"}</button>
        <button className="primary" disabled={exporting || !book.pages.length}
          onClick={() => act(() => api.exportStorybook(projectId))}>{exporting ? "Exporting…" : "📕 Export book"}</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {stale > 0 && <p className="warn small">{stale} page{stale === 1 ? " has" : "s have"} a newer take of {stale === 1 ? "its" : "their"} shot.
        Open the page to use it (the book keeps the frame you chose until you do).</p>}

      <div className="sb-view">
        <section className="sb-pages" aria-label="Pages">
          <button className="sb-sheet sb-cover" onClick={() => setEditing("cover")} title="Edit the cover">
            {coverPanel && <img src={frame({ ...coverPanel, crop: doc.cover.crop ?? coverPanel.crop })} alt="" loading="lazy" />}
            <span className="sb-cover-text">
              <strong>{doc.cover.title || "Untitled"}</strong>
              {(doc.cover.subtitle || book.logline) && <em>{doc.cover.subtitle || book.logline}</em>}
              {doc.cover.author && <small>by {doc.cover.author}</small>}
            </span>
          </button>
          {book.pages.map((pg, i) => {
            const panels = pg.shot_ids.map((s) => byId[s]);
            return doc.layout === "picture"
              ? <PicturePage key={i} n={i + 2} chapter={pg.chapter} p={panels[0]} open={setEditing} />
              : <ComicPage key={i} n={i + 2} chapter={pg.chapter} panels={panels} portrait={book.portrait} open={setEditing} />;
          })}
          {!book.pages.length && <p className="muted">The book has no pages yet: it needs shots with takes. Render the storyboard first.</p>}
        </section>

        <aside className="sb-side">
          <div className="card">
            <h3>Exports</h3>
            {!book.exports.length ? <p className="muted small">A PDF, every page as a PNG (zipped) and a CBZ for comic readers.
              Pages are A4 at 150 dpi.</p> : <ul className="sb-exports">
              {book.exports.map((e) => (
                <li key={e.id}>
                  <div className="row small">
                    <span className="grow">{e.layout === "comic" ? "Comic" : "Picture book"} · {new Date(e.created_at).toLocaleString()}</span>
                    <button className="ghost small" aria-label="Delete this export" title="Delete"
                      onClick={() => act(async () => { if (await ask("Delete this export's files?")) await api.deleteBookExport(e.id); })}>✕</button>
                  </div>
                  {e.status === "done" ? <div className="row small wrap sb-files">
                    <a href={api.bookFileUrl(e.id, "pdf")} target="_blank" rel="noreferrer">PDF</a>
                    <a href={api.bookFileUrl(e.id, "pdf", true)}>⬇ PDF</a>
                    <a href={api.bookFileUrl(e.id, "zip", true)}>⬇ Pages (PNG)</a>
                    <a href={api.bookFileUrl(e.id, "cbz", true)}>⬇ CBZ</a>
                    <span className="muted">{e.pages} pages · {mb(e.bytes)} MB</span>
                  </div> : e.status === "failed" ? <p className="error small">{e.error || "failed"}</p>
                    : <p className="small tp-status"><span className="spinner" aria-hidden /> Drawing the pages…</p>}
                </li>))}
            </ul>}
          </div>
          {out.length > 0 && <div className="card">
            <h3>Not in the book ({out.length})</h3>
            <ul className="small sb-out">{out.map((p) => (
              <li key={p.shot_id} className="row"><span className="grow">{p.position}. {p.title}</span>
                {p.take_id ? <button className="ghost small" onClick={() => patch({ panels: { [p.shot_id]: { skip: false } }, note: `put shot ${p.position} back` })}>Put back</button>
                  : <span className="muted">no take yet</span>}</li>))}</ul>
          </div>}
          <details className="card">
            <summary><strong>History</strong></summary>
            <ol className="history small">{book.revisions.map((r, i) => (
              <li key={r.id} className="row"><span className="grow">{r.actor === "owner" ? "You" : r.actor}: {r.note || "edit"}
                <span className="muted"> · {new Date(r.created_at).toLocaleString()}</span></span>
                {i > 0 && <button className="ghost small" onClick={() => act(async () => {
                  if (await ask("Restore the book as it was after this change?")) setBook(await api.restoreStorybook(projectId, r.id));
                })}>Restore</button>}</li>))}</ol>
          </details>
        </aside>
      </div>

      {editing === "cover" && <CoverEditor book={book} onClose={() => setEditing(null)} save={patch} />}
      {editing && editing !== "cover" && byId[editing] &&
        <PanelEditor p={byId[editing]} layout={doc.layout} onClose={() => setEditing(null)} save={patch} />}
      {writing && <StoryWriter book={book} projectId={projectId} subscribe={subscribe} onClose={() => { setWriting(false); load(); }} onApplied={setBook} />}
    </main>
  );
}

function PicturePage({ p, chapter, n, open }: { p: BookPanel; chapter: string | null; n: number; open: (id: string) => void }) {
  const talk = p.dialogue ? p.lines : [];
  return (
    <button className="sb-sheet sb-picture" onClick={() => open(p.shot_id)} title={`Edit page ${n}: shot ${p.position}`}>
      {chapter && <span className="sb-chapter">{chapter}</span>}
      <span className="sb-pic"><img src={frame(p)} alt={p.title} loading="lazy" />{p.new_take_id && <span className="tag sb-new">new take</span>}</span>
      <span className="sb-text">
        {p.narration ? <span>{p.narration}</span> : !talk.length && <span className="sb-empty">+ add narration</span>}
        {talk.map((ln, i) => <em key={i}>{ln.speaker ? `${ln.speaker}: ` : ""}“{ln.text}”</em>)}
      </span>
      <span className="sb-num">{n}</span>
    </button>
  );
}

function ComicPage({ panels, chapter, portrait, n, open }: {
  panels: BookPanel[]; chapter: string | null; portrait: boolean; n: number; open: (id: string) => void;
}) {
  let k = 0;
  return (
    <div className="sb-sheet sb-comic">
      {chapter && <span className="sb-comic-head">{chapter}</span>}
      <div className="sb-rows">
        {comicRows(panels.length, portrait).map((cols, r) => (
          <div key={r} className="sb-row" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
            {Array.from({ length: cols }, () => panels[k++]).filter(Boolean).map((p) => (
              <button key={p.shot_id} className="sb-panel" onClick={() => open(p.shot_id)} title={`Edit panel: shot ${p.position}`}>
                <img src={frame(p)} alt={p.title} loading="lazy" />
                {p.narration && <span className="sb-caption">{p.narration}</span>}
                {p.new_take_id && <span className="tag sb-new">new take</span>}
                {p.dialogue && p.lines.length > 0 && <span className="sb-bubbles">{p.lines.slice(0, 2).map((ln, i) => (
                  <span key={i} className="sb-bubble"><b>{ln.speaker}</b>{ln.text}</span>))}</span>}
              </button>))}
          </div>
        ))}
      </div>
      <span className="sb-num">{n}</span>
    </div>
  );
}

/** The modal shell the book's editors share: Esc closes, Tab stays inside, phones get a full-screen sheet. */
function Modal({ title, onClose, children, foot, wide }: {
  title: string; onClose: () => void; children: ReactNode; foot: ReactNode; wide?: boolean;
}) {
  const backdrop = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  useModalFrame(backdrop);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (dialogOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); close.current(); } else trapTab(e, card.current);
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
  return createPortal(
    <div ref={backdrop} className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={card} className={`modal glass settings-modal sb-modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="sm-head"><h3>{title}</h3>
          <button type="button" className="ghost small" aria-label="Close" onClick={onClose}>✕</button></header>
        <div className="sm-body">{children}</div>
        <footer className="sm-foot">{foot}</footer>
      </div>
    </div>,
    document.body,
  );
}

/** Pick the moment of the take (scrub, step a frame), then optionally drag a crop on that frame. */
function FramePicker({ p, t, setT, crop, setCrop }: {
  p: BookPanel; t: number; setT: (t: number) => void; crop: Crop | null; setCrop: (c: Crop | null) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [cropping, setCropping] = useState(false);
  const step = (frames: number) => {
    const v = video.current;
    if (!v) return;
    v.pause();
    v.currentTime = Math.min(Math.max(0, v.currentTime + frames / p.fps), Math.max(0, p.duration_s - 0.05));
  };
  if (!p.media_id || !p.take_id) return <p className="muted small">This shot has no take yet.</p>;
  return (
    <div className="sb-frame">
      {!cropping ? <>
        <video ref={video} className="player" controls preload="auto" src={api.mediaUrl(p.media_id)} muted
          onLoadedMetadata={(e) => { e.currentTarget.currentTime = t; }}
          onSeeked={(e) => setT(e.currentTarget.currentTime)} onPause={(e) => setT(e.currentTarget.currentTime)} />
        <div className="row small wrap">
          <button type="button" className="ghost small" onClick={() => step(-1)}>◀ 1 frame</button>
          <button type="button" className="ghost small" onClick={() => step(1)}>1 frame ▶</button>
          <span className="muted">at {t.toFixed(2)} s</span>
          <span className="grow" />
          <button type="button" className="ghost small" onClick={() => { video.current?.pause(); setCropping(true); }}>
            ✂ {crop ? "Change crop" : "Crop"}</button>
        </div>
      </> : <>
        <CropBox src={api.bookFrameUrl(p.take_id, t)} crop={crop} onChange={setCrop} />
        <div className="row small">
          <span className="muted grow">Drag over the picture to keep only that part.</span>
          {crop && <button type="button" className="ghost small" onClick={() => setCrop(null)}>No crop</button>}
          <button type="button" className="ghost small" onClick={() => setCropping(false)}>Done</button>
        </div>
      </>}
    </div>
  );
}

function PanelEditor({ p, layout, onClose, save }: {
  p: BookPanel; layout: "picture" | "comic"; onClose: () => void; save: (p: Patch) => Promise<void>;
}) {
  const [t, setT] = useState(p.t);
  const [crop, setCrop] = useState<Crop | null>(p.crop);
  const [narration, setNarration] = useState(p.narration);
  const [dialogue, setDialogue] = useState(p.dialogue);
  const [skip, setSkip] = useState(p.skip);
  const [brk, setBrk] = useState(p.break_before);
  const [busy, setBusy] = useState(false);
  const commit = async (extra: Partial<BookPanelDoc> = {}) => {
    setBusy(true);
    await save({ panels: { [p.shot_id]: { t, crop, narration, dialogue, skip, break_before: brk, ...extra } },
      note: `${layout === "comic" ? "panel" : "page"} for shot ${p.position}` });
    onClose();
  };
  return (
    <Modal title={`Shot ${p.position} · ${p.title}`} onClose={onClose} wide foot={
      <div className="row modal-actions"><span className="grow" />
        <button type="button" className="ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy} onClick={() => commit()}>{busy ? "Saving…" : "Save"}</button></div>}>
      {p.new_take_id && <div className="row warn small sb-newtake">
        <span className="grow">A newer take of this shot exists.</span>
        <button type="button" className="ghost small" disabled={busy}
          onClick={() => commit({ take_id: p.new_take_id, t: null, crop: null })}>Use the new take</button></div>}
      <FramePicker p={p} t={t} setT={setT} crop={crop} setCrop={setCrop} />
      <label>{layout === "comic" ? "Caption" : "Narration"}
        <textarea rows={layout === "comic" ? 2 : 4} maxLength={600} value={narration} onChange={(e) => setNarration(e.target.value)}
          placeholder={layout === "comic" ? "A short caption box (leave empty for none)" : "What happens on this page"} /></label>
      {p.lines.length > 0 && <label className="check small"><input type="checkbox" className="switch" checked={dialogue}
        onChange={(e) => setDialogue(e.target.checked)} /> Show the dialogue <span className="muted">
          ({p.lines.map((l) => `${l.speaker ? l.speaker + ": " : ""}“${l.text}”`).join(" ")})</span></label>}
      {layout === "comic" && <label className="check small"><input type="checkbox" className="switch" checked={brk}
        onChange={(e) => setBrk(e.target.checked)} /> Start a new page with this panel</label>}
      <label className="check small"><input type="checkbox" className="switch" checked={skip} onChange={(e) => setSkip(e.target.checked)} />
        Leave this shot out of the book</label>
      <p className="muted small">{p.idea}</p>
    </Modal>
  );
}

function CoverEditor({ book, onClose, save }: { book: Storybook; onClose: () => void; save: (p: Patch) => Promise<void> }) {
  const c = book.doc.cover;
  const usable = book.panels.filter((p) => p.take_id);
  const [title, setTitle] = useState(c.title);
  const [subtitle, setSubtitle] = useState(c.subtitle);
  const [author, setAuthor] = useState(c.author);
  const [shot, setShot] = useState(c.shot_id ?? usable[0]?.shot_id ?? "");
  const [crop, setCrop] = useState<Crop | null>(c.crop);
  const [busy, setBusy] = useState(false);
  const p = usable.find((x) => x.shot_id === shot);
  return (
    <Modal title="Cover" onClose={onClose} foot={
      <div className="row modal-actions"><span className="grow" />
        <button type="button" className="ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy} onClick={async () => {
          setBusy(true);
          await save({ cover: { title, subtitle, author, shot_id: shot || null, crop }, note: "cover" });
          onClose();
        }}>{busy ? "Saving…" : "Save"}</button></div>}>
      <label>Title<input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Subtitle <span className="muted small">(empty: the film's logline)</span>
        <input value={subtitle} maxLength={200} onChange={(e) => setSubtitle(e.target.value)} placeholder={book.logline} /></label>
      <label>Author<input value={author} maxLength={120} onChange={(e) => setAuthor(e.target.value)} placeholder="Your name" /></label>
      <label>Cover picture
        <select value={shot} onChange={(e) => { setShot(e.target.value); setCrop(null); }}>
          {usable.map((x) => <option key={x.shot_id} value={x.shot_id}>{x.position}. {x.title}</option>)}</select></label>
      {p?.take_id && <>
        <CropBox src={api.bookFrameUrl(p.take_id, p.t, p.crop)} crop={crop} onChange={setCrop} />
        <p className="muted small">The page's frame. Drag to choose the part for the cover (it's tall: a portrait slice of a wide
          frame works best); without a crop, the middle is used.</p>
      </>}
    </Modal>
  );
}

function StoryWriter({ book, projectId, subscribe, onClose, onApplied }: {
  book: Storybook; projectId: string; subscribe: Subscribe; onClose: () => void; onApplied: (b: Storybook) => void;
}) {
  const [plan, setPlan] = useState<StoryPlan | null>(book.writer);
  const [note, setNote] = useState("");
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const turn = useRef<string | null>(book.writer?.turn_id ?? null);
  const filled = useRef(false);
  const take = (p: StoryPlan | null) => {
    setPlan(p);
    turn.current = p?.turn_id ?? turn.current;
    if (p?.status === "ready" && !filled.current) {
      filled.current = true;
      setTexts(Object.fromEntries(p.items.map((i) => [i.shot_id, i.text])));
      setTitle(p.title || book.doc.cover.title);
      setSubtitle(p.subtitle || book.doc.cover.subtitle);
    }
  };
  useEffect(() => take(book.writer), []);  // eslint-disable-line react-hooks/exhaustive-deps
  const { steps, reset } = useDirectorStream(subscribe, projectId, turn, (type) => {
    if (type === "storybook.write" || type === "resync") api.storybook(projectId).then((b) => take(b.writer)).catch(() => undefined);
  });
  const phase = !plan ? "setup" : plan.status;
  const count = book.panels.filter((p) => !p.skip).length;
  const comic = book.doc.layout === "comic";

  const start = async () => {
    setBusy(true); setError(null); reset(); filled.current = false;
    try { take((await api.writeStory(projectId, note)).plan); } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  const cancel = async () => {
    setBusy(true);
    if (phase === "thinking") await api.stopChat(projectId).catch(() => undefined);
    if (plan) await api.discardStory(projectId).catch(() => undefined);
    onClose();
  };
  const accept = async () => {
    setBusy(true); setError(null);
    try {
      onApplied(await api.applyStory(projectId, texts, title, subtitle));
      onClose();
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };
  return (
    <Modal title="✨ Write the story" onClose={onClose} wide foot={<>
      {error && <p className="error small" role="alert">{error}</p>}
      <div className="row modal-actions">
        {(phase === "ready" || phase === "failed") && <button type="button" className="ghost" disabled={busy} onClick={async () => {
          await api.discardStory(projectId).catch(() => undefined); setPlan(null); reset(); filled.current = false;
        }}>Write again</button>}
        <span className="grow" />
        <button type="button" className="ghost" onClick={phase === "setup" ? onClose : cancel}>{phase === "thinking" ? "Stop" : "Cancel"}</button>
        {phase === "setup" && <button type="button" className="primary" disabled={busy || !count} onClick={start}>
          {busy ? "Starting…" : `Write ${count} ${comic ? "caption" : "page"}${count === 1 ? "" : "s"}`}</button>}
        {phase === "ready" && <button type="button" className="primary" disabled={busy} onClick={accept}>{busy ? "Saving…" : "Accept"}</button>}
      </div></>}>
      {phase === "setup" && <>
        <p className="small muted">{comic
          ? "The director writes a short caption box for each panel: time, place, a thought, what happens between panels. The dialogue is already in the speech bubbles."
          : "The director writes a few sentences of narration for each page, so the pictures read as one story. The characters' own lines are printed under it."}
          {" "}It reads every shot's idea, scene and dialogue. Nothing changes until you accept.</p>
        <label className="small">Note for the director <span className="muted">(optional)</span>
          <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. for children, bedtime-story voice · hard-boiled narrator, first person" /></label>
      </>}
      {phase !== "setup" && <DirectorLog steps={steps} thinking={phase === "thinking"} what="writing the story" />}
      {phase === "failed" && <p className="error small" role="alert">{plan?.error || "It didn't finish."}</p>}
      {phase === "ready" && plan && <>
        {plan.summary && <p className="tp-summary small">“{plan.summary}”</p>}
        <div className="sm-grid">
          <label>Title<input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></label>
          <label>Subtitle<input value={subtitle} maxLength={200} onChange={(e) => setSubtitle(e.target.value)} /></label>
        </div>
        <ol className="sb-story">
          {plan.items.map((i) => (
            <li key={i.shot_id}>
              {i.take_id && <img src={api.bookFrameUrl(i.take_id, i.t, i.crop)} alt="" loading="lazy" />}
              <div>
                <span className="small muted">{i.position}. {i.title}</span>
                <textarea rows={comic ? 2 : 3} maxLength={600} value={texts[i.shot_id] ?? ""} aria-label={`Text for shot ${i.position}`}
                  onChange={(e) => setTexts((t) => ({ ...t, [i.shot_id]: e.target.value }))} />
                {i.lines.length > 0 && <span className="small muted sb-lines">{i.lines.map((l) => `${l.speaker ? l.speaker + ": " : ""}“${l.text}”`).join(" ")}</span>}
              </div>
            </li>))}
        </ol>
      </>}
    </Modal>
  );
}
