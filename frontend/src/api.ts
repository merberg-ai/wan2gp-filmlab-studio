// Typed client for the FilmLab backend. Mutations carry the CSRF token.

import type { Asset, Issue, ShotRef } from "./library-api";

export type ShotStatus =
  | "draft" | "pending_approval" | "approved" | "queued"
  | "rendering" | "ready" | "failed" | "cancelled";

export interface Project {
  id: string; title: string; logline: string; updated_at: string; shot_count?: number;
  /** For the Projects page's card */
  shots_rendered?: number; seconds?: number; rendering?: boolean; template?: string | null; music_mode?: string | null;
  created_at?: string;
  cover?: { kind: "take" | "ref"; thumb: string; video?: string; take_id?: string; picked?: boolean } | null;
  cover_take_id?: string | null;
}
export interface Shot {
  id: string; project_id: string; position: number; title: string; description: string; prompt: string;
  template: string; model_type: string; resolution: string; frames: number; seed: number;
  status: ShotStatus; created_by: "owner" | "director"; updated_at: string;
  template_explicit?: boolean; refs?: ShotRef[]; idea?: string;
  scene_id?: string | null; locked?: boolean; transition?: string; rationale?: string; selected_take_id?: string | null;
  sound?: ShotSound | null;
}
/** Who says which exact words (lines: [] = nobody speaks), the place's sounds and music. FilmLab writes it into the
 *  video model's own script format (backend/app/sound.py). */
