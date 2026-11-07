// Typed client for the F3 library, image generation, model settings and shot references.

import { ApiError, csrfHeaders, request, type QueueOwner } from "./api";

export type AssetKind = "character" | "location" | "prop" | "style" | "sound";
export const KINDS: AssetKind[] = ["character", "location", "prop", "style", "sound"];

export interface Asset {
  id: string; kind: AssetKind; name: string; description: string; notes: string; tags: string;
  created_at: string; updated_at: string; variant_count?: number; cover_file_id?: string | null;
}
export interface LibFile {
  entry_id: string; variant_id: string; position: number; caption: string; source_note: string; license_note: string;
  provenance: Record<string, unknown> | null; file_id: string; kind: "image" | "audio"; mime: string;
  width: number | null; height: number | null; duration_s: number | null; sha256: string; source: "upload" | "generated";
  bytes: number;
}
export interface Variant {
  id: string; asset_id: string; name: string; description: string; version: number; parent_id: string | null;
  locked: boolean; files: LibFile[];
}
export interface ImageJob {
  id: string; variant_id: string; template: string; prompt: string; count: number; status: string; phase: string | null;
  current_step: number | null; total_steps: number | null; queue_ahead: number | null; queue_running: QueueOwner | null;
  stalled: number; error: string | null; error_code: string | null; submitted_at: string; finished_at: string | null;
  label?: string | null; media?: "image" | "audio";
}
export interface Candidate {
  id: string; job_id: string; file_id: string; variant_id: string; seed: number | null; template: string;
  label?: string | null; media?: "image" | "audio";
}
/** The Creator (backend/app/creator.py): a draft asset the director plans and the owner generates and approves. */
export interface CreationPlan {
  name: string; description: string; tags: string; views: { label: string; prompt: string }[];
  voice: { description: string; line: string } | null;
  music: { style: string; lyrics: string; seconds: number } | null;
}
export interface Creation {
  id: string; asset_id: string; kind: AssetKind; brief: string; plan: CreationPlan | null; image_template: string;
  edit_template: string; status: "drafting" | "ready" | "approved" | "discarded"; error: string | null;
  created_at: string; updated_at: string; asset?: { id: string; name: string; kind: AssetKind } | null;
}
export interface ImageRequest {
  id: string; variant_id: string; requested_by: string; prompt: string; input_file_ids: string[]; count: number; note: string;
}
export interface AssetDetail extends Asset {
  voice?: { file_id: string; mime: string; duration_s: number | null; bytes: number } | null;
  variants: Variant[]; images: { jobs: ImageJob[]; candidates: Candidate[] }; image_requests: ImageRequest[];
}
export interface Capability {
  task: "gen_video" | "gen_video_with_refs" | "gen_image" | "edit_image" | "gen_speech_from_description" | "gen_song";
  template: string; model_type: string;
  refs: "none" | "single" | "multiple"; requires_refs: boolean; audio: boolean; start_end: boolean;
  valid: boolean; installed: boolean | null; reason: string | null; specialities: string[];
  default_resolution?: string; fps?: number | null; wangp_default?: boolean;
}
export interface TemplateSettings {
  video_refs: string; video_plain: string; image_text: string; image_refs: string; image_candidates: number;
  voice_design?: string; music?: string;
}
export interface Capabilities { built_at: string | null; templates: Capability[]; settings: TemplateSettings }
export interface GenerateRequest {
  prompt: string; template?: string; input_file_ids?: string[]; count?: number; resolution?: string; seed?: number;
}
export interface ShotRef {
  position: number; role: string; variant_id: string; file_id: string; variant_name: string; version: number;
  locked: number; asset_id: string; asset_name: string; kind: AssetKind;
}
export interface Usage {
  shot_id: string; position: number; title: string; status: string; project_id: string; project_title: string;
  locked?: number; variant_id?: string; variant_name?: string; version?: number;
}
export interface SwapResult {
  changed: { shot_id: string; project_title: string; position: number; title: string }[];
  skipped: { shot_id: string; project_title: string; position: number; title: string; reason: string }[];
}

