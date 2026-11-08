-- The Console (console.py): what FilmLab does in the background, one line each; the newest few thousand are kept.

CREATE TABLE console_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    project_id TEXT,
    detail TEXT
);
CREATE INDEX console_log_project ON console_log(project_id, id);
