/** A director chat turn as it happens, for modals that ask the director for one thing (Plan transitions, Write the
 *  story): its words stream in, each tool call is a line, notes and errors show as they come. */
import { useEffect, useRef, useState } from "react";
import type { Listener } from "./App";

type Subscribe = (l: Listener, r: () => void) => () => void;
export type Step = { kind: "text" | "tool" | "note" | "error"; text: string };

const TOOL_LABEL: Record<string, string> = {
  get_edit: "Read the edit", get_project: "Read the storyboard", review_takes: "Looked at the takes",
  propose_transitions: "Proposed the transitions", propose_story: "Wrote the story",
};

/** Steps of the turn whose id is in turnRef; reset() clears them for a new turn. */
export function useDirectorStream(subscribe: Subscribe, projectId: string, turnRef: { current: string | null },
  onEvent?: (type: string) => void) {
  const [steps, setSteps] = useState<Step[]>([]);
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => subscribe((e) => {
    if (e.project_id !== projectId) return;
    cb.current?.(e.type);
    const p = e.payload as { turn_id?: string; text?: string; name?: string; ok?: boolean; role?: string; content?: string };
    if (!turnRef.current || p.turn_id !== turnRef.current) return;
    if (e.type === "chat.delta" && p.text) {
      setSteps((s) => s.length && s[s.length - 1].kind === "text"
        ? [...s.slice(0, -1), { kind: "text", text: s[s.length - 1].text + p.text }] : [...s, { kind: "text", text: p.text! }]);
    } else if (e.type === "chat.tool" && p.name) {
      setSteps((s) => [...s, { kind: "tool", text: `${p.ok ? "✓" : "✗"} ${TOOL_LABEL[p.name!] ?? p.name}` }]);
    } else if (e.type === "chat.message" && (p.role === "note" || p.role === "error") && p.content) {
      setSteps((s) => [...s, { kind: p.role === "error" ? "error" : "note", text: p.content! }]);
    }
  }, () => cb.current?.("resync")), [subscribe, projectId, turnRef]);
  return { steps, reset: () => setSteps([]) };
}

/** The live log box: a spinner line while thinking, then every step. */
export function DirectorLog({ steps, thinking, what }: { steps: Step[]; thinking: boolean; what: string }) {
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [steps]);
  if (!thinking && !steps.length) return null;
  return (
    <div className={`tp-think ${thinking ? "live" : ""}`} ref={log} aria-live="polite">
      {thinking && <p className="tp-status small"><span className="spinner" aria-hidden /> The director is {what}…</p>}
      {steps.length === 0 && thinking && <p className="muted small">Waiting for its first words…</p>}
      {steps.map((s, i) => <p key={i} className={`tp-step ${s.kind}`}>{s.text}</p>)}
    </div>
  );
}