export interface ShotSound {
  lines: { speaker: string; text: string; delivery: string; language: string }[];
  ambience: string; music: string; refine: boolean;
}
export interface Segment {
  kind: "clip" | "title"; frames: number; start_s: number; transition: string; transition_s: number;
  shot_id?: string; position?: number; title?: string; take_id?: string; media_id?: string;
  src?: { width: number; height: number; fps: number; frames: number; has_audio: boolean };
  in?: number; out?: number; gain_db?: number; mute?: boolean;
  id?: string; text?: string; text_position?: string;
}
export interface Music { id?: string; file_id: string; start_s: number; offset_s: number; gain_db: number; fade_in_s: number; fade_out_s: number; duck: boolean; loop?: boolean }
export interface TextItem {
  id?: string; kind: "title" | "caption"; text: string; position?: string;
  at?: string; duration_s?: number; start_s?: number; end_s?: number;
}
export interface EditDoc {
  clips: Record<string, { in?: number; out?: number; skip?: boolean; gain_db?: number; mute?: boolean; transition_s?: number }>;
  music: Music[]; texts: TextItem[]; settings: { loudness: boolean; transition_s: number; mute_shots?: boolean };
}
export interface Timeline {
  project_id: string; fps: number; width: number; height: number; segments: Segment[]; total_s: number; doc: EditDoc;
  issues: string[]; suggestions: { id: string; change: Record<string, unknown>; note: string; created_at: string }[];
  revisions: { id: string; actor: string; note: string; created_at: string }[];
}
export interface ExportRow {
  id: string; project_id: string; kind: "draft" | "final"; number: number | null; status: string; mode: string;
  progress: number | null; error: string | null; sha256: string | null; bytes: number | null; duration_s: number | null;
  srt: number; verify_status: string | null; verify_detail: string | null; verified_at: string | null;
  created_at: string; finished_at: string | null;
  summary: { total_s: number; width: number; height: number; fps: number; clips: number; titles: number; music: number;
    captions: number; burn_captions: boolean; loudness: boolean; mode: string };
}
export interface Scene {
  id: string; project_id: string; position: number; title: string; goal: string; time_place: string; mood: string;
  location_asset_id: string | null; created_by: string;
}
export interface BatchItem {
  batch_id: string; position: number; shot_id: string; status: "pending" | "rendering" | "done" | "failed" | "skipped" | "cancelled";
  job_id: string | null; error: string | null; title: string; shot_position: number;
}
export interface Batch {
  id: string; project_id: string; status: "running" | "paused" | "done" | "cancelled"; pause_reason: string | null;
  queue_cap: number; requested_by: string; created_at: string; finished_at: string | null; items: BatchItem[];
  preview?: number | boolean;
  /** Waiting (paused) until this time, then it starts by itself. */
  start_at?: string | null;
}
export interface BulkResult {
  action: string; done: string[]; skipped: { shot_id: string; position: number; title: string; reason: string }[];
  scenes_deleted?: string[]; undo?: string | null; undo_s?: number;
}
/** Something in the brief that disagrees with the project's settings, found before planning. */
export interface PlanIssue {
  id: string; level: "warn" | "info"; message: string;
  fixes: { label: string; patch: Partial<Pick<ProjectDetail, "target_seconds" | "style">> }[];
}
export interface BatchRequest { id: string; shot_ids: string[]; note: string; created_at: string }
export interface Revision {
  id: string; shot_id: string; actor: string; kind: string; rationale: string; created_at: string;
  before: Record<string, unknown>; after: Record<string, unknown>; changed: string[];
  /** The takes rendered from this version (submitted before the next change). */
  takes?: { id: string; media_id: string; thumb_media_id: string | null; verdict: string | null; preview: boolean }[];
}
export interface ReviewRow {
  shot_id: string; position: number; title: string; status: string; locked: boolean; takes: number; template: string;
  frames: number; model_type: string; issues: { level: "block" | "warn"; message: string }[];
  renderable: boolean; blocked: boolean; default: boolean;
}
export interface BatchReview {
  shots: ReviewRow[]; rates: Record<string, { s_per_frame: number; samples: number }>;
  queue: { total: number; filmlab: number; other: number; running: number } | null;
}
export interface ErrorInfo { code: string; message: string; hint: string }
/** Who owns the task WanGP is running: this job, another FilmLab job, another MCP agent, or the WanGP UI. */
export type QueueOwner = "this" | "filmlab" | "agent" | "ui";
export interface Job {
  id: string; shot_id: string; status: string; phase: string | null; current_step: number | null;
  total_steps: number | null; recovered: number; error: string | null; submitted_at: string; finished_at: string | null;
  started_at?: string | null;
  error_info?: ErrorInfo | null; queue_ahead?: number | null; queue_running?: QueueOwner | null; stalled?: boolean;
  /** A quick low-resolution render (half width and height) to judge the shot before full quality. */
  preview?: boolean;
  /** WanGP's own status line, e.g. "Downloading model files (2/5)" while it fetches a model first. */
  status_text?: string | null;
}
/** While WanGP downloads a model for a render: what has arrived in its model folder since, and how fast. */
export interface DownloadProgress { bytes: number; speed: number }
export const isDownloading = (j: { status_text?: string | null }) => /download/i.test(j.status_text ?? "");

