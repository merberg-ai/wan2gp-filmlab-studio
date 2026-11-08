/** A shot's sound: who says which exact words (or nobody speaks), the place's sounds, music, audio refinement.
 *  FilmLab writes it into the video model's own script format, so the words are spoken as written. */
import type { ShotSound } from "./api";

type Line = ShotSound["lines"][number];
export const EMPTY_SOUND: ShotSound = { lines: [], ambience: "", music: "", refine: false };
const WORDS_PER_S = 2.6;  // a comfortable speaking pace; longer lines get rushed or cut off

export function soundSummary(s: ShotSound | null | undefined): string | null {
  if (!s) return null;
  const said = s.lines.map((l) => `${l.speaker}: “${l.text}”`).join(" · ");
  return [said || "Nobody speaks", s.ambience, s.music && `music: ${s.music}`].filter(Boolean).join(" · ");
}

export function SoundEditor({ value, onChange, speakers, seconds }: {
  value: ShotSound | null; onChange: (s: ShotSound | null) => void; speakers: string[]; seconds: number;
}) {
  const s = value ?? EMPTY_SOUND;
  const set = (patch: Partial<ShotSound>) => onChange({ ...s, ...patch });
  const setLine = (i: number, patch: Partial<Line>) => set({ lines: s.lines.map((l, n) => (n === i ? { ...l, ...patch } : l)) });
  const words = s.lines.reduce((n, l) => n + l.text.split(/\s+/).filter(Boolean).length, 0);
  const tooLong = words > Math.max(3, seconds * WORDS_PER_S);
  return (
    <fieldset className="sound-field">
      <legend className="small">Sound</legend>
      {value === null && (
        <p className="small warn">Not set yet: the model may invent speech or mumble. Choose below, or let ✨ Enhance fill it in.</p>
      )}
      <div className="sound-mode" role="radiogroup" aria-label="Speech">
        <label className="check small"><input type="radio" name="speech" checked={value !== null && s.lines.length === 0}
          onChange={() => set({ lines: [] })} /> Nobody speaks</label>
        <label className="check small"><input type="radio" name="speech" checked={s.lines.length > 0}
          onChange={() => s.lines.length || set({ lines: [{ speaker: speakers[0] ?? "", text: "", delivery: "", language: "" }] })} /> Someone speaks</label>
      </div>
      {s.lines.length > 0 && (
        <ol className="sound-lines">
          {s.lines.map((l, i) => (
            <li key={i} className="sound-line">
              <input list="sound-speakers" className="speaker" placeholder="Who" aria-label={`Line ${i + 1}: who speaks`}
                value={l.speaker} maxLength={80} onChange={(e) => setLine(i, { speaker: e.target.value })} />
              <input className="grow" placeholder="The exact words" aria-label={`Line ${i + 1}: the exact words`}
                value={l.text} maxLength={300} onChange={(e) => setLine(i, { text: e.target.value })} />
              <input className="delivery" placeholder="How (e.g. whispering, amazed)" aria-label={`Line ${i + 1}: how it is said`}
                value={l.delivery} maxLength={120} onChange={(e) => setLine(i, { delivery: e.target.value })} />
              <button type="button" className="ghost small" aria-label={`Remove line ${i + 1}`}
                onClick={() => set({ lines: s.lines.filter((_, n) => n !== i) })}>✕</button>
            </li>
          ))}
          <datalist id="sound-speakers">{speakers.map((n) => <option key={n} value={n} />)}</datalist>
        </ol>
      )}
      {s.lines.length > 0 && s.lines.length < 6 && (
        <button type="button" className="ghost small" onClick={() => set({ lines: [...s.lines, {
          speaker: speakers.find((n) => n !== s.lines[s.lines.length - 1]?.speaker) ?? "", text: "", delivery: "", language: "" }] })}>
          + Line</button>
      )}
      {tooLong && <p className="small warn">{words} words in {seconds} s is fast: about {Math.floor(seconds * WORDS_PER_S)} fit comfortably.</p>}
      <label className="small">Sounds of the place and the action
        <input placeholder="e.g. servo whirr, crackling relays, low electrical hum" value={s.ambience} maxLength={600}
          onChange={(e) => set({ ambience: e.target.value })} /></label>
      <div className="row">
        <label className="small grow">Music <span className="muted">· the score the audience hears; empty for none</span>
          <input placeholder="e.g. tense low strings" value={s.music} maxLength={300} onChange={(e) => set({ music: e.target.value })} /></label>
        <label className="check small" title="An extra audio pass after the video (6 steps): cleaner speech and sound, a little slower">
          <input type="checkbox" className="switch" checked={s.refine} onChange={(e) => set({ refine: e.target.checked })} /> Refine audio</label>
      </div>
      <p className="small muted">Speakers are the shot's characters by their Library name; one with a voice sample in the Library
        keeps that voice. Sound applies to MiniMax H3 models exactly; other audio models get it as a plain description.</p>
    </fieldset>
  );
}
