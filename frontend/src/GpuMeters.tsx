/** Live GPU load and VRAM bars, fed by the backend's `system.gpu` events (a sample every ~2 s while connected). */

export interface GpuSample {
  gpu?: string; load?: number | null; vram_used_gb?: number; vram_total_gb?: number;
  temp_c?: number | null; power_w?: number | null; unavailable?: boolean;
}

const level = (pct: number) => (pct >= 95 ? "crit" : pct >= 85 ? "high" : "");

function Meter({ label, pct, value, busy }: { label: string; pct: number; value: string; busy: boolean }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <span className={`meter ${level(p)} ${busy ? "busy" : ""}`}>
      <span className="meter-label">{label}</span>
      <span className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)}>
        <span className="meter-fill" style={{ transform: `scaleX(${p / 100})` }} />
      </span>
      <span className="meter-value">{value}</span>
    </span>
  );
}

export function GpuMeters({ sample, detailed }: { sample: GpuSample | null; detailed?: boolean }) {
  if (!sample) return null;
  if (sample.unavailable || !sample.vram_total_gb) {
    return detailed ? <p className="muted small">GPU stats aren't available (nvidia-smi didn't answer).</p> : null;
  }
  const load = sample.load ?? 0;
  const vram = (100 * (sample.vram_used_gb ?? 0)) / sample.vram_total_gb;
  const busy = load >= 10;  // the shimmer: the GPU is doing real work (a render, say)
  const extra = [sample.temp_c != null && `${Math.round(sample.temp_c)} °C`, sample.power_w != null && `${Math.round(sample.power_w)} W`]
    .filter(Boolean).join(" · ");
  const title = `${sample.gpu ?? "GPU"} · load ${Math.round(load)}% · VRAM ${sample.vram_used_gb} of ${sample.vram_total_gb} GB`
    + (extra ? ` · ${extra}` : "");
  return (
    <span className={`gpu-meters ${detailed ? "detailed" : ""}`} title={title}>
      <Meter label="GPU" pct={sample.load == null ? 0 : load} value={sample.load == null ? "n/a" : `${Math.round(load)}%`} busy={busy} />
      <Meter label="VRAM" pct={vram} busy={busy}
             value={detailed ? `${sample.vram_used_gb} / ${sample.vram_total_gb} GB` : `${sample.vram_used_gb}/${Math.round(sample.vram_total_gb)} GB`} />
      {detailed && extra && <span className="meter-extra muted">{extra}</span>}
    </span>
  );
}