/** A music video's setup (backend/app/music_video.py): the song and how shots follow it. */
export type MusicMode = "lipsync" | "soundtrack";
export interface LipsyncModel {
  template: string; model_type: string; installed: boolean | null; refs: string; suggested: boolean; note: string;
}
export interface SongCandidate { id: string; job_id: string; file_id: string; created_at: string }
export interface MusicVideoSetup {
  mode: MusicMode | null; lyrics: string; style: string; speech_template: string | null;
  song: { file_id: string; duration_s: number | null; mime: string } | null;
  song_asset_id: string | null; song_files: { file_id: string; caption: string; duration_s?: number | null }[];
  song_candidates: SongCandidate[];
  song_jobs: {
    id: string; status: string; phase: string | null; error: string | null; submitted_at: string; count: number;
    current_step: number | null; total_steps: number | null; queue_ahead: number | null; stalled: number | boolean | null;
  }[];
  library_songs: { file_id: string; name: string; duration_s: number | null }[];
  lipsync_models: LipsyncModel[]; song_models: { template: string; installed: boolean | null }[]; song_model: string;
}
/** One line of the Console (backend/app/console.py). */
export interface ConsoleLine {
  id: number; ts: string; level: "info" | "warn" | "error"; source: string; message: string; project_id: string | null;
  has_detail: boolean;
}
export interface WanGPWebUI { found: boolean; url?: string | null; port?: number; host_is_local?: boolean }
/** The Windows helper (scripts/filmlab-host.ps1): WanGP's folder, launcher, ports and the startup settings. */
export interface HostStatus {
  available: boolean; helper_running: boolean; wangp_connected: boolean; updated_at?: string;
  wangp?: { found: boolean; path: string | null; found_by: string | null; launcher: string | null; python: string | null;
    mcp_port: number | null; webui_port: number | null; mcp_enabled: boolean | null; running: boolean; process: boolean;
    starting: boolean; started_by_filmlab: boolean };
  settings?: { wangp_autostart: boolean; wangp_restart: boolean; start_at_logon: boolean; wangp_path_chosen: string | null };
  note?: string | null; error?: string | null;
}
export type HostSettings = Partial<{ wangp_autostart: boolean; wangp_restart: boolean; start_at_logon: boolean; wangp_path: string }>;
export interface Provenance {
  request_id: string; wangp_job_id: string; wangp_media_id: string; sha256: string;
  submitted_at: string; ingested_at: string; mcp_server?: { name?: string; version?: string };
  seed?: number; model_type?: string; model_file?: string; wangp_version?: string; steps?: number;
  generation_s?: number; filename?: string; settings_error?: string;
  requested?: { prompt?: string };
  references?: { position: number; asset_name: string; variant_name: string; version: number }[];
}
export interface Take {
  id: string; shot_id: string; job_id: string; media_id: string; thumb_media_id: string | null; created_at: string;
  width: number; height: number; fps: number; frames: number; duration_s: number; has_audio: number; bytes: number;
  provenance?: Provenance | null; verdict?: "good" | "maybe" | "reject" | null; notes?: string;
  preview?: boolean;
}
export interface Inspection {
  status: "running" | "done" | "failed"; verdict: "ok" | "flagged" | null; summary: string | null; sheet: boolean;
  vision_error?: string | null; vision_summary?: string | null; vision_skipped?: string | null;
}
/** Take inspection: technical checks always run on this PC; the vision check's four keyframes go nowhere (off), to an
 *  Ollama model on this PC (local), or to an Ollama cloud model (cloud: they leave this PC). */
