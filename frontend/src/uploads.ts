/** File uploads with progress: every upload in flight is listed (name, bytes sent) for the progress panel. fetch()
 *  can't report upload progress, so these go through XMLHttpRequest. */
import { useEffect, useState } from "react";
import { ApiError, csrfHeaders } from "./api";

export interface Upload { id: number; name: string; loaded: number; total: number }

let uploads: Upload[] = [];
let next = 1;
const watchers = new Set<() => void>();
const changed = (list: Upload[]) => { uploads = list; watchers.forEach((w) => w()); };

export function useUploads(): Upload[] {
  const [, tick] = useState(0);
  useEffect(() => {
    const w = () => tick((n) => n + 1);
    watchers.add(w);
    return () => { watchers.delete(w); };
  }, []);
  return uploads;
}

/** Send a file as the request body; resolves with the JSON reply, or throws ApiError("<file>: <reason>"). */
export function sendFile<T>(method: string, url: string, file: File): Promise<T> {
  const id = next++;
  changed([...uploads, { id, name: file.name, loaded: 0, total: file.size }]);
  const done = () => changed(uploads.filter((u) => u.id !== id));
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    for (const [k, v] of Object.entries(csrfHeaders())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      changed(uploads.map((u) => (u.id === id ? { ...u, loaded: e.loaded, total: e.lengthComputable ? e.total : u.total } : u)));
    };
    xhr.onload = () => {
      done();
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body as T); return; }
      const detail = (body as { detail?: unknown } | null)?.detail;
      reject(new ApiError(xhr.status, `${file.name}: ${typeof detail === "string" ? detail : xhr.statusText || "upload failed"}`));
    };
    xhr.onerror = () => { done(); reject(new ApiError(0, `${file.name}: the upload didn't reach FilmLab (connection lost)`)); };
    xhr.onabort = () => { done(); reject(new ApiError(0, `${file.name}: upload cancelled`)); };
    xhr.send(file);
  });
}
