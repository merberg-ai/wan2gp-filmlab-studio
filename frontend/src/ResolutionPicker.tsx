/** Picking a video size the way WanGP's own page does: a category (1080p, 720p, 540p, 480p, …) and then one of its
 *  sizes, each with its aspect ratio and orientation. The sizes are WanGP's own presets (shared/resolutions.py,
 *  without 4K, which FilmLab doesn't render); a size that isn't one of them shows as Custom. */
import { useEffect, useState } from "react";

// [width x height, aspect ratio], grouped by pixel count like WanGP
const PRESETS: [string, string][] = [
  ["1920x1088", "16:9"], ["1088x1920", "9:16"], ["1440x1440", "1:1"], ["1536x1024", "3:2"], ["1024x1536", "2:3"],
  ["1920x832", "21:9"], ["832x1920", "9:21"], ["2048x768", "8:3"], ["1024x1792", "4:7"], ["1088x1088", "1:1"],
  ["1024x1024", "1:1"], ["1280x720", "16:9"], ["720x1280", "9:16"], ["1600x400", "4:1"], ["1280x544", "21:9"],
  ["544x1280", "9:21"], ["1104x832", "4:3"], ["832x1104", "3:4"], ["960x960", "1:1"],
  ["960x544", "16:9"], ["544x960", "9:16"],
  ["832x624", "4:3"], ["624x832", "3:4"], ["720x720", "1:1"], ["832x480", "16:9"], ["480x832", "9:16"],
  ["672x384", "16:9"], ["384x672", "9:16"], ["512x512", "1:1"],
  ["576x320", "16:9"], ["320x576", "9:16"], ["448x448", "1:1"],
  ["448x256", "7:4"], ["256x448", "4:7"], ["320x320", "1:1"],
];
// the largest pixel count in each category (WanGP's thresholds)
const GROUPS: [string, string, number][] = [
  ["256p", "256p", 448 * 256], ["320p", "320p", 448 * 448], ["384p", "384p", 512 * 512],
  ["480p", "480p · SD", 832 * 624], ["540p", "540p", 960 * 544], ["720p", "720p · HD", 1024 * 1024],
  ["1080p", "1080p · Full HD", 1920 * 1088],
];
const CATEGORY_ORDER = ["1080p", "720p", "540p", "480p", "384p", "320p", "256p"];
export const CUSTOM = "custom";

const pixels = (r: string) => { const [w, h] = r.split("x").map(Number); return (w || 0) * (h || 0); };
export function categoryOf(r: string): string {
  if (!PRESETS.some(([p]) => p === r)) return CUSTOM;
  const n = pixels(r);
  return (GROUPS.find(([, , max]) => n <= max) ?? GROUPS[GROUPS.length - 1])[0];
}
function shape(r: string) {
  const [w, h] = r.split("x").map(Number);
  if (!w || !h) return "";
  if (w === h) return "square";
  const ratio = Math.max(w, h) / Math.min(w, h);
  return w > h ? (ratio >= 2.2 ? "wide" : "landscape") : (ratio >= 2.2 ? "tall" : "portrait");
}
/** e.g. "1280×720 · 16:9 landscape" */
export function sizeLabel(r: string) {
  const preset = PRESETS.find(([p]) => p === r);
  const s = shape(r);
  return `${r.replace("x", "×")}${preset ? ` · ${preset[1]}` : ""}${s ? ` ${s}` : ""}`;
}
const sizesIn = (cat: string) => PRESETS.map(([r]) => r).filter((r) => categoryOf(r) === cat);
const label = (cat: string) => (cat === CUSTOM ? "Custom" : GROUPS.find(([g]) => g === cat)?.[1] ?? cat);

/** Category + size. keep: an extra first choice that means "no change" (bulk edits). */
export function ResolutionPicker({ value, onChange, keep, disabled }: {
  value: string; onChange: (r: string) => void; keep?: { value: string; label: string }; disabled?: boolean;
}) {
  const keeping = !!keep && value === keep.value;
  const [cat, setCat] = useState(() => (keeping ? keep!.value : categoryOf(value)));
  useEffect(() => { setCat(keeping ? keep!.value : categoryOf(value)); }, [value]);  // eslint-disable-line react-hooks/exhaustive-deps
  const cats = [...CATEGORY_ORDER, ...(cat === CUSTOM ? [CUSTOM] : [])];
  const pickCategory = (c: string) => {
    setCat(c);
    if (keep && c === keep.value) { onChange(keep.value); return; }
    const sizes = sizesIn(c);
    // stay in the same shape (landscape stays landscape) where the category has one
    const same = sizes.find((r) => shape(r) === shape(value) && PRESETS.find(([p]) => p === r)?.[1] === PRESETS.find(([p]) => p === value)?.[1])
      ?? sizes.find((r) => shape(r) === shape(value)) ?? sizes[0];
    if (same) onChange(same);
  };
  return (
    <span className="res-picker">
      <select value={cat} disabled={disabled} aria-label="Size category" onChange={(e) => pickCategory(e.target.value)}>
        {keep && <option value={keep.value}>{keep.label}</option>}
        {cats.map((c) => <option key={c} value={c}>{label(c)}</option>)}
      </select>
      {!keeping && (
        <select value={value} disabled={disabled} aria-label="Size" onChange={(e) => onChange(e.target.value)}>
          {cat === CUSTOM && <option value={value}>{sizeLabel(value)}</option>}
          {sizesIn(cat).map((r) => <option key={r} value={r}>{sizeLabel(r)}</option>)}
        </select>)}
    </span>
  );
}
