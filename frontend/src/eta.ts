/** Render time estimates from this machine's own past renders (seconds per frame, per model). */
import { useEffect, useState } from "react";
import type { Job, ProjectDetail, Shot } from "./api";

type Rates = ProjectDetail["render_rates"];

/** WanGP's generation time leaves out loading and saving: about 13 s on this machine's recent renders. */
const OVERHEAD_S = 15;

/** Expected WanGP time for a shot, or null when its model hasn't rendered here yet. */
export function expected(shot: Shot | undefined, rates: Rates): number | null {
  const r = shot && rates?.[shot.model_type];
  return r ? r.s_per_frame * shot.frames + OVERHEAD_S : null;
}

/** For a job WanGP is working on: seconds left (never below 0) and seconds since it started; nulls when unknown. */
export function jobEta(job: Job, shot: Shot | undefined, rates: Rates, now: number) {
  const start = job.started_at ? Date.parse(job.started_at) : NaN;
  const elapsed = Number.isFinite(start) ? Math.max(0, (now - start) / 1000) : null;
  const total = expected(shot, rates);
  const left = total != null && elapsed != null ? total - elapsed : null;
  // a little past the estimate is normal ("almost done"); well past it (half again) is worth saying
  return { elapsed, left, over: left != null && left < 0, late: left != null && total != null && -left > total * 0.5 };
}

export function duration(s: number): string {
  if (s < 50) return "under a minute";
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

/** "about 3 min left · running 2 min", or what's known of it. */
export function etaText(job: Job, shot: Shot | undefined, rates: Rates, now: number): string | null {
  const { elapsed, left, over, late } = jobEta(job, shot, rates, now);
  if (elapsed == null) return null;
  const ran = `running ${duration(elapsed)}`;
  if (left == null) return `${ran} · no estimate yet (this model hasn't rendered here before)`;
  if (late) return `${ran} · taking longer than usual`;
  return over || left < 50 ? `almost done · ${ran}` : `about ${duration(left)} left · ${ran}`;
}

/** The current time, refreshed every few seconds while mounted (for countdowns). */
export function useNow(ms = 5000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}
