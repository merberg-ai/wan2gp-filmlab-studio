-- The Storybook: a project as an illustrated book (picture book or comic), its history and its exports.
CREATE TABLE storybooks (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    doc TEXT NOT NULL,                      -- layout, cover, and per shot: frame, crop, narration, dialogue, skip
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE storybook_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX storybook_revisions_project ON storybook_revisions(project_id, created_at);
CREATE TABLE storybook_exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    status TEXT NOT NULL,                   -- queued | running | done | failed
    layout TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    rel_dir TEXT,                           -- under the media folder: book.pdf, pages.zip, book.cbz, page-NNN.png
    bytes INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX storybook_exports_project ON storybook_exports(project_id, created_at);