export interface InspectionSettings {
  auto_inspect: boolean; vision_model: string; vision: "off" | "local" | "cloud"; vision_model_kind: "local" | "cloud";
}
export interface RedoProposal {
  id: string; shot_id: string; take_id: string | null; run_id: string | null; changes: { prompt?: string; seed?: number };
  reason: string; status: string; outcome: string | null; created_at: string; title: string; position: number; current_prompt: string;
}
export interface RedoRun {
  id: string; status: "running" | "paused" | "stopped" | "done"; shot_ids: string[]; created_at: string;
  caps: { max_retries: number; max_renders: number; gpu_minutes: number; wall_minutes: number };
  used_renders: number; used_gpu_min: number; stop_reason: string | null; log: { at: string; shot_id: string | null; text: string }[];
  shot_state: Record<string, { state?: string; attempts?: number; note?: string }>;
}
export interface RedoState {
  proposals: RedoProposal[]; run: RedoRun | null; hold: string | null;
  needs_redo: { shot_id: string; position: number; title: string; why: string; take_id: string | null }[];
}
export interface SystemHealth {
  at: string; ok: boolean; problems: string[];
  database: { integrity: string; foreign_key_problems: number };
  files: { referenced: number; missing: string[]; orphans: string[]; orphan_bytes: number; deleted_deliveries: number;
    deleted_delivery_bytes: number };
  storage: { free_gb: number; total_gb: number; takes_mb: number; deliveries_mb: number; library_mb: number; caches_mb: number;
    database_mb: number; backups_mb: number };
  jobs: { active: number; checked: boolean; error?: string; wangp_tasks?: number; filmlab_tasks?: number;
    stale: { kind: string; id: string; status: string }[]; unknown_in_wangp: { client_id: string; status: string }[] };
  startup: { manual: boolean; units: Record<string, { active: string; enabled_at_boot: boolean }> };
  gpu: { share: string; ollama_loaded: { name: string; vram_gb: number }[]; vram: { gpu: string; used_gb: number; total_gb: number } | null };
  settings: { warn_free_gb: number; min_free_gb: number; gpu_share: string };
}
export interface BackupRow {
  name: string; kind: string; created_at: string; version: string; files: number; bytes: number; new_bytes: number; note?: string;
  counts?: Record<string, number>;
  verified?: { ok: boolean; deep: boolean; at: string; problems: string[] } | null;
  drill?: { ok: boolean; at: string; detail: string } | null;
}
export interface ProjectTemplate {
  id: string; name: string; description: string; target_seconds: number; default_frames: number; default_resolution: string;
  brief: string; style: string;
}
export interface SheetShot {
  shot_id: string; position: number; title: string; seconds: number; transition: string; take_id: string | null; sheet: boolean;
  inspection: { verdict: string | null; summary: string | null; setting_seen: string | null; characters: string[] } | null;
}
export interface Approval { id: string; shot_id: string; requested_by: string; note: string; created_at: string }
export interface ProjectDetail extends Project {
  cast: Asset[]; video_refs_template: string | null; video_plain_template: string | null;
  default_resolution: string; default_frames: number; brief: string; target_seconds: number | null;
  scenes: Scene[]; batch: Batch | null; batch_requests: BatchRequest[];
  render_rates?: Record<string, { s_per_frame: number; samples: number }>;
  autonomy: "A0" | "A1" | "A2" | "A3"; inspections: Record<string, Inspection>; redo: RedoState;
  template: string | null; style: string;
  music_mode?: MusicMode | null; song_file_id?: string | null; lyrics?: string; song_style?: string;
  speech_template?: string | null;
  shots: Shot[]; jobs: Job[]; takes: Take[]; approvals: Approval[]; director_busy: boolean; last_event_id: number;
  /** A plan in parts being written right now: parts saved so far and the seconds of film they add up to. */
  planning?: { turn_id: string; started_at: string; parts: number; seconds: number; scenes: number } | null;
  /** Enhance all, while it runs (backend/app/enhance_all.py). */
  enhancing?: {
    started_at: string; total: number; done: number; failed: number; skipped: number; finished: number; shot_ids: string[];
    current: { shot_id: string; position: number; title: string } | null;
  } | null;
}
/** A shot's pre-render problem, as a render would report it. */
export interface ShotIssue { level: "block" | "warn"; message: string; kind?: string }
export type TrashKind = "project" | "shot" | "take" | "asset" | "variant";
export interface Trash {
  projects: { id: string; title: string; logline: string; deleted_at: string; shots: number }[];
  shots: { id: string; position: number; title: string; deleted_at: string; project_id: string; project_title: string; takes: number }[];
  takes: { id: string; verdict: string | null; deleted_at: string; thumb_media_id: string | null; shot_id: string; position: number;
    title: string; project_id: string; project_title: string }[];
  assets: { id: string; kind: string; name: string; deleted_at: string; cover_file_id: string | null }[];
  variants: { id: string; name: string; version: number; deleted_at: string; asset_id: string; asset_name: string; kind: string; cover_file_id: string | null }[];
}
export interface ChatMessage {
  id: number; role: "user" | "assistant" | "error" | "note"; content: string; created_at: string;
  steps?: { name: string; arguments?: string; ok: boolean | null }[];
}
export interface Template {
  template: string; model_type: string; default: boolean; image_inputs: string[]; has_audio: boolean;
  task?: "gen_video" | "gen_video_with_refs";
}
export interface Validation {
  valid: boolean; diagnostics: { severity: string; message: string }[];
  canvas: { width: number; height: number } | null; requested_video: { frames: number; fps: number; duration_seconds?: number } | null;
  windows: number; available: boolean | null;
  template?: string; refs?: number; issues?: Issue[];
}
export interface Health {
  version?: string;
  wangp: {
    connected: boolean; queue_total?: number; queue_running?: number; queue_filmlab?: number; queue_other?: number;
    workspace?: string; error?: string;
  };
  director: { connected: boolean; agent: string };
}

