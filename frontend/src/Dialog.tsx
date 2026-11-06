import { type RefObject, useEffect, useRef, useState } from "react";

/** Glass confirm and prompt dialogs, replacing window.confirm / window.prompt.
 *  `await ask("Delete X? This can't be undone.")` resolves true or false; `await askText(...)` gives text or null.
 *  The first sentence (up to "?") becomes the title, the rest the body; the confirm button is named after the
 *  question's verb ("Delete", "Restore", "Generate"…), and destructive verbs get a red button. */

interface Request {
  id: number; title: string; body: string; confirm: string; danger: boolean;
  input?: { value: string; placeholder?: string };
  resolve: (v: boolean | string | null) => void;
}

export interface AskOptions { title?: string; confirm?: string; danger?: boolean }

let seq = 0;
let queue: Request[] = [];
const listeners = new Set<(q: Request[]) => void>();
const emit = () => listeners.forEach((l) => l(queue));

const VERBS = ["Delete", "Remove", "Restore", "Generate", "Move", "Stop", "Cancel", "Unlock", "Hold", "Turn", "Render",
  "Queue", "Re-plan", "Replace", "Also"];
const DANGER = /\b(delete|remove|forever|can't be undone|cancel)\b/i;

function split(message: string): { title: string; body: string } {
  const m = message.match(/^([^?\n]{1,160}\?)\s*([\s\S]*)$/);
  return m ? { title: m[1], body: m[2].trim() } : { title: message, body: "" };
}

function verb(title: string): string {
  const first = title.split(/[\s:?,]/)[0];
  if (first === "Also") return "Yes";
  if (first === "Re-plan") return "Re-plan";
  if (first === "Move" && /recently deleted/i.test(title)) return "Move to Recently deleted";
  return VERBS.includes(first) ? first : "Continue";
}

function push(r: Omit<Request, "id" | "resolve">): Promise<boolean | string | null> {
  return new Promise((resolve) => {
    queue = [...queue, { ...r, id: ++seq, resolve }];
    emit();
  });
}

export function ask(message: string, opts: AskOptions = {}): Promise<boolean> {
  const { title, body } = opts.title ? { title: opts.title, body: message } : split(message);
  return push({ title, body, confirm: opts.confirm ?? verb(title), danger: opts.danger ?? DANGER.test(title) })
    .then((v) => v === true);
}

export function askText(message: string, opts: AskOptions & { value?: string; placeholder?: string } = {}): Promise<string | null> {
  const { title, body } = opts.title ? { title: opts.title, body: message } : split(message);
  return push({ title, body, confirm: opts.confirm ?? "Save", danger: false,
                input: { value: opts.value ?? "", placeholder: opts.placeholder } })
    .then((v) => (typeof v === "string" ? v : null));
}

/** True while a confirm or prompt dialog is showing (other modals leave their keys to it). */
export function dialogOpen(): boolean { return queue.length > 0; }

let locks = 0;
/** For every modal: lock page scroll while it's open (counted, so stacked modals don't unlock early), and
 *  keep its backdrop on the part of the page that's actually on screen. On phones a zoomed page or the keyboard
 *  makes the visual viewport smaller than the fixed-position box, and a modal centred in that box can spill. */
export function useModalFrame(backdrop: RefObject<HTMLElement>) {
  useEffect(() => {
    const vv = window.visualViewport;
    const fit = () => {
      const b = backdrop.current;
      if (!b || !vv) return;
      Object.assign(b.style, { left: `${vv.offsetLeft}px`, top: `${vv.offsetTop}px`, width: `${vv.width}px`,
                               height: `${vv.height}px`, right: "auto", bottom: "auto" });
    };
    fit();
    vv?.addEventListener("resize", fit);
    vv?.addEventListener("scroll", fit);
    if (locks++ === 0) document.body.classList.add("modal-open");
    return () => {
      vv?.removeEventListener("resize", fit); vv?.removeEventListener("scroll", fit);
      if (--locks === 0) document.body.classList.remove("modal-open");
    };
  }, [backdrop]);
}

/** Keep Tab focus inside a modal. */
export function trapTab(e: KeyboardEvent, el: HTMLElement | null) {
  if (e.key !== "Tab" || !el) return;
  const items = [...el.querySelectorAll<HTMLElement>("button, input, select, textarea, summary, a[href]")]
    .filter((x) => !(x as HTMLButtonElement).disabled && x.offsetParent !== null);
  if (!items.length) return;
  const i = items.indexOf(document.activeElement as HTMLElement);
  const next = e.shiftKey ? (i <= 0 ? items.length - 1 : i - 1) : (i === items.length - 1 ? 0 : i + 1);
  e.preventDefault(); items[next]?.focus();
}

/** Mount once (in App). Shows queued dialogs one at a time. */
export function DialogHost() {
  const [q, setQ] = useState<Request[]>(queue);
  useEffect(() => { listeners.add(setQ); return () => { listeners.delete(setQ); }; }, []);
  const r = q[0];
  return r ? <DialogView key={r.id} r={r} /> : null;
}

function DialogView({ r }: { r: Request }) {
  const [value, setValue] = useState(r.input?.value ?? "");
  const card = useRef<HTMLDivElement>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const before = useRef<Element | null>(document.activeElement);
  useModalFrame(backdrop);

  const close = (v: boolean | string | null) => {
    queue = queue.filter((x) => x.id !== r.id);
    emit();
    r.resolve(v);
    (before.current as HTMLElement | null)?.focus?.();
  };
  const accept = () => close(r.input ? (value.trim() || null) : true);

  useEffect(() => {
    const el = card.current;
    const first = el?.querySelector<HTMLElement>(r.input ? "input" : r.danger ? "button.dismiss" : "button.confirm");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close(r.input ? null : false); }
      trapTab(e, el);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div ref={backdrop} className="modal-backdrop top" onMouseDown={(e) => { if (e.target === e.currentTarget) close(r.input ? null : false); }}>
      <div ref={card} className={`modal glass ${r.danger ? "danger" : ""}`} role="alertdialog" aria-modal="true"
           aria-labelledby={`dlg-t-${r.id}`} aria-describedby={r.body ? `dlg-b-${r.id}` : undefined}>
        <h3 id={`dlg-t-${r.id}`}>{r.title}</h3>
        {r.body && <p id={`dlg-b-${r.id}`} className="modal-body">{r.body}</p>}
        {r.input && <input value={value} placeholder={r.input.placeholder} onChange={(e) => setValue(e.target.value)}
                           onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); accept(); } }} />}
        <div className="modal-actions">
          <button className="ghost dismiss" onClick={() => close(r.input ? null : false)}>{r.input ? "Cancel" : "Go back"}</button>
          <button className={`confirm ${r.danger ? "danger" : "primary"}`} onClick={accept}
                  disabled={!!r.input && !value.trim()}>{r.confirm}</button>
        </div>
      </div>
    </div>
  );
}
