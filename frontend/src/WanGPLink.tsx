/** WanGP's own web page (found by the backend on WanGP's host), and a render's model-download note. */
import { useEffect, useState } from "react";
import { api, type DownloadProgress, type WanGPWebUI } from "./api";

let found: Promise<WanGPWebUI> | null = null;
let foundAt = 0;
function locate(): Promise<WanGPWebUI> {
  if (!found || Date.now() - foundAt > 5 * 60_000) {  // look again now and then: WanGP may have restarted elsewhere
    foundAt = Date.now();
    found = api.wangpWebUI().catch(() => ({ found: false }));
  }
  return found;
}

/** The page's address as this browser reaches it: WanGP on FilmLab's own machine is at this page's host. */
export function webuiUrl(w: WanGPWebUI | null): string | null {
  if (!w?.found) return null;
  if (w.url) return w.url;
  return w.port ? `http://${location.hostname}:${w.port}/` : null;
}

/** A small "Watch in WanGP ↗" link; nothing when WanGP's page wasn't found. */
export function WanGPLink({ label = "Watch in WanGP ↗", title }: { label?: string; title?: string }) {
  const [w, setW] = useState<WanGPWebUI | null>(null);
  useEffect(() => { let live = true; locate().then((x) => live && setW(x)); return () => { live = false; }; }, []);
  const url = webuiUrl(w);
  if (!url) return null;
  return <a className="small wangp-link" href={url} target="_blank" rel="noopener noreferrer"
    title={title ?? "Open WanGP's own page, which shows the download's exact progress"}>{label}</a>;
}

const size = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.round(b / 1e3)} KB`);

/** While WanGP fetches a model's files first: its status line, what has arrived so far, and the link to WanGP. */
export function DownloadNote({ text, progress }: { text: string; progress?: DownloadProgress | null }) {
  return (
    <div className="download-note small" role="status">
      <span aria-hidden>⬇</span> <span>{text}</span>
      {progress && progress.bytes > 0 && (
        <span className="muted"> · {size(progress.bytes)} so far{progress.speed > 0 ? ` · ${size(progress.speed)}/s` : ""}</span>
      )}
      {" "}<WanGPLink />
      <span className="block muted">WanGP downloads a model's files the first time something renders with it; the render
        starts when they are in.</span>
    </div>
  );
}
