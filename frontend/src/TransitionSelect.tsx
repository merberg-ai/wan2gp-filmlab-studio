import { TRANSITION_GROUPS, transitionLabel } from "./api";

/** Picks the transition into the next shot (rendered by the export; the in-browser preview shows a fade). */
export function TransitionSelect({ value, onChange }: { value: string; onChange: (t: string) => void }) {
  const known = TRANSITION_GROUPS.some((g) => g.items.some(([k]) => k === value));
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {!known && <option value={value}>{transitionLabel(value)}</option>}
      {TRANSITION_GROUPS.map((g) => (
        <optgroup key={g.group} label={g.group}>
          {g.items.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </optgroup>
      ))}
    </select>
  );
}
