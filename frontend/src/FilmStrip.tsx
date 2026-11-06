/** The whole film at a glance: one frame per shot, as wide as the shot is long, grouped by scene, with the
 *  take the edit uses as its picture. Click a frame to open its shot; drag frames to reorder. */
import { type DragEvent, useState } from "react";
import { api, type ProjectDetail, type Shot, type Take } from "./api";
import { ACTIVE_JOB, STATUS_LABEL } from "./Shots";

type Act = (fn: () => Promise<unknown>) => Promise<void>;
const secs = (s: Shot) => (s.frames - 1) / 24;

/** The take the edit uses: the explicit choice, else the newest marked good, else the newest not rejected. */
export function usedTake(shot: Shot, takes: Take[]): Take | undefined {
  const mine = takes.filter((t) => t.shot_id === shot.id);
  return mine.find((t) => t.id === shot.selected_take_id)
    ?? [...mine].reverse().find((t) => t.verdict === "good")
    ?? [...mine].reverse().find((t) => t.verdict !== "reject");
}

export function FilmStrip({ project, selected, onSelect, act }: {
  project: ProjectDetail; selected: string | null; onSelect: (id: string | null) => void; act: Act;
}) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  if (!project.shots.length) return null;

  const sceneIds = new Set(project.scenes.map((s) => s.id));
  const groups = [
    ...project.scenes.map((sc) => ({ id: sc.id as string | null, title: sc.title, shots: project.shots.filter((s) => s.scene_id === sc.id) })),
    { id: null, title: project.scenes.length ? "Unsorted" : "", shots: project.shots.filter((s) => !s.scene_id || !sceneIds.has(s.scene_id)) },
  ].filter((g) => g.shots.length);
  const total = project.shots.reduce((n, s) => n + secs(s), 0);
  const target = project.target_seconds || 0;
  const busy = (id: string) => project.jobs.some((j) => j.shot_id === id && ACTIVE_JOB.has(j.status));

  const drop = (e: DragEvent, group: typeof groups[number], before: Shot) => {
    e.preventDefault();
    const id = dragging;
    setDragging(null); setOver(null);
    if (!id || id === before.id) return;
    const index = group.shots.filter((s) => s.id !== id).findIndex((s) => s.id === before.id);
    act(() => api.moveShot(id, group.id, index));
  };
  const open = (id: string) => {
    onSelect(id === selected ? null : id);
    if (id !== selected) requestAnimationFrame(() => document.getElementById(`shot-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  };

  return (
    <div className="filmstrip" aria-label="Film strip">
      <div className="fs-track">
        {groups.map((g) => (
          <div key={g.id ?? "unsorted"} className="fs-scene" style={{ flexGrow: g.shots.reduce((n, s) => n + secs(s), 0) }}>
            {g.title && <div className="fs-scene-title" title={g.title}>{g.title}</div>}
            <div className="fs-shots">
              {g.shots.map((s) => {
                const take = usedTake(s, project.takes);
                const movable = !busy(s.id);
                return (
                  <button key={s.id} type="button" style={{ flexGrow: secs(s) }}
                    className={`fs-shot st-${s.status} ${s.id === selected ? "on" : ""} ${over === s.id ? "over" : ""} ${dragging === s.id ? "dragging" : ""}`}
                    title={`${s.position}. ${s.title} · ${secs(s).toFixed(1)} s · ${STATUS_LABEL[s.status] ?? s.status}${take ? "" : " · no take yet"}`}
                    aria-pressed={s.id === selected} onClick={() => open(s.id)}
                    draggable={movable}
                    onDragStart={(e) => { setDragging(s.id); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/x-filmlab-shot", s.id); }}
                    onDragEnd={() => { setDragging(null); setOver(null); }}
                    onDragOver={(e) => { if (dragging) { e.preventDefault(); setOver(s.id); } }}
                    onDragLeave={() => setOver((o) => (o === s.id ? null : o))}
                    onDrop={(e) => drop(e, g, s)}>
                    {take?.thumb_media_id && <img src={api.mediaUrl(take.thumb_media_id)} alt="" loading="lazy" draggable={false} />}
                    <span className="fs-pos">{s.position}</span>
                    {s.locked && <span className="fs-lock" aria-label="locked">🔒</span>}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {target > total && <div className="fs-rest" style={{ flexGrow: target - total }} title={`${(target - total).toFixed(1)} s to go to the ${target} s target`} />}
      </div>
      <div className="fs-scale small muted">
        <span>0 s</span>
        <span className="grow" />
        {target ? <span className={total > target * 1.1 ? "warn" : ""}>{total.toFixed(1)} of {target} s{total > target ? ` · ${(total - target).toFixed(1)} s over` : ""}</span>
          : <span>{total.toFixed(1)} s</span>}
      </div>
    </div>
  );
}
