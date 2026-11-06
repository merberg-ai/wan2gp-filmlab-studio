import { useCallback, useEffect, useState } from "react";
import { api, csrfHeaders, request } from "./api";
import { lib } from "./library-api";

/** LAN self-test: open #/selftest on another device and press Run. Each check runs from this browser. */

interface Check { name: string; ok: boolean; detail: string; ms: number | null }
interface Result { at: string; client: string | null; passed: boolean; checks: Check[]; user_agent: string; screen: string; origin: string }
interface Info { client: string | null; sample_video: string | null; results: Result[] }

const TIMEOUT = (ms: number, what: string) =>
  new Promise<never>((_, reject) => setTimeout(() => reject(new Error(`${what} took over ${ms / 1000} s`)), ms));

async function timed(name: string, fn: () => Promise<string>): Promise<Check> {
  const t = performance.now();
  try {
    const detail = await fn();
    return { name, ok: true, detail, ms: Math.round(performance.now() - t) };
  } catch (e) {
    return { name, ok: false, detail: (e as Error).message.slice(0, 300), ms: Math.round(performance.now() - t) };
  }
}

function websocketRoundTrip(): Promise<string> {
  return Promise.race([TIMEOUT(8000, "the event stream"), new Promise<string>((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "stream.ready") { ws.close(); resolve(`stream ready, last event ${ev.payload.last_event_id}`); }
    };
    ws.onclose = (e) => reject(new Error(`closed with code ${e.code}`));
    ws.onerror = () => reject(new Error("WebSocket error"));
  })]);
}

function playVideo(url: string): Promise<string> {
  return Promise.race([TIMEOUT(15000, "loading the video"), new Promise<string>((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.playsInline = true;
    v.onloadeddata = () => { const d = `${v.videoWidth}x${v.videoHeight}, ${v.duration.toFixed(1)} s`; v.removeAttribute("src"); v.load(); resolve(d); };
    v.onerror = () => reject(new Error(`the browser couldn't decode it (${v.error?.code ?? "?"})`));
    v.src = url;
  })]);
}

function testImage(): Promise<File> {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 144;
  const g = c.getContext("2d")!;
  g.fillStyle = "#223"; g.fillRect(0, 0, 256, 144);
  g.fillStyle = "#fc6"; g.font = "16px sans-serif";
  g.fillText("FilmLab LAN self-test", 20, 60);
  g.fillText(new Date().toISOString(), 20, 90); // unique, so the upload is never deduplicated
  return new Promise((resolve, reject) => c.toBlob((b) => b ? resolve(new File([b], "selftest.png", { type: "image/png" })) : reject(new Error("canvas")), "image/png"));
}

async function uploadRoundTrip(): Promise<string> {
  const a = await lib.createAsset({ kind: "prop", name: `LAN self-test ${Date.now()} (delete me)`, description: "Made by the self-test page; it removes it again" });
  try {
    const img = await testImage();
    const up = await lib.upload(a.variants[0].id, img, { caption: "self-test" }) as { file_id: string };
    const back = await fetch(`/api/library/files/${up.file_id}`, { credentials: "same-origin" });
    const bytes = (await back.arrayBuffer()).byteLength;
    if (!back.ok || bytes !== img.size) throw new Error(`read back ${bytes} of ${img.size} bytes (${back.status})`);
    return `${img.size} bytes up and back`;
  } finally {
    await lib.deleteAsset(a.id).catch(() => undefined);
    await fetch(`/api/trash/asset/${a.id}`, { method: "DELETE", credentials: "same-origin", headers: csrfHeaders() }).catch(() => undefined);
  }
}