/** The confirmation text for deleting a library item: which shots lose it, and what restore does. */
export function usageWarning(what: string, usage: Usage[]): string {
  const where = usage.length
    ? `\n\nIt's a reference in ${usage.length} shot${usage.length === 1 ? "" : "s"}, which will lose it:\n`
      + usage.map((u) => `• ${u.project_title}: shot ${u.position} “${u.title}”`).join("\n")
      + "\n\nTakes already rendered keep their record of what they used. Restoring doesn't put it back on these shots."
    : "";
  return `Move ${what} to Recently deleted? You can restore it from there.${where}`;
}
export interface Issue { level: "block" | "warn" | "info"; message: string }
export interface ShotChecks { template: string; follows_defaults: boolean; refs: number; issues: Issue[] }
type FileMeta = { caption?: string; source_note?: string; license_note?: string };
export interface Preset {
  id: string; name: string; template: string; resolution: string; frames: number; seconds: number; refs: string;
  use: string; tips: string; source: "filmlab" | "wangp" | "owner"; status: "ready" | "needs_download" | "unavailable";
  reason: string; audio: boolean; takes_refs: boolean;
}
export interface SuggestedLook { variant_id: string; name: string; version: number; file_id: string; why: string }
export interface Suggestion extends Asset { score: number; reasons: string[]; on_shot: boolean; look: SuggestedLook | null }
export interface Suggestions { context: { shot: string | null; scene: string | null; words: string[] }; assets: Suggestion[] }

