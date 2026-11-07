/** "Render finished" alerts: an in-app toast while the cockpit is in view, a system notification when it isn't
 *  (only if this device turned them on). Phones show notifications only through a service worker. */
import { useEffect, useState } from "react";

const KEY = "filmlab.notify";
export type NotifyState = "on" | "off" | "blocked" | "unsupported";

function stored(): boolean {
  try { return localStorage.getItem(KEY) === "on"; } catch { return false; }
}
function store(on: boolean) {
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* private window: on for this visit only */ }
}

let enabled = stored();
const watchers = new Set<() => void>();

export function notifyState(): NotifyState {
  if (!("Notification" in window) || !window.isSecureContext) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  return enabled && Notification.permission === "granted" ? "on" : "off";
}

async function worker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try { return await navigator.serviceWorker.register("/sw.js"); } catch { return null; }  // e.g. an untrusted certificate
}

/** Turn notifications on or off for this device; asks the browser's permission the first time. */
export async function setNotify(on: boolean): Promise<NotifyState> {
  if (on && notifyState() !== "unsupported") {
    const p = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    enabled = p === "granted";
    if (enabled) void worker();
  } else enabled = false;
  store(enabled);
  watchers.forEach((w) => w());
  return notifyState();
}

export function useNotifyState(): NotifyState {
  const [s, setS] = useState(notifyState);
  useEffect(() => {
    const w = () => setS(notifyState());
    watchers.add(w);
    if (enabled && Notification.permission === "granted") void worker();
    return () => { watchers.delete(w); };
  }, []);
  return s;
}

// --- toasts -----------------------------------------------------------------

export interface Toast {
  id: number; title: string; body: string; kind: "ok" | "bad"; url?: string;
  /** A button on the toast, e.g. Undo; the toast closes once it ran. */
  action?: { label: string; run: () => unknown };
}
let toasts: Toast[] = [];
let nextId = 1;
const toastWatchers = new Set<(t: Toast[]) => void>();
const emit = () => toastWatchers.forEach((w) => w(toasts));

export function dismissToast(id: number) { toasts = toasts.filter((t) => t.id !== id); emit(); }

export function useToasts(): Toast[] {
  const [t, setT] = useState(toasts);
  useEffect(() => { toastWatchers.add(setT); return () => { toastWatchers.delete(setT); }; }, []);
  return t;
}

/** A toast now, whatever the page's visibility (for what the owner just did, e.g. a delete they can undo). */
export function showToast(t: Omit<Toast, "id">, ms = 8000) {
  const toast = { ...t, id: nextId++ };
  toasts = [...toasts.slice(-3), toast];
  emit();
  window.setTimeout(() => dismissToast(toast.id), ms);
}

/** Tell the owner something finished: a toast if they're looking, else a system notification if turned on. */
export async function alertOwner(t: Omit<Toast, "id">) {
  if (document.visibilityState === "visible") {
    const toast = { ...t, id: nextId++ };
    toasts = [...toasts.slice(-3), toast];
    emit();
    window.setTimeout(() => dismissToast(toast.id), 8000);
    return;
  }
  if (notifyState() !== "on") return;
  const opts: NotificationOptions = { body: t.body, tag: t.url ?? t.title, data: { url: t.url } };
  try {
    const n = new Notification(t.title, opts);  // desktop browsers
    n.onclick = () => { window.focus(); if (t.url) location.hash = t.url.replace(/^\/?/, ""); n.close(); };
  } catch {  // phones: "Illegal constructor"; they need the service worker
    const reg = await worker();
    await reg?.showNotification(t.title, opts).catch(() => undefined);
  }
}
