-- F6: take inspections, redo proposals, bounded director runs, autonomy level.

ALTER TABLE projects ADD COLUMN autonomy TEXT NOT NULL DEFAULT 'A2';  -- A0..A3, and A3 (bounded redo) is opt-in

CREATE TABLE take_inspections (
    take_id     TEXT PRIMARY KEY REFERENCES takes(id),
    status      TEXT NOT NULL,              -- running | done | failed
    verdict     TEXT,                       -- ok | flagged
    technical   TEXT,                       -- JSON: measured signals and flags
    vision      TEXT,                       -- JSON: the vision model's answer (or its error)
    summary     TEXT,
    model       TEXT,
    sheet_rel   TEXT,                       -- contact sheet (4 keyframes) under media/cache
    error       TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE director_runs (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id),
    status       TEXT NOT NULL,             -- running | paused | stopped | done
    shot_ids     TEXT NOT NULL,             -- JSON, storyboard order
    caps         TEXT NOT NULL,             -- JSON: max_retries, gpu_minutes, wall_minutes, max_renders
    used_renders INTEGER NOT NULL DEFAULT 0,
    used_gpu_s   REAL NOT NULL DEFAULT 0,
    shot_state   TEXT NOT NULL DEFAULT '{}', -- JSON: {shot_id: {attempts, state, asked_at, note}}
    stop_reason  TEXT,
    log          TEXT NOT NULL DEFAULT '[]', -- JSON list of {at, shot_id, text}
    requested_by TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX director_runs_project ON director_runs(project_id, created_at);

CREATE TABLE redo_proposals (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id),
    shot_id        TEXT NOT NULL REFERENCES shots(id),
    take_id        TEXT,                    -- the take it wants to replace
    run_id         TEXT REFERENCES director_runs(id),
    changes        TEXT NOT NULL,           -- JSON: {prompt?, seed?}
    reason         TEXT NOT NULL,
    status         TEXT NOT NULL,           -- pending | rendering | done | failed | rejected | superseded
    job_id         TEXT,
    result_take_id TEXT,
    outcome        TEXT,                    -- what happened, for the owner
    created_at     TEXT NOT NULL,
    decided_at     TEXT
);
CREATE INDEX redo_proposals_project ON redo_proposals(project_id, status);
