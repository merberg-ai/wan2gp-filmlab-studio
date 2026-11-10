import { useCallback, useEffect, useState } from "react";
import { StartupSection } from "./Startup";
import { ask } from "./Dialog";
import { type Capabilities, type Capability, lib, type Preset, templateLabel, type TemplateSettings } from "./library-api";
import { api, type DirectorModel, type DirectorOverview, type DirectorSettings as DS, type InspectionSettings as InspectionSettingsT } from "./api";
import { AUTONOMY } from "./Autonomy";
import { Markdown } from "./Markdown";

const SETTINGS: { key: keyof TemplateSettings; label: string; hint: string; fits: (c: Capability) => boolean }[] = [
  { key: "video_refs", label: "Video, shots with references", hint: "Used when a shot has reference images",
    fits: (c) => (c.task === "gen_video" || c.task === "gen_video_with_refs") && c.refs !== "none" },
  { key: "video_plain", label: "Video, shots without references", hint: "Text (and start/end frame) video",
    fits: (c) => c.task === "gen_video" },
  { key: "image_text", label: "Image, from text", hint: "New reference images from a prompt", fits: (c) => c.task === "gen_image" },
  { key: "image_refs", label: "Image, from references", hint: "New angles or looks that keep identity",
    fits: (c) => (c.task === "gen_image" || c.task === "edit_image") && c.refs !== "none" },
  { key: "voice_design", label: "Voice, from a description", hint: "The Creator designs a character's voice with it",
    fits: (c) => c.task === "gen_speech_from_description" },
  { key: "music", label: "Music", hint: "The Creator makes sound assets (music) with it", fits: (c) => c.task === "gen_song" },
  { key: "voice_clone", label: "Voice, cloned from a sample", hint: "The Creator clones a character's voice from your recording with it, and says test lines",
    fits: (c) => c.task === "gen_speech_from_sample" },
];
const TASK_LABEL: Record<Capability["task"], string> = {
  gen_video: "Video", gen_video_with_refs: "Video with references", gen_image: "Image", edit_image: "Image editing",
  gen_speech_from_description: "Voice design", gen_song: "Music", gen_speech_from_sample: "Voice cloning",
  gen_video_with_speech: "Video from a song or speech",
};