export function SelfTestPage() {
  const [info, setInfo] = useState<Info | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => request<Info>("GET", "/api/selftest").then(setInfo).catch((e) => setError((e as Error).message)), []);
  useEffect(() => { load(); }, [load]);

  const run = async () => {
    if (!info) return;
    setError(null); setChecks([]);
    const out: Check[] = [];
    const step = async (name: string, fn: () => Promise<string>) => {
      setRunning(name);
      const c = await timed(name, fn);
      out.push(c); setChecks([...out]);
    };
    await step("Secure connection", async () => {
      const local = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
      if (location.protocol !== "https:" && !local) throw new Error(`opened over ${location.protocol} instead of HTTPS`);
      if (!window.isSecureContext) throw new Error("the browser doesn't treat this page as secure (certificate?)");
      return `${location.protocol}//${location.host}`;
    });
    await step("Signed in", async () => { if (!(await api.me())) throw new Error("no session"); return "session valid"; });
    await step("API reads", async () => {
      const h = await api.health();
      const p = await request<unknown[]>("GET", "/api/projects");
      return `version ${h.version}, ${p.length} projects, WanGP ${h.wangp.connected ? "connected" : "not connected"}`;
    });
    await step("Live updates (WebSocket)", websocketRoundTrip);
    if (info.sample_video) {
      const url = `/api/media/${info.sample_video}`;
      await step("Video streaming (range request)", async () => {
        const r = await fetch(url, { credentials: "same-origin", headers: { Range: "bytes=0-65535" } });
        const n = (await r.arrayBuffer()).byteLength;
        if (r.status !== 206) throw new Error(`answer ${r.status} instead of 206`);
        return `206, ${n} bytes of ${r.headers.get("content-range")?.split("/")[1] ?? "?"}`;
      });
      await step("Video playback", () => playVideo(url));
    } else {
      out.push({ name: "Video streaming", ok: true, detail: "skipped: no takes yet", ms: null });
    }
    await step("Upload and delete", uploadRoundTrip);
    setRunning("Saving the result");
    try {
      await request("POST", "/api/selftest", { checks: out, user_agent: navigator.userAgent.slice(0, 300),
        screen: `${screen.width}x${screen.height}`, origin: location.origin });
    } catch (e) { setError(`couldn't save the result: ${(e as Error).message}`); }
    setRunning(null);
    load();
  };

  const passed = checks.length > 0 && !running && checks.every((c) => c.ok);
  return (
    <main className="page">
      <section>
        <h2>LAN self-test</h2>
        <p className="muted small">Open this page on another device on your network (your phone, a laptop) and press Run. It checks
          from that browser: the secure connection, your session, the API, live updates, video streaming and playback, and an
          upload (a small test image it deletes again). The result is saved here with the device's address.</p>
        {info && <p className="small">This browser reaches FilmLab from <strong>{info.client ?? "?"}</strong> at {location.host}.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={!info || !!running} onClick={run}>{running ? `Running: ${running}…` : "Run the self-test"}</button>
        {checks.length > 0 && (
          <table className="matrix" style={{ marginTop: 12 }}>
            <thead><tr><th>Check</th><th>Result</th><th>Detail</th><th>Time</th></tr></thead>
            <tbody>
              {checks.map((c) => (
                <tr key={c.name} className={c.ok ? "ok" : "bad"}>
                  <td>{c.name}</td><td>{c.ok ? <span className="ok-text">PASS</span> : <span className="error">FAIL</span>}</td>
                  <td className="small">{c.detail}</td><td className="small">{c.ms === null ? "" : `${c.ms} ms`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {passed && <p className="ok-text">All checks passed on this device.</p>}
      </section>
      {info && info.results.length > 0 && (
        <section>
          <h2>Earlier runs</h2>
          <table className="matrix">
            <thead><tr><th>When</th><th>From</th><th>Result</th><th>Browser</th></tr></thead>
            <tbody>
              {info.results.map((r) => (
                <tr key={r.at} className={r.passed ? "ok" : "bad"}>
                  <td className="small">{new Date(r.at).toLocaleString()}</td><td className="small">{r.client} · {r.screen}</td>
                  <td>{r.passed ? <span className="ok-text">PASS</span>
                    : <span className="error">FAIL: {r.checks.filter((c) => !c.ok).map((c) => c.name).join(", ")}</span>}</td>
                  <td className="small muted">{r.user_agent.slice(0, 90)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}
