-- FilmLab schema v1 (F1b). IDs are opaque text UUIDs; times are ISO-8601 UTC.

CREATE TABLE owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    logline TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- status: draft | pending_approval | approved | queued | rendering | ready | failed | cancelled
CREATE TABLE shots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    template TEXT NOT NULL,
    model_type TEXT NOT NULL,
    resolution TEXT NOT NULL,
    frames INTEGER NOT NULL,
    seed INTEGER NOT NULL DEFAULT -1,
    status TEXT NOT NULL DEFAULT 'draft',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX shots_project ON shots(project_id, position);

-- status: pending | approved | rejected
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id),
    kind TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- status: submitting | queued | running | cancelling | restored | done | failed | interrupted | cancelled
CREATE TABLE render_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id),
    request_id TEXT NOT NULL UNIQUE,
    wangp_job_id TEXT,
    client_ids TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT,
    current_step INTEGER,
    total_steps INTEGER,
    recovered INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    submitted_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX render_jobs_shot ON render_jobs(shot_id);

CREATE TABLE media (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    frames INTEGER,
    duration_s REAL,
    has_audio INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE takes (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL REFERENCES shots(id),
    job_id TEXT NOT NULL REFERENCES render_jobs(id),
    media_id TEXT NOT NULL REFERENCES media(id),
    thumb_media_id TEXT REFERENCES media(id),
    wangp_media_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX chat_project ON chat_messages(project_id, id);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    project_id TEXT,
    payload TEXT NOT NULL
);

CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);