export const lib = {
  search: (q = "", kind = "") => request<Asset[]>("GET", `/api/library?q=${encodeURIComponent(q)}&kind=${kind}`),
  suggest: (shotId: string, q = "") =>
    request<Suggestions>("GET", `/api/shots/${shotId}/library-suggestions?q=${encodeURIComponent(q)}`),
  presets: () => request<Preset[]>("GET", "/api/presets"),
  createPreset: (p: { name: string; template: string; resolution: string; frames: number; use?: string; tips?: string }) =>
    request<Preset>("POST", "/api/presets", p),
  deletePreset: (id: string) => request("DELETE", `/api/presets/${encodeURIComponent(id)}`),
  applyPreset: (shotId: string, presetId: string, keepLength = false) =>
    request("POST", `/api/shots/${shotId}/preset`, { preset_id: presetId, keep_length: keepLength }),
  createAsset: (a: Partial<Asset>) => request<AssetDetail>("POST", "/api/library/assets", a),
  asset: (id: string) => request<AssetDetail>("GET", `/api/library/assets/${id}`),
  updateAsset: (id: string, a: Partial<Asset>) => request<AssetDetail>("PATCH", `/api/library/assets/${id}`, a),
  createVariant: (assetId: string, name: string, description = "") =>
    request<Variant>("POST", `/api/library/assets/${assetId}/variants`, { name, description }),
  updateVariant: (id: string, v: { name?: string; description?: string }) =>
    request<Variant>("PATCH", `/api/library/variants/${id}`, v),
  lock: (id: string, locked: boolean) => request<Variant>("POST", `/api/library/variants/${id}/lock`, { locked }),
  newVersion: (id: string) => request<Variant>("POST", `/api/library/variants/${id}/new-version`),
  async upload(variantId: string, file: File, meta: FileMeta) {
    const q = new URLSearchParams(Object.entries(meta).filter(([, v]) => v) as [string, string][]);
    const res = await fetch(`/api/library/variants/${variantId}/files?${q}`, {
      method: "POST", credentials: "same-origin", body: file,
      headers: { "Content-Type": "application/octet-stream", ...csrfHeaders() },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
      throw new ApiError(res.status, `${file.name}: ${detail}`);
    }
    return res.json() as Promise<{ file_id: string; deduplicated: boolean; already_attached: boolean }>;
  },
  /** A character's voice sample (WAV, MP3 or FLAC, 2-30 s): its voice in every shot it speaks in. */
  async setVoice(assetId: string, file: File) {
    const res = await fetch(`/api/library/assets/${assetId}/voice`, {
      method: "PUT", credentials: "same-origin", body: file,
      headers: { "Content-Type": "application/octet-stream", ...csrfHeaders() },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
      throw new ApiError(res.status, `${file.name}: ${detail}`);
    }
    return res.json() as Promise<AssetDetail>;
  },
  clearVoice: (assetId: string) => request<AssetDetail>("DELETE", `/api/library/assets/${assetId}/voice`),
  updateEntry: (id: string, m: FileMeta) => request("PATCH", `/api/library/entries/${id}`, m),
  removeEntry: (id: string) => request("DELETE", `/api/library/entries/${id}`),
  assetUsage: (id: string) => request<Usage[]>("GET", `/api/library/assets/${id}/usage`),
  variantUsage: (id: string) => request<Usage[]>("GET", `/api/library/variants/${id}/usage`),
  swapLook: (assetId: string, toVariantId: string, fromVariantId?: string) => request<SwapResult>("POST",
    `/api/library/assets/${assetId}/swap-look`, { to_variant_id: toVariantId, from_variant_id: fromVariantId ?? null }),
  deleteAsset: (id: string) => request("DELETE", `/api/library/assets/${id}`),
  deleteVariant: (id: string) => request("DELETE", `/api/library/variants/${id}`),
  fileUrl: (id: string, thumb = false) => `/api/library/files/${id}${thumb ? "?thumb=1" : ""}`,
  generate: (variantId: string, g: GenerateRequest) =>
    request<ImageJob>("POST", `/api/library/variants/${variantId}/generate`, g),
  creations: () => request<Creation[]>("GET", "/api/creations"),
  creation: (id: string) => request<Creation>("GET", `/api/creations/${id}`),
  startCreation: (c: { kind: AssetKind; brief: string; image_template?: string; edit_template?: string; voice?: boolean }) =>
    request<Creation>("POST", "/api/creations", c),
  updateCreationPlan: (id: string, plan: CreationPlan) => request<Creation>("PUT", `/api/creations/${id}/plan`, { plan }),
  redraftCreation: (id: string, voice = true) => request<Creation>("POST", `/api/creations/${id}/redraft`, { voice }),
  generateCreation: (id: string, part: "anchor" | "views" | "voice" | "music", count?: number) =>
    request("POST", `/api/creations/${id}/generate`, { part, ...(count ? { count } : {}) }),
  approveCreation: (id: string) => request<Creation>("POST", `/api/creations/${id}/approve`),
  discardCreation: (id: string) => request<Creation>("POST", `/api/creations/${id}/discard`),
  keep: (id: string, meta: FileMeta = {}) => request("POST", `/api/library/candidates/${id}/keep`, meta),
  discard: (id: string) => request("POST", `/api/library/candidates/${id}/discard`),
  cancelImageJob: (id: string) => request("POST", `/api/image-jobs/${id}/cancel`),
  approveRequest: (id: string) => request<ImageJob>("POST", `/api/library/image-requests/${id}/approve`),
  rejectRequest: (id: string) => request("POST", `/api/library/image-requests/${id}/reject`),
  capabilities: (refresh = false) => request<Capabilities>("GET", `/api/capabilities${refresh ? "?refresh=true" : ""}`),
  updateSettings: (s: Partial<TemplateSettings>) => request<TemplateSettings>("PUT", "/api/settings/templates", s),
  projectModels: (pid: string, m: { video_refs_template?: string; video_plain_template?: string }) =>
    request("PATCH", `/api/projects/${pid}/models`, m),
  setShotRefs: (shotId: string, refs: { variant_id: string; file_id?: string; role?: string }[]) =>
    request<ShotRef[]>("PUT", `/api/shots/${shotId}/refs`, { refs }),
  shotChecks: (shotId: string) => request<ShotChecks>("GET", `/api/shots/${shotId}/checks`),
};

export const KIND_LABEL: Record<AssetKind, string> = {
  character: "Character", location: "Location", prop: "Prop", style: "Style", sound: "Sound",
};

/** A template is offered for a setting only if WanGP accepts it; files not downloaded is shown, not hidden. */
export function templateLabel(c: Capability) {
  if (!c.valid) return `${c.template} — unusable: ${c.reason ?? "rejected by WanGP"}`;
  return c.installed === false ? `${c.template} — downloads on first use` : c.template;
}
