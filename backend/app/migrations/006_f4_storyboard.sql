-- F4: brief, scenes, storyboard locks and transitions, shot revisions, batch rendering.

ALTER TABLE projects ADD COLUMN brief TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN target_seconds INTEGER;

CREATE TABLE scenes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',          -- narrative goal: what this scene is for
    time_place TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    location_asset_id TEXT REFERENCES assets(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX scenes_project ON scenes(project_id, position);

ALTER TABLE shots ADD COLUMN scene_id TEXT REFERENCES scenes(id);
ALTER TABLE shots ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;           -- owner lock: no director change, no batch re-render
ALTER TABLE shots ADD COLUMN transition TEXT NOT NULL DEFAULT 'cut';     -- into the next shot: cut | fade | dissolve
ALTER TABLE shots ADD COLUMN rationale TEXT NOT NULL DEFAULT '';         -- the director's latest reason

-- Every change to what a shot renders, with who and why; "before" is enough to revert.
CREATE TABLE shot_revisions (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL REFERENCES shots(id),
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,                     -- create | update | refs | revert | plan
    before TEXT NOT NULL,                   -- JSON snapshot of the tracked fields (and refs)
    after TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX shot_revisions_shot ON shot_revisions(shot_id, created_at);

-- An owner-approved batch: shots rendered one after another, in storyboard order.
CREATE TABLE batches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    status TEXT NOT NULL,                   -- running | paused | done | cancelled
    pause_reason TEXT,
    queue_cap INTEGER NOT NULL DEFAULT 1,
    requested_by TEXT NOT NULL,             -- owner, or director (via an approved request)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE batch_items (
    batch_id TEXT NOT NULL REFERENCES batches(id),
    position INTEGER NOT NULL,
    shot_id TEXT NOT NULL REFERENCES shots(id),
    status TEXT NOT NULL DEFAULT 'pending', -- pending | rendering | done | failed | skipped | cancelled
    job_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, position)
);

-- The director's request to render a storyboard batch (it can't start one).
CREATE TABLE batch_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shot_ids TEXT NOT NULL,                 -- JSON list, in order
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    created_at TEXT NOT NULL,
    decided_at TEXT
);
