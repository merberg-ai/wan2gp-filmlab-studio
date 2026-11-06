-- The Creator: the director drafts a library asset from the owner's description, WanGP generates its
-- reference images (and a character's voice, or a sound's music), and the owner approves it into the
-- library. Until then the asset is a draft, hidden from the library, the director and every shot.

ALTER TABLE assets ADD COLUMN draft INTEGER NOT NULL DEFAULT 0;
ALTER TABLE image_jobs ADD COLUMN label TEXT;                            -- the view or part, e.g. front portrait
ALTER TABLE image_jobs ADD COLUMN media TEXT NOT NULL DEFAULT 'image';   -- image or audio (voice, music)

CREATE TABLE creations (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    kind TEXT NOT NULL,
    brief TEXT NOT NULL,                 -- the owner's own words
    plan TEXT,                           -- JSON from the director (name, description, views, voice, music)
    image_template TEXT,
    edit_template TEXT,
    status TEXT NOT NULL,                -- drafting, ready, approved, discarded
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE INDEX creations_status ON creations(status, updated_at);
