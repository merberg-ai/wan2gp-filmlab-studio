/** The project workspace's columns on wide screens: widths, hidden panels and focus, remembered in this browser. */
import { type PointerEvent as RPointerEvent, useCallback, useEffect, useRef, useState } from "react";

export const PANELS = ["storyboard", "director", "renders"] as const;
export type PanelName = typeof PANELS[number];
export const PANEL_LABEL: Record<PanelName, string> = { storyboard: "Storyboard", director: "Director", renders: "Renders" };
interface Layout { widths: [number, number, number]; hidden: PanelName[] }
const DEFAULT: Layout = { widths: [1, 1.2, 1], hidden: [] };
const KEY = "filmlab:layout";
const MIN_FR = 0.35;

function read(): Layout {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && Array.isArray(v.widths) && v.widths.length === 3 && Array.isArray(v.hidden)) return v;
  } catch { /* private window or bad value */ }
  return DEFAULT;
}

const WIDE = "(min-width: 1101px)";  // the three-column layout (styles.css); narrower screens stack or use tabs

export function useLayout() {
  const [layout, setLayout] = useState<Layout>(read);
  const [wide, setWide] = useState(() => typeof matchMedia === "function" && matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const m = matchMedia(WIDE);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  const current = useRef(layout);
  current.current = layout;
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(layout)); } catch { /* only for now */ } }, [layout]);
  const toggle = (p: PanelName) => setLayout((l) => {
    const hidden = l.hidden.includes(p) ? l.hidden.filter((x) => x !== p) : [...l.hidden, p];
    return hidden.length === 3 ? l : { ...l, hidden };  // one panel always shows
  });
  const focus = (p: PanelName) => setLayout((l) => ({ ...l, hidden: l.hidden.length === 2 && !l.hidden.includes(p) ? [] : PANELS.filter((x) => x !== p) }));
  const reset = () => setLayout(DEFAULT);
  /** Drag the handle between column i and i+1: move width from one to the other. */
  const drag = useCallback((i: 0 | 1, e: RPointerEvent<HTMLDivElement>) => {
    const grid = (e.currentTarget.parentElement as HTMLElement);
    const start = e.clientX;
    const startWidths = current.current.widths;
    const total = startWidths.reduce((a, b) => a + b, 0);
    const px = grid.getBoundingClientRect().width;
    e.currentTarget.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const d = ((ev.clientX - start) / px) * total;
      const w = [...startWidths] as [number, number, number];
      const a = Math.max(MIN_FR, startWidths[i] + d), b = Math.max(MIN_FR, startWidths[i + 1] - d);
      if (a === MIN_FR || b === MIN_FR) return;
      w[i] = a; w[i + 1] = b;
      setLayout((l) => ({ ...l, widths: w }));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, []);
  // grid columns: panel, handle, panel, handle, panel (a hidden panel is a thin rail; its handle stays put)
  const cols = PANELS.map((p, i) => (layout.hidden.includes(p) ? "40px" : `minmax(240px, ${layout.widths[i]}fr)`));
  const template = `${cols[0]} 10px ${cols[1]} 10px ${cols[2]}`;
  // hiding is for wide screens only: a phone shows each panel on its own tab
  return { layout, template, toggle, focus, reset, drag, hidden: (p: PanelName) => wide && layout.hidden.includes(p) };
}

/** A hidden panel's rail: its name, and a click shows it again. */
export function PanelRail({ name, onShow }: { name: PanelName; onShow: () => void }) {
  return (
    <section className={`panel rail ${name === "director" ? "chat" : name === "renders" ? "monitor" : "storyboard"}`}>
      <button className="rail-button" onClick={onShow} title={`Show ${PANEL_LABEL[name]}`} aria-label={`Show ${PANEL_LABEL[name]}`}>
        <span>{PANEL_LABEL[name]}</span>
      </button>
    </section>
  );
}