export function SettingsPage() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    lib.capabilities(refresh).then((c) => { setCaps(c); setError(null); })
      .catch((e) => setError((e as Error).message)).finally(() => setBusy(false));
  }, []);
  useEffect(() => load(), [load]);

  const save = async (patch: Partial<TemplateSettings>) => {
    try {
      const s = await lib.updateSettings(patch);
      setCaps((c) => c && { ...c, settings: s }); setError(null);
    } catch (e) { setError((e as Error).message); }
  };

  // Startup & WanGP comes first and shows even while WanGP is down (that's when it's needed)
  if (!caps) return (
    <main className="page"><StartupSection />
      {error ? <p className="error">{error}</p> : <p className="muted">Checking WanGP's templates…</p>}</main>);
  return (
    <main className="page">
      <StartupSection />
      <section>
        <h2>Default models</h2>
        <p className="muted small">Shots and image generations that don't name a template use these. A project can
          override the two video defaults; a shot can still pick its own template.</p>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="settings card">
          {SETTINGS.map(({ key, label, hint, fits }) => {
            const options = caps.templates.filter(fits);
            return (
              <label key={key}>
                <span>{label} <span className="muted small">· {hint}</span></span>
                <select value={caps.settings[key] as string} onChange={(e) => save({ [key]: e.target.value })}>
                  {!options.some((o) => o.template === caps.settings[key]) && <option value={caps.settings[key] as string}>{caps.settings[key]} (not installed)</option>}
                  {options.map((c) => <option key={c.task + c.template} value={c.template} disabled={!c.valid}>{templateLabel(c)}</option>)}
                </select>
              </label>
            );
          })}
          <label>
            <span>Candidates per image generation <span className="muted small">· each one is a full generation</span></span>
            <select value={caps.settings.image_candidates} onChange={(e) => save({ image_candidates: Number(e.target.value) })}>
              {[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}
            </select>
          </label>
        </div>
      </section>
      <DirectorSettings />
      <PresetSettings />
      <InspectionSettings />
      <section>
        <div className="panel-head">
          <h2>What WanGP can do here</h2>
          <button className="ghost small" disabled={busy} onClick={() => load(true)}>{busy ? "Checking…" : "Check again"}</button>
        </div>
        <p className="muted small">Read from WanGP's templates and a CPU-only validation of each (nothing is generated).
          {caps.built_at && ` Last checked ${new Date(caps.built_at).toLocaleString()}.`}</p>
        <table className="matrix">
          <thead><tr><th>Template</th><th>Kind</th><th>References</th><th>Audio</th><th>State</th></tr></thead>
          <tbody>
            {caps.templates.map((c) => (
              <tr key={c.task + c.template} className={!c.valid ? "bad" : c.installed === false ? "warn" : "ok"}>
                <td>{c.template}{c.wangp_default && <span className="muted small"> · WanGP default</span>}</td>
                <td>{TASK_LABEL[c.task]}</td>
                <td>{c.refs === "none" ? "—" : c.refs}{c.requires_refs ? " (required)" : ""}</td>
                <td>{c.audio ? "yes" : "—"}</td>
                <td>{!c.valid ? <span className="error">Unusable: {c.reason}</span>
                  : c.installed === false ? <span className="warn">Valid · files download on first use</span>
                    : <span className="ok-text">Ready</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}

/** Take inspection: on or off, and where the vision check's four keyframes go (off, this PC, or Ollama's cloud). */
function InspectionSettings() {
  const [s, setS] = useState<InspectionSettingsT | null>(null);
  const [mode, setMode] = useState<InspectionSettingsT["vision"]>("off");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const show = (x: InspectionSettingsT) => { setS(x); setMode(x.vision); setModel(x.vision_model); setError(null); };
  useEffect(() => { api.autonomySettings().then(show).catch((e) => setError((e as Error).message)); }, []);
  const save = async (patch: Partial<Pick<InspectionSettingsT, "auto_inspect" | "vision_model" | "vision">>) => {
    if (patch.vision === "cloud" && s?.vision !== "cloud" && !await ask(
      `Send four frames of every new take to ${patch.vision_model ?? model} on Ollama's cloud servers for the vision check? `
      + "Your takes themselves, your references and voices stay on this PC; only these frames (and the shot's prompt) are sent.",
      { confirm: "Use the cloud model" })) return;
    api.putAutonomySettings(patch).then(show).catch((e) => setError((e as Error).message));
  };
  if (!s) return error ? <p className="error">{error}</p> : null;
  const cloudName = /(:|-)cloud$/i.test(model.trim());
  const dirty = mode !== s.vision || model.trim() !== s.vision_model;
  return (
    <section>
      <h2>Take inspection</h2>
      <p className="muted small">After each render FilmLab checks the take. The <strong>technical checks</strong> (length, black or
        frozen picture, silent or clipping sound) always run with ffmpeg on this PC. The <strong>vision check</strong> looks at
        four keyframes against the prompt and the scene, with an Ollama vision model: choose where those frames go. Speech
        isn't checked.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="settings card">
        <label><input type="checkbox" className="switch" checked={s.auto_inspect} onChange={(e) => save({ auto_inspect: e.target.checked })} />
          <span>Inspect new takes automatically</span></label>
        <div className="vision-modes" role="radiogroup" aria-label="Vision check">
          {([["off", "Off", "No frames are sent anywhere. Only the technical checks run."],
            ["local", "Local model", "An Ollama vision model on this PC (e.g. qwen2.5vl:7b). Frames stay here; it uses GPU memory while WanGP renders."],
            ["cloud", "Cloud model", "An Ollama cloud model (e.g. gemma4:31b-cloud). Four frames per take leave this PC; no GPU used here."],
          ] as const).map(([v, label, note]) => (
            <label key={v} className={`vision-mode ${mode === v ? "on" : ""}`}>
              <input type="radio" name="vision" checked={mode === v} onChange={() => setMode(v)} />
              <span><strong>{label}</strong><span className="block muted small">{note}</span></span>
            </label>
          ))}
        </div>
        {mode !== "off" && (
          <label><span>Vision model <span className="muted small">· an Ollama model that takes images</span></span>
            <span className="row"><input value={model} onChange={(e) => setModel(e.target.value)} />
              <span className={`chip ${cloudName ? "s-pending_approval" : "s-ready"}`}
                title={cloudName ? "Runs on Ollama's servers: frames leave this PC" : "Runs on this PC"}>{cloudName ? "Cloud" : "Local"}</span></span>
          </label>
        )}
        {mode === "local" && cloudName && <p className="small warn">That's a cloud model: name a model that runs on this PC, or choose Cloud.</p>}
        {mode === "cloud" && model.trim() && !cloudName && <p className="small warn">That model runs on this PC: choose Local for it.</p>}
        {dirty && <div className="actions"><button className="ghost small" onClick={() => show(s)}>Cancel</button>
          <button className="primary small" onClick={() => save({ vision: mode, ...(mode !== "off" ? { vision_model: model.trim() } : {}) })}>Save</button></div>}
        <p className="small">Now: {s.vision === "off" ? "vision check off, nothing leaves this PC"
          : s.vision === "local" ? `vision check with ${s.vision_model} on this PC`
          : `vision check with ${s.vision_model} in Ollama's cloud: four frames per take are sent there`}.</p>
      </div>
    </section>
  );
}

const PRESET_STATUS: Record<Preset["status"], string> = { ready: "Ready", needs_download: "Model not downloaded", unavailable: "Not available" };

/** Model presets: FilmLab's, one per other installed template, and the owner's own (deletable). */
function PresetSettings() {
  const [list, setList] = useState<Preset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => lib.presets().then((p) => { setList(p); setError(null); }).catch((e) => setError((e as Error).message));
  useEffect(() => { load(); }, []);
  if (!list) return error ? <p className="error">{error}</p> : null;
  return (
    <section>
      <h2>Model presets</h2>
      <p className="muted small">A preset sets a shot's model, size and length in one go, with advice for writing its prompt.
        Pick one in the shot editor; save your own there with “Save as preset”. A preset whose model files aren't downloaded
        stays unavailable: FilmLab never downloads models. Download it in WanGP, then press “Check again” under What WanGP can do here.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <table className="matrix">
        <thead><tr><th>Preset</th><th>Model</th><th>Size · length</th><th>References</th><th>State</th><th /></tr></thead>
        <tbody>
          {list.map((p) => (
            <tr key={p.id} className={p.status === "ready" ? "ok" : p.status === "needs_download" ? "warn" : "bad"}>
              <td><strong>{p.name}</strong>{p.source === "owner" && <span className="muted small"> · yours</span>}
                {p.use && <div className="small muted">{p.use}</div>}</td>
              <td className="small">{p.template}</td>
              <td className="small">{p.resolution} · {p.seconds} s</td>
              <td className="small">{p.refs || (p.takes_refs ? "references" : "none")}</td>
              <td className="small" title={p.reason}>{PRESET_STATUS[p.status]}</td>
              <td>{p.source === "owner" && <button className="ghost small danger-text" onClick={async () => {
                if (!await ask(`Delete the preset “${p.name}”? Shots that used it keep their settings.`)) return;
                try { await lib.deletePreset(p.id); load(); } catch (e) { setError((e as Error).message); }
              }}>Delete</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

const STYLE_LABEL: Record<DS["reply_style"], string> = {
  short: "Short: a few plain sentences", standard: "Standard", detailed: "Detailed: reasoning, lists and tables",
};

/** Settings → Director: the owner's standing notes and limits, what it runs on, a test, its instructions. */
function DirectorSettings() {
  const [o, setO] = useState<DirectorOverview | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [test, setTest] = useState<{ busy: boolean; text?: string; ok?: boolean }>({ busy: false });
  const [models, setModels] = useState<DirectorModel[] | null>(null);
  const [trying, setTrying] = useState<string | null>(null);
  const load = () => api.director().then((x) => { setO(x); setNotes(x.settings.notes); setError(null); })
    .catch((e) => setError((e as Error).message));
  useEffect(() => { load(); api.directorModels().then(setModels).catch(() => setModels([])); }, []);
  /** Try a model with one harmless turn first; save it only if it answers. */
  const chooseModel = async (id: string) => {
    if (!o) return;
    if (!id) { await save({ model: "" }, "Model"); setTest({ busy: false }); return; }
    const m = models?.find((x) => x.id === id);
    if (m && !m.cloud && !(await ask(`Run the director on ${m.name}, on this PC's GPU? `
      + "It shares the video memory WanGP renders with, so they take turns (Health → GPU sharing): FilmLab unloads it "
      + "before each render, and while WanGP is rendering the director waits for the queue to finish. "
      + `Small local models (${m.size ?? "this size"}) are also much weaker at long plans and tool calls than the cloud model.`,
      { confirm: "Try it" }))) return;
    setTrying(id); setTest({ busy: true });
    try {
      const r = await api.testDirector(id);
      if (r.ok) {
        await save({ model: id }, "Model");
        setTest({ busy: false, ok: true, text: `${id} answered in ${r.seconds} s (“${r.reply}”). The director now runs on it.` });
      } else {
        setTest({ busy: false, ok: false, text: `${id} didn't answer (${r.error}); the director stays on ${o.settings.model || o.model}.` });
      }
    } catch (e) { setTest({ busy: false, ok: false, text: (e as Error).message }); }
    setTrying(null);
  };
  const save = async (patch: Partial<DS>, what: string) => {
    try {
      const s = await api.putDirectorSettings(patch);
      setO((x) => x && { ...x, settings: s }); setNotes(s.notes); setError(null);
      setSaved(`${what} saved.`); setTimeout(() => setSaved(null), 2500);
    } catch (e) { setError((e as Error).message); }
  };
  const runTest = async () => {
    setTest({ busy: true });
    try {
      const r = await api.testDirector();
      setTest({ busy: false, ok: r.ok, text: r.ok ? `Answered in ${r.seconds} s: “${r.reply}”` : `No answer after ${r.seconds} s: ${r.error}` });
    } catch (e) { setTest({ busy: false, ok: false, text: (e as Error).message }); }
  };
  if (!o) return error ? <p className="error">{error}</p> : null;
  const s = o.settings;
  return (
    <section>
      <div className="panel-head">
        <h2>Director</h2>
        <span className="small muted grow">
          <i className={`dot ${o.connected ? "ok" : "bad"}`} /> {o.connected ? "connected" : "not reachable"} · agent “{o.agent}” · runs on {s.model || o.model || "unknown"}
        </span>
        <button className="ghost small" disabled={test.busy} onClick={runTest}>{test.busy ? "Testing…" : "Test the director"}</button>
      </div>
      {test.text && <p className={`small ${test.ok ? "ok-text" : "error"}`}>{test.text}</p>}
      <p className="muted small">The director plans and edits only through FilmLab's own tools ({o.tools.length} of them), and FilmLab
        checks every call. These settings shape how it works; they can't give it new tools or lift any limit.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {saved && <p className="ok-text small">{saved}</p>}
      <div className="settings card">
        <label>
          <span>Studio notes <span className="muted small">· added after every message FilmLab sends it: chat, plans, enhance, redo runs</span></span>
          <span className="director-notes">
            <textarea rows={4} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Keep every shot at 832x480. Prefer slow, static cameras. Use a character's in-costume image as the reference." />
            <span className="row">
              <span className="muted small grow">{notes.length} / 2000</span>
              {notes !== s.notes && <>
                <button className="ghost small" onClick={() => setNotes(s.notes)}>Undo</button>
                <button className="primary small" onClick={() => save({ notes }, "Studio notes")}>Save notes</button>
              </>}
            </span>
          </span>
        </label>
        <label><span>Model <span className="muted small">· an Ollama model that can call tools; picking one tries it before saving</span></span>
          <select value={trying ?? s.model} disabled={!!trying || models === null} onChange={(e) => chooseModel(e.target.value)}>
            <option value="">OpenClaw's default{o.model ? ` (${o.model.replace(/^ollama\//, "")})` : ""}</option>
            {s.model && !models?.some((m) => m.id === s.model) && <option value={s.model}>{s.model} (not offered by Ollama now)</option>}
            {models?.some((m) => m.cloud) && <optgroup label="Cloud (no GPU)">
              {models.filter((m) => m.cloud).map((m) => <option key={m.id} value={m.id}>{m.name}{m.size ? ` · ${m.size}` : ""}</option>)}
            </optgroup>}
            {models?.some((m) => !m.cloud) && <optgroup label="Local (uses this PC's GPU)">
              {models.filter((m) => !m.cloud).map((m) => <option key={m.id} value={m.id}>{m.name}{m.size ? ` · ${m.size}` : ""}</option>)}
            </optgroup>}
          </select></label>
        <label><span>Reply style</span>
          <select value={s.reply_style} onChange={(e) => save({ reply_style: e.target.value as DS["reply_style"] }, "Reply style")}>
            {(Object.keys(STYLE_LABEL) as DS["reply_style"][]).map((k) => <option key={k} value={k}>{STYLE_LABEL[k]}</option>)}
          </select></label>
        <label><span>Tool steps per chat message <span className="muted small">· 2-12; more lets it look things up before answering</span></span>
          <select value={s.chat_rounds} onChange={(e) => save({ chat_rounds: Number(e.target.value) }, "Chat steps")}>
            {Array.from({ length: 11 }, (_, i) => i + 2).map((n) => <option key={n}>{n}</option>)}
          </select></label>
        <label><span>Tool steps per storyboard plan <span className="muted small">· 6-30; a big plan with fixes may need more</span></span>
          <select value={s.plan_rounds} onChange={(e) => save({ plan_rounds: Number(e.target.value) }, "Plan steps")}>
            {[6, 8, 10, 12, 14, 16, 20, 24, 30].map((n) => <option key={n}>{n}</option>)}
          </select></label>
        <label><span>Autonomy for new projects <span className="muted small">· each project can still change it</span></span>
          <select value={s.default_autonomy} onChange={(e) => save({ default_autonomy: e.target.value as DS["default_autonomy"] }, "Default autonomy")}>
            {Object.entries(AUTONOMY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
      </div>
      <details className="director-instructions">
        <summary className="small">Its installed instructions ({o.instructions.map((i) => i.name).join(", ") || "none found"}) · read-only</summary>
        <p className="muted small">From the director's workspace in the FilmLab distro, installed from <code>agent/workspace/</code> in the repo
          by <code>scripts/install/f1a-director-workspace.sh</code>. To change them, edit the repo and run that script. The model is set in
          OpenClaw's config by <code>f1a-director-policy.sh</code>.</p>
        {o.instructions.map((i) => (
          <details key={i.name} className="card"><summary><strong>{i.name}</strong></summary><Markdown text={i.text} /></details>
        ))}
      </details>
    </section>
  );
}
