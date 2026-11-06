-- F3: reference library (shared across projects), shot references, image generation, model settings.

-- Library files are global (not tied to a project), deduplicated by content.
CREATE TABLE library_files (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- image | audio
    rel_path TEXT NOT NULL,
    thumb_rel_path TEXT,
    sha256 TEXT NOT NULL UNIQUE,
    bytes INTEGER NOT NULL,
    mime TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    duration_s REAL,
    source TEXT NOT NULL,               -- upload | generated
    created_at TEXT NOT NULL
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- character | location | prop | style
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- A look / state of an asset. A locked variant is immutable; changing it makes version + 1.
CREATE TABLE variants (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    parent_id TEXT REFERENCES variants(id),
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX variants_asset ON variants(asset_id);

CREATE TABLE variant_files (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES variants(id),
    file_id TEXT NOT NULL REFERENCES library_files(id),
    position INTEGER NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    source_note TEXT NOT NULL DEFAULT '',
    license_note TEXT NOT NULL DEFAULT '',
    provenance TEXT,                    -- generated images: prompt, model, seed, inputs, WanGP record
    created_at TEXT NOT NULL,
    UNIQUE (variant_id, file_id)
);

CREATE TABLE project_cast (
    project_id TEXT NOT NULL REFERENCES projects(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_id)
);

-- Ordered references of a shot: position N is <Picture N> in the prompt.
CREATE TABLE shot_refs (
    shot_id TEXT NOT NULL REFERENCES shots(id),
    position INTEGER NOT NULL,
    variant_id TEXT NOT NULL REFERENCES variants(id),
    file_id TEXT NOT NULL REFERENCES library_files(id),
    role TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (shot_id, position)
);

-- Gallery ids of library files already uploaded to FilmLab's WanGP workspace.
CREATE TABLE wangp_uploads (
    file_id TEXT NOT NULL REFERENCES library_files(id),
    workspace_id TEXT NOT NULL,
    wangp_media_id TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (file_id, workspace_id)
);

-- Image generations for a variant (same lifecycle columns as render_jobs).
CREATE TABLE image_jobs (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    variant_id TEXT NOT NULL REFERENCES variants(id),
    request_id TEXT NOT NULL UNIQUE,
    wangp_job_id TEXT,
    client_ids TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL,
    template TEXT NOT NULL,
    prompt TEXT NOT NULL,
    count INTEGER NOT NULL,
    input_file_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    phase TEXT,
    current_step INTEGER,
    total_steps INTEGER,
    recovered INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    error_code TEXT,
    queue_ahead INTEGER,
    queue_running TEXT,
    last_progress_at TEXT,
    stalled INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE image_candidates (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES image_jobs(id),
    file_id TEXT NOT NULL REFERENCES library_files(id),
    wangp_media_id TEXT,
    provenance TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',   -- candidate | kept | discarded
    created_at TEXT NOT NULL
);

-- The director may propose an image generation; only the owner starts one.
CREATE TABLE image_requests (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES variants(id),
    requested_by TEXT NOT NULL,
    prompt TEXT NOT NULL,
    template TEXT,
    input_file_ids TEXT NOT NULL DEFAULT '[]',
    count INTEGER NOT NULL DEFAULT 2,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | approved | rejected
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- Shots without an explicit template follow the configured defaults (with / without references).
ALTER TABLE shots ADD COLUMN template_explicit INTEGER NOT NULL DEFAULT 1;
ALTER TABLE projects ADD COLUMN video_refs_template TEXT;
ALTER TABLE projects ADD COLUMN video_plain_template TEXT;
-- The references a render was submitted with (asset, variant, version, file, gallery id, position).
ALTER TABLE render_jobs ADD COLUMN refs TEXT;

CREATE VIRTUAL TABLE library_search USING fts5(asset_id UNINDEXED, text);