export interface QueueSummary { total: number; filmlab: number; other: number; running: number }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let csrf: string | null = null;

export const csrfHeaders = (): Record<string, string> => (csrf ? { "X-CSRF-Token": csrf } : {});

export async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  const res = await fetch(url, {
    method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export interface DirectorSettings {
  notes: string; reply_style: "short" | "standard" | "detailed"; chat_rounds: number; plan_rounds: number;
  default_autonomy: "A0" | "A1" | "A2" | "A3"; model: string;
}
export interface DirectorModel { id: string; name: string; cloud: boolean; size: string | null; vision: boolean }
export interface DirectorTest { ok: boolean; reply: string; seconds: number; error: string | null; model: string | null }
export interface DirectorOverview {
  settings: DirectorSettings; connected: boolean; agent: string; model: string | null; source: string;
  instructions: { name: string; text: string }[]; tools: string[];
}

export const api = {
  async me(): Promise<boolean> {
    try { csrf = (await request<{ csrf_token: string }>("GET", "/api/auth/me")).csrf_token; return true; }
    catch (e) { if (e instanceof ApiError && e.status === 401) return false; throw e; }
  },
  async login(username: string, password: string) {
    csrf = (await request<{ csrf_token: string }>("POST", "/api/auth/login", { username, password })).csrf_token;
  },
  logout: () => request("POST", "/api/auth/logout"),
  health: () => request<Health>("GET", "/api/health"),
  queue: () => request<QueueSummary>("GET", "/api/queue"),
  projects: () => request<Project[]>("GET", "/api/projects"),
  createProject: (title: string, logline: string, template?: string) =>
    request<Project>("POST", "/api/projects", { title, logline, template: template || null }),
  projectTemplates: () => request<ProjectTemplate[]>("GET", "/api/project-templates"),
  contactSheet: (pid: string) => request<{ scenes: { scene_id: string | null; title: string; time_place: string | null; shots: SheetShot[] }[];
    checks: { where: string; level: string; text: string }[] }>("GET", `/api/projects/${pid}/contact-sheet`),
  systemHealth: () => request<SystemHealth>("GET", "/api/system/health"),
  systemSettings: (s: { warn_free_gb?: number; min_free_gb?: number; gpu_share?: string }) => request("PUT", "/api/system/settings", s),
  clearCaches: () => request<{ freed_mb: number }>("POST", "/api/system/clear-caches"),
  removeDeletedDeliveries: () => request<{ removed: number; freed_mb: number }>("POST", "/api/system/remove-deleted-deliveries"),
  removeOrphans: (files: string[]) => request<{ removed: number; freed_mb: number }>("POST", "/api/system/remove-orphans", { files }),
  backups: () => request<{ backups: BackupRow[]; settings: { daily: boolean; keep: number; copy_dir: string }; dir: string }>("GET", "/api/backups"),
  createBackup: (note = "") => request<BackupRow>("POST", "/api/backups", { note }),
  backupSettings: (s: { daily?: boolean; keep?: number; copy_dir?: string }) => request("PUT", "/api/backups/settings", s),
  verifyBackup: (name: string, deep: boolean) =>
    request<{ ok: boolean; checked_files: number; hashed: number; problems: string[] }>("POST", `/api/backups/${name}/verify?deep=${deep}`),
  drillBackup: (name: string) => request<{ ok: boolean; detail: string }>("POST", `/api/backups/${name}/drill`),
  deleteBackup: (name: string) => request("DELETE", `/api/backups/${name}`),
  project: (id: string) => request<ProjectDetail>("GET", `/api/projects/${id}`),
  templates: () => request<Template[]>("GET", "/api/templates"),
  createShot: (pid: string, s: Partial<Shot>) => request<Shot>("POST", `/api/projects/${pid}/shots`, s),
  updateShot: (id: string, s: Partial<Shot>) => request<Shot>("PATCH", `/api/shots/${id}`, s),
  updateProject: (id: string, p: { title?: string; logline?: string; default_resolution?: string; default_frames?: number;
    brief?: string; target_seconds?: number | null; autonomy?: string; style?: string | null }) =>
    request<ProjectDetail>("PATCH", `/api/projects/${id}`, p),
  deleteProject: (id: string) => request("DELETE", `/api/projects/${id}`),
  deleteShot: (id: string) => request("DELETE", `/api/shots/${id}`),
  deleteTake: (id: string) => request("DELETE", `/api/takes/${id}`),
  createScene: (pid: string, s: Partial<Scene>) => request<Scene>("POST", `/api/projects/${pid}/scenes`, s),
  updateScene: (id: string, s: Partial<Scene>) => request<Scene>("PATCH", `/api/scenes/${id}`, s),
  moveScene: (id: string, index: number) => request("POST", `/api/scenes/${id}/move`, { index }),
  deleteScene: (id: string) => request<{ shots_deleted: number }>("DELETE", `/api/scenes/${id}`),
  moveShot: (id: string, sceneId: string | null, index?: number) =>
    request<Shot>("POST", `/api/shots/${id}/move`, { scene_id: sceneId, index: index ?? null }),
  lockShot: (id: string, locked: boolean) => request<Shot>("POST", `/api/shots/${id}/lock`, { locked }),
  revisions: (id: string) => request<Revision[]>("GET", `/api/shots/${id}/revisions`),
  restoreRevision: (id: string) => request<Shot>("POST", `/api/revisions/${id}/restore`),
  bulk: (pid: string, b: { action: "delete" | "lock" | "unlock" | "set"; shot_ids?: string[]; scene_ids?: string[];
    fields?: { template?: string; resolution?: string; frames?: number } }) => request<BulkResult>("POST", `/api/projects/${pid}/bulk`, b),
  bulkUndo: (pid: string, token: string) => request("POST", `/api/projects/${pid}/bulk/undo`, { token }),
  planCheck: (pid: string) => request<{ issues: PlanIssue[] }>("GET", `/api/projects/${pid}/plan-check`),
  plan: (pid: string, replaceDrafts: boolean) => request("POST", `/api/projects/${pid}/plan`, { replace_drafts: replaceDrafts }),
  batchReview: (pid: string) => request<BatchReview>("GET", `/api/projects/${pid}/batch-review`),
  createBatch: (pid: string, shotIds: string[], requestId?: string, preview = false, startAt: string | null = null) =>
    request<Batch>("POST", `/api/projects/${pid}/batches`, { shot_ids: shotIds, request_id: requestId ?? null, preview, start_at: startAt }),
  moveBatchItem: (id: string, position: number, direction: "up" | "down") =>
    request<Batch>("POST", `/api/batches/${id}/items/${position}/move`, { direction }),
  batchAction: (id: string, action: "pause" | "resume" | "cancel") => request<Batch>("POST", `/api/batches/${id}/${action}`),
  retryItem: (id: string, position: number) => request<Batch>("POST", `/api/batches/${id}/items/${position}/retry`),
  rejectBatchRequest: (id: string) => request("POST", `/api/batch-requests/${id}/reject`),
  reviewTake: (id: string, r: { verdict?: string; notes?: string }) => request<Take>("POST", `/api/takes/${id}/review`, r),
  selectTake: (shotId: string, takeId: string | null) => request<Shot>("POST", `/api/shots/${shotId}/select-take`, { take_id: takeId }),
  edit: (pid: string) => request<Timeline>("GET", `/api/projects/${pid}/edit`),
  patchEdit: (pid: string, patch: Partial<EditDoc> & { note?: string }) => request<Timeline>("PATCH", `/api/projects/${pid}/edit`, patch),
  restoreEdit: (revId: string) => request<Timeline>("POST", `/api/edit-revisions/${revId}/restore`),
  decideSuggestion: (id: string, accept: boolean) => request("POST", `/api/edit-suggestions/${id}/${accept ? "accept" : "reject"}`),
  exports: (pid: string) => request<ExportRow[]>("GET", `/api/projects/${pid}/exports`),
  startExport: (pid: string, o: { kind: "draft" | "final"; burn_captions: boolean; srt: boolean; loudness: boolean }) =>
    request<ExportRow>("POST", `/api/projects/${pid}/exports`, o),
  exportAction: (id: string, action: "cancel" | "verify") => request<ExportRow>("POST", `/api/exports/${id}/${action}`),
  deleteExport: (id: string) => request("DELETE", `/api/exports/${id}`),
  exportFile: (id: string, what: "mp4" | "srt" | "manifest") => `/api/exports/${id}/file/${what}`,
  waveformUrl: (mediaId: string) => `/api/media/${mediaId}/waveform`,
  inspectTake: (takeId: string) => request("POST", `/api/takes/${takeId}/inspect`),
  sheetUrl: (takeId: string) => `/api/takes/${takeId}/sheet`,
  acceptRedo: (id: string) => request("POST", `/api/redo-proposals/${id}/accept`),
  rejectRedo: (id: string) => request("POST", `/api/redo-proposals/${id}/reject`),
  startRun: (pid: string, body: { shot_ids?: string[]; caps?: Record<string, number> }) =>
    request<RedoRun>("POST", `/api/projects/${pid}/runs`, body),
  runAction: (id: string, action: "pause" | "resume" | "stop") => request<RedoRun>("POST", `/api/runs/${id}/${action}`),
  setGpuHold: (on: boolean, cancel: boolean) => request("POST", "/api/gpu-hold", { on, cancel }),
  director: () => request<DirectorOverview>("GET", "/api/director"),
  putDirectorSettings: (p: Partial<DirectorSettings>) => request<DirectorSettings>("PUT", "/api/director/settings", p),
  testDirector: (model?: string) => request<DirectorTest>("POST", "/api/director/test", model === undefined ? undefined : { model }),
  directorModels: () => request<DirectorModel[]>("GET", "/api/director/models"),
  enhanceAll: (projectId: string, shotIds: string[]) =>
    request<{ total: number }>("POST", `/api/projects/${projectId}/enhance-all`, { shot_ids: shotIds }),
  stopEnhanceAll: (projectId: string) => request<{ stopped: boolean }>("POST", `/api/projects/${projectId}/enhance-all/stop`),
  stopChat: (projectId: string) => request<{ stopped: boolean }>("POST", `/api/projects/${projectId}/chat/stop`),
  fixPrompts: (pid: string, shotIds?: string[]) =>
    request<{ turn_id: string; shots: string[] }>("POST", `/api/projects/${pid}/fix-prompts`, { shot_ids: shotIds ?? null }),
  projectChecks: (pid: string) => request<{ shots: Record<string, ShotIssue[]> }>("GET", `/api/projects/${pid}/checks`),
  freshChat: (projectId: string) => request<{ conversation: number }>("POST", `/api/projects/${projectId}/chat/fresh`),
  autonomySettings: () => request<InspectionSettings>("GET", "/api/settings/autonomy"),
  putAutonomySettings: (s: Partial<Pick<InspectionSettings, "auto_inspect" | "vision_model" | "vision">>) =>
    request<InspectionSettings>("PUT", "/api/settings/autonomy", s),
  filmstripUrl: (mediaId: string) => `/api/media/${mediaId}/filmstrip`,
  trash: () => request<Trash>("GET", "/api/trash"),
  restore: (kind: TrashKind, id: string) => request("POST", `/api/trash/${kind}/${id}/restore`),
  purge: (kind: TrashKind, id: string) => request<{ files_removed: number }>("DELETE", `/api/trash/${kind}/${id}`),
  setCover: (projectId: string, takeId: string | null) =>
    request<{ cover_take_id: string | null }>("PUT", `/api/projects/${projectId}/cover`, { take_id: takeId }),
  musicVideo: (projectId: string) => request<MusicVideoSetup>("GET", `/api/projects/${projectId}/music-video`),
  updateMusicVideo: (projectId: string, patch: Partial<{ mode: MusicMode | null; lyrics: string; style: string;
    speech_template: string | null; song_file_id: string | null }>) =>
    request<MusicVideoSetup>("PATCH", `/api/projects/${projectId}/music-video`, patch),
  generateSong: (projectId: string, body: { lyrics: string; style: string; seconds: number; template?: string }) =>
    request<{ song_asset_id: string }>("POST", `/api/projects/${projectId}/music-video/song`, body),
  useSongCandidate: (projectId: string, candidateId: string) =>
    request<MusicVideoSetup>("POST", `/api/projects/${projectId}/music-video/song/candidates/${candidateId}/use`),
  songAsset: (projectId: string) =>
    request<{ asset_id: string; variant_id: string }>("POST", `/api/projects/${projectId}/music-video/song-asset`),
  wangpWebUI: () => request<WanGPWebUI>("GET", "/api/wangp/webui"),
  consoleLines: (q: { before_id?: number; limit?: number; level?: string; source?: string; project_id?: string; q?: string }) =>
    request<ConsoleLine[]>("GET", `/api/console?${new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]))}`),
  consoleEntry: (id: number) => request<ConsoleLine & { detail: string | null }>("GET", `/api/console/${id}`),
  host: () => request<HostStatus>("GET", "/api/host"),
  startWanGP: () => request("POST", "/api/host/wangp/start"),
  detectWanGP: () => request("POST", "/api/host/detect"),
  hostSettings: (s: HostSettings) => request("PATCH", "/api/host/settings", s),
  emptyTrash: () => request<{ purged: Record<string, number>; files_removed: number; failed: { kind: string; error: string }[] }>(
    "POST", "/api/trash/empty"),
  enhanceShot: (id: string) => request<{ turn_id: string }>("POST", `/api/shots/${id}/enhance`),
  validateShot: (id: string) => request<Validation>("POST", `/api/shots/${id}/validate`),
  approveAndRender: (id: string, preview = false) => request<Job>("POST", `/api/shots/${id}/approve-and-render`, { preview }),
  rejectShot: (id: string) => request<Shot>("POST", `/api/shots/${id}/reject`),
  cancelJob: (id: string) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  cancelAllRenders: (projectId: string) =>
    request<{ batches: number; jobs: number }>("POST", `/api/projects/${projectId}/renders/cancel-all`),
  chat: (pid: string) => request<ChatMessage[]>("GET", `/api/projects/${pid}/chat`),
  sendChat: (pid: string, text: string) => request<{ turn_id: string }>("POST", `/api/projects/${pid}/chat`, { text }),
  mediaUrl: (id: string) => `/api/media/${id}`,
};

export const seconds = (frames: number) => ((frames - 1) / 24).toFixed(2).replace(/\.?0+$/, "");

/** Transitions into the next shot, grouped for the pickers. The names match backend/app/shots.py TRANSITIONS. */
export const TRANSITION_GROUPS: { group: string; items: [string, string][] }[] = [
  { group: "Basic", items: [["cut", "Cut"], ["dissolve", "Dissolve"], ["fade", "Fade through black"], ["fade_white", "Fade through white"]] },
  { group: "Wipe", items: [["wipe_left", "Wipe left"], ["wipe_right", "Wipe right"], ["wipe_up", "Wipe up"], ["wipe_down", "Wipe down"],
    ["soft_wipe_left", "Soft wipe left"], ["soft_wipe_right", "Soft wipe right"]] },
  { group: "Slide", items: [["slide_left", "Slide left"], ["slide_right", "Slide right"], ["slide_up", "Slide up"], ["slide_down", "Slide down"]] },
  { group: "Shape", items: [["circle_open", "Circle open"], ["circle_close", "Circle close"], ["clock_wipe", "Clock wipe"]] },
  { group: "Effect", items: [["zoom_in", "Zoom in"], ["blur", "Blur"], ["pixelize", "Pixelate"], ["squeeze", "Squeeze"]] },
];
const TRANSITION_LABEL = Object.fromEntries(TRANSITION_GROUPS.flatMap((g) => g.items));
export const transitionLabel = (t: string) => TRANSITION_LABEL[t] ?? t.replace(/_/g, " ");
