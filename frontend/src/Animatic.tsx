/** The storyboard as an animatic: each shot as a still for its length, with its transition, the edit's music and the
 *  shot's idea as a caption. A quick read of the pacing before (or between) renders. */
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ProjectDetail, type Shot } from "./api";
import { usedTake } from "./FilmStrip";
import { lib } from "./library-api";

interface Frame { shot: Shot; seconds: number; image: string | null; source: "take" | "reference" | "none" }

function frames(project: ProjectDetail): Frame[] {
  return project.shots.map((shot) => {
    const take = usedTake(shot, project.takes);
    const ref = shot.refs?.[0];
    const image = take?.thumb_media_id ? api.mediaUrl(take.thumb_media_id) : ref ? lib.fileUrl(ref.file_id, true) : null;
    return { shot, seconds: Math.max(0.5, (shot.frames - 1) / 24), image, source: take ? "take" : ref ? "reference" : "none" };
  });
}

export function Animatic({ project, onClose }: { project: ProjectDetail; onClose: () => void }) {
  const list = useMemo(() => frames(project), [project]);
  const total = list.reduce((n, f) => n + f.seconds, 0);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [music, setMusic] = useState<{ url: string; start: number; offset: number } | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const last = useRef<number | null>(null);

  useEffect(() => {  // the edit's first music track, if any
    api.edit(project.id).then((tl) => {
      const m = tl.doc.music[0];
      if (m) setMusic({ url: lib.fileUrl(m.file_id), start: m.start_s, offset: m.offset_s });
    }).catch(() => undefined);
  }, [project.id]);

  useEffect(() => {
    if (!playing) { last.current = null; return; }
    let raf = 0;
    const tick = (now: number) => {
      if (last.current != null) setT((x) => Math.min(total, x + (now - last.current!) / 1000));
      last.current = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, total]);
  useEffect(() => { if (t >= total && playing) setPlaying(false); }, [t, total, playing]);

  useEffect(() => {  // keep the music where the film is
    const a = audio.current;
    if (!a || !music) return;
    const at = t - music.start + music.offset;
    if (!playing || at < 0) { a.pause(); return; }
    if (Math.abs(a.currentTime - at) > 0.3) a.currentTime = at;
    if (a.paused) a.play().catch(() => undefined);
  }, [t, playing, music]);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", on);
    document.body.classList.add("modal-open");
    return () => { window.removeEventListener("keydown", on); document.body.classList.remove("modal-open"); };
  }, [onClose]);

  let acc = 0, idx = 0;
  for (; idx < list.length; idx++) { if (t < acc + list[idx].seconds) break; acc += list[idx].seconds; }
  idx = Math.min(idx, list.length - 1);
  const cur = list[idx];
  const into = t - acc;
  const prev = idx > 0 ? list[idx - 1] : null;
  const fade = prev && prev.shot.transition && prev.shot.transition !== "cut" ? Math.min(1, into / 0.5) : 1;
  const scene = project.scenes.find((s) => s.id === cur?.shot.scene_id);
  if (!cur) return null;
  return (
    <div className="animatic" role="dialog" aria-modal="true" aria-label="Animatic">
      <div className="an-top">
        <strong className="grow">Animatic · {project.title}</strong>
        <span className="small muted">{list.filter((f) => f.source === "take").length} of {list.length} shots from takes, the rest from references</span>
        <button className="ghost small" onClick={onClose}>Close</button>
      </div>
      <div className="an-stage">
        {prev && fade < 1 && <Still frame={prev} style={{ opacity: 1 - fade }} />}
        <Still frame={cur} style={{ opacity: fade }} />
        <div className="an-caption">
          <span className="small muted">{scene ? `${scene.title} · ` : ""}shot {cur.shot.position}</span>
          <strong>{cur.shot.title}</strong>
          {cur.shot.idea && <span className="small">{cur.shot.idea}</span>}
        </div>
      </div>
      <div className="an-controls row">
        <button className="primary small" onClick={() => { if (t >= total) setT(0); setPlaying((p) => !p); }}>{playing ? "Pause" : t >= total ? "Play again" : "Play"}</button>
        <input type="range" className="grow" min={0} max={total} step={0.05} value={t} aria-label="Scrub the animatic"
          onChange={(e) => setT(Number(e.target.value))} />
        <span className="small muted">{t.toFixed(1)} / {total.toFixed(1)} s{project.target_seconds ? ` (target ${project.target_seconds} s)` : ""}</span>
      </div>
      <div className="an-strip">
        {list.map((f, i) => (
          <button key={f.shot.id} className={`an-cell ${i === idx ? "on" : ""}`} style={{ flexGrow: f.seconds }}
            title={`${f.shot.position}. ${f.shot.title} · ${f.seconds.toFixed(1)} s`}
            onClick={() => setT(list.slice(0, i).reduce((n, x) => n + x.seconds, 0))}>
            {f.image ? <img src={f.image} alt="" /> : <span>{f.shot.position}</span>}
          </button>
        ))}
      </div>
      {music && <audio ref={audio} src={music.url} preload="auto" />}
    </div>
  );
}

function Still({ frame, style }: { frame: Frame; style: React.CSSProperties }) {
  return frame.image
    ? <img className="an-still" src={frame.image} alt={frame.shot.title} style={style} />
    : <div className="an-still an-card" style={style}><p>{frame.shot.prompt}</p></div>;
}
