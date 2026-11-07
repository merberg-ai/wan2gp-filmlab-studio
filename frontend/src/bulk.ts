/** Deleting shots and scenes with an Undo toast instead of a confirmation (backend bulk.py keeps it undoable). */
import { api } from "./api";
import { showToast } from "./notify";

export async function deleteWithUndo(projectId: string, what: { shot_ids?: string[]; scene_ids?: string[] }, label: string) {
  const r = await api.bulk(projectId, { action: "delete", ...what });
  const kept = r.skipped.length
    ? ` Kept ${r.skipped.map((s) => `${s.position}. ${s.title} (${s.reason})`).join("; ")}.` : "";
  if (!r.undo) {
    if (kept) throw new Error(`Nothing was deleted.${kept}`);
    return r;
  }
  const token = r.undo;
  showToast({ title: label, body: `Moved to Recently deleted.${kept}`, kind: "ok",
    action: { label: "Undo", run: () => api.bulkUndo(projectId, token) } }, 10000);
  return r;
}
