import { useState } from "react";
import { lib, type Tuning, type TuningField, type TuningValue, type TuningValues } from "./library-api";

/** Project settings → Generation: this project's own values for what WanGP's Advanced mode offers, per video model.
 *  Each model lists only the settings it has; an empty field follows the template. Saved on its own (not with the
 *  modal's Save), because WanGP checks the values first. */
export function GenerationSettings({ projectId, templates }: { projectId: string; templates: { use: string; template: string }[] }) {
  if (!templates.length) return <p className="muted small">Loading WanGP's models…</p>;
  return (
    <div className="tuning" onKeyDown={(e) => {  // Enter in a field mustn't submit (and close) the settings modal
      if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && (e.target as HTMLElement).tagName === "INPUT") e.preventDefault();
    }}>
      {templates.map((t) => <TemplateTuning key={t.template} projectId={projectId} use={t.use} template={t.template} />)}
    </div>
  );
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const show = (v: TuningValue | null | undefined) => v === null || v === undefined || v === "" ? "none" : Array.isArray(v) ? (v.join(", ") || "none") : String(v);

function TemplateTuning({ projectId, use, template }: { projectId: string; use: string; template: string }) {
  const [data, setData] = useState<Tuning | null>(null);
  const [draft, setDraft] = useState<TuningValues>({});  // only what differs from the template
  const [state, setState] = useState<{ busy?: boolean; error?: string; note?: string }>({});
  const [asked, setAsked] = useState(false);

  const load = () => {
    if (asked) return;
    setAsked(true);
    lib.tuning(projectId, template).then((d) => { setData(d); setDraft(d.values); })
      .catch((e) => { setState({ error: (e as Error).message }); setAsked(false); });
  };
  const set = (f: TuningField, value: TuningValue | null) => {
    setState({});
    setDraft((d) => {
      const next = { ...d };
      if (value === null || same(value, f.default)) delete next[f.key]; else next[f.key] = value;
      return next;
    });
  };
  const save = async (values: TuningValues) => {
    setState({ busy: true });
    try {
      const d = await lib.saveTuning(projectId, template, values);
      setData(d); setDraft(d.values);
      setState({ note: Object.keys(d.values).length ? "Saved. Renders started from now on use these; takes already made keep theirs."
        : "Back to the template's own settings." });
    } catch (e) { setState({ error: (e as Error).message }); }
  };

  const changed = data ? Object.keys(data.values).length : 0;
  const dirty = !!data && !same(draft, data.values);
  const groups = data ? [...new Set(data.fields.map((f) => f.group))] : [];
  return (
    <details className="tuning-model" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load(); }}>
      <summary><span className="grow"><strong>{template}</strong> <span className="muted small">· {use}</span></span>
        {data && changed > 0 && <span className="chip small">{changed} changed</span>}</summary>
      {!data && !state.error && <p className="muted small">Asking WanGP what this model offers…</p>}
      {data && groups.map((g) => (
        <fieldset key={g} className="tuning-group">
          <legend>{g}</legend>
          <div className="sm-grid">
            {data.fields.filter((f) => f.group === g).map((f) => (
              <Field key={f.key} f={f} value={draft[f.key]} set={(v) => set(f, v)} />
            ))}
          </div>
        </fieldset>
      ))}
      {data && data.fields.length === 0 && <p className="muted small">This model has no settings FilmLab can change.</p>}
      {state.error && <p className="error small" role="alert">{state.error}</p>}
      {state.note && <p className="muted small" role="status">{state.note}</p>}
      {data && data.fields.length > 0 && (
        <div className="row">
          <span className="muted small grow">{dirty ? "Unsaved generation settings" : "An empty field follows the template."}</span>
          {(changed > 0 || Object.keys(draft).length > 0) && (
            <button type="button" className="ghost small" disabled={state.busy} onClick={() => save({})}>Reset all</button>
          )}
          <button type="button" className="primary small" disabled={state.busy || !dirty} onClick={() => save(draft)}>
            {state.busy ? "Checking with WanGP…" : "Save generation settings"}</button>
        </div>
      )}
    </details>
  );
}

function Field({ f, value, set }: { f: TuningField; value: TuningValue | undefined; set: (v: TuningValue | null) => void }) {
  const over = value !== undefined;
  const hint = f.locked ?? [f.help, over ? `Template: ${show(f.default)}.` : ""].filter(Boolean).join(" ");
  const label = <span>{f.label}{over && <button type="button" className="link small" title="Back to the template's value"
    onClick={() => set(null)}> ↺</button>}</span>;
  let input;
  if (f.kind === "int" || f.kind === "number") {
    input = <input type="number" min={f.min ?? undefined} max={f.max ?? undefined} step={f.step ?? 1} disabled={!!f.locked}
      value={over ? String(value) : ""} placeholder={show(f.default)}
      onChange={(e) => set(e.target.value === "" || Number.isNaN(Number(e.target.value)) ? null : Number(e.target.value))} />;
  } else if (f.kind === "choice") {
    const choices = f.choices ?? [];
    const current = over ? value : f.default;
    const at = choices.findIndex((c) => String(c.value) === String(current ?? ""));
    input = <select value={at} disabled={!!f.locked} onChange={(e) => set(choices[Number(e.target.value)].value)}>
      {at < 0 && <option value={-1}>{show(current as TuningValue)}</option>}
      {choices.map((c, i) => <option key={i} value={i}>{c.label}</option>)}
    </select>;
  } else if (f.kind === "loras") {
    const picked = (over ? value : f.default) as string[] | null ?? [];
    input = <div className="tuning-loras">
      {(f.choices ?? []).map((c) => {
        const name = String(c.value);
        return <label key={name} className="check"><input type="checkbox" checked={picked.includes(name)}
          onChange={(e) => set(e.target.checked ? [...picked, name] : picked.filter((x) => x !== name))} />{name}</label>;
      })}
    </div>;
  } else {
    input = <input type="text" maxLength={1000} value={over ? String(value) : ""} placeholder={show(f.default)}
      onChange={(e) => set(e.target.value === "" ? null : e.target.value)} />;
  }
  const wide = f.kind === "loras" || f.kind === "text";
  return (
    <div className={`sm-field tuning-field${wide ? " sm-wide" : ""}${over ? " over" : ""}`}>
      {f.kind === "loras" ? <>{label}{input}</> : <label>{label}{input}</label>}
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}
