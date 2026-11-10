/** Feedback for every button: a request started by a click marks that button busy (no double clicks), and after a
 *  moment it shows a spinner in the button's own colour, at its exact size, until the request and the refresh it
 *  triggers have finished. api.ts request() and uploads.ts sendFile() pass their promises through track().
 *  A button opts out with data-no-busy. */

const WINDOW = 1500;      // a request this soon after a click belongs to that click's button
const CHAIN = 400;        // a request this soon after the button's last one finished (e.g. the reload) extends it
const SHOW_AFTER = 150;   // quick actions finish before any spinner shows
const MIN_SHOWN = 350;    // once shown, long enough to read as feedback, not a flicker

interface State { pending: number; lastSettle: number; shownAt: number; timer?: number }
const states = new WeakMap<HTMLElement, State>();
let clicks: { el: HTMLElement; t: number }[] = [];
let active: HTMLElement | null = null;

if (typeof document !== "undefined") {
  document.addEventListener("click", (e) => {
    const el = (e.target as Element | null)?.closest?.("button") as HTMLButtonElement | null;
    if (!el || el.disabled || el.hasAttribute("data-no-busy")) return;
    clicks = [{ el, t: performance.now() }, ...clicks].slice(0, 2);
  }, true);
}

/** The button a request starting now belongs to, if any. */
function owner(): HTMLElement | null {
  const now = performance.now();
  const [last, before] = clicks;
  if (last && now - last.t < WINDOW) {
    if (last.el.isConnected) return last.el;
    // The click was a confirm dialog's button (gone now): the action belongs to the button that opened the dialog.
    if (before?.el.isConnected && now - before.t < 120_000) return before.el;
  }
  const s = active?.isConnected ? states.get(active) : undefined;
  if (active && s && (s.pending > 0 || now - s.lastSettle < CHAIN)) return active;
  return null;
}

export function track<T>(p: Promise<T>): Promise<T> {
  const el = owner();
  if (!el) return p;
  let s = states.get(el);
  if (!s) states.set(el, (s = { pending: 0, lastSettle: 0, shownAt: 0 }));
  const st = s;
  active = el;
  if (st.pending++ === 0) start(el, st);
  const done = () => {
    st.lastSettle = performance.now();
    if (--st.pending === 0) finish(el, st);
  };
  p.then(done, done);
  return p;
}

function start(el: HTMLElement, s: State) {
  window.clearTimeout(s.timer);
  el.classList.add("is-busy");
  el.setAttribute("aria-busy", "true");
  if (s.shownAt) return;
  s.timer = window.setTimeout(() => {
    if (s.pending === 0 || !el.isConnected) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--busy-ink", getComputedStyle(el).color);
    el.style.width = `${r.width}px`;   // the label is hidden while the spinner shows: keep the button's size
    el.style.height = `${r.height}px`;
    el.classList.add("busy-show");
    s.shownAt = performance.now();
  }, SHOW_AFTER);
}

function finish(el: HTMLElement, s: State) {
  window.clearTimeout(s.timer);
  s.timer = window.setTimeout(() => {  // a chained request (the reload after the action) keeps it busy
    if (s.pending > 0) return;
    const wait = s.shownAt ? Math.max(0, MIN_SHOWN - (performance.now() - s.shownAt)) : 0;
    s.timer = window.setTimeout(() => {
      if (s.pending > 0) return;
      el.classList.remove("is-busy", "busy-show");
      el.removeAttribute("aria-busy");
      el.style.removeProperty("width");
      el.style.removeProperty("height");
      el.style.removeProperty("--busy-ink");
      s.shownAt = 0;
      if (active === el) active = null;
    }, wait);
  }, CHAIN);
}
