-- F5: take review, the edit (one per project), exports and deliveries.

ALTER TABLE takes ADD COLUMN verdict TEXT;                         -- good | maybe | reject | NULL (not reviewed)
ALTER TABLE takes ADD COLUMN notes TEXT NOT NULL DEFAULT '';
ALTER TABLE shots ADD COLUMN selected_take_id TEXT REFERENCES takes(id);  -- NULL: the newest take not rejected

-- The edit is one JSON document per project (per-clip trims and audio, music, text, settings);
-- clips themselves come from the storyboard, so the edit follows the plan.
CREATE TABLE edits (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    doc TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE edit_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    doc TEXT NOT NULL,                      -- the edit after this change
    created_at TEXT NOT NULL
);
CREATE INDEX edit_revisions_project ON edit_revisions(project_id, created_at);

-- The director's proposed edit changes; the owner accepts or rejects them.
CREATE TABLE edit_suggestions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    change TEXT NOT NULL,                   -- JSON
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | rejected
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- Draft and final exports. A final export that succeeds is a numbered, immutable delivery.
CREATE TABLE exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,                     -- draft | final
    number INTEGER,                         -- delivery number (final exports that finished)
    status TEXT NOT NULL,                   -- queued | running | done | failed | cancelled
    manifest TEXT NOT NULL,                 -- JSON: everything the film is built from
    mode TEXT,                              -- encode | copy
    progress REAL,
    error TEXT,
    rel_dir TEXT NOT NULL,                  -- under media_dir
    sha256 TEXT,
    bytes INTEGER,
    duration_s REAL,
    srt INTEGER NOT NULL DEFAULT 0,
    verify_status TEXT,                     -- running | match | mismatch | failed
    verify_detail TEXT,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    deleted_at TEXT
);
CREATE INDEX exports_project ON exports(project_id, created_at);
