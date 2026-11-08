-- The Create page: a draft's picture size, how a character's voice is made (designed from a description, or cloned
-- from a sample the owner uploads), and short test clips of a draft (not project media, so a table of their own).

ALTER TABLE creations ADD COLUMN resolution TEXT;
ALTER TABLE creations ADD COLUMN voice_mode TEXT;
ALTER TABLE creations ADD COLUMN voice_sample_file_id TEXT;
ALTER TABLE creations ADD COLUMN sound_type TEXT;
CREATE TABLE creation_clips (
    id TEXT PRIMARY KEY,
    creation_id TEXT NOT NULL REFERENCES creations(id),
    job_id TEXT,
    rel_path TEXT NOT NULL,
    thumb_rel_path TEXT,
    duration_s REAL,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX creation_clips_creation ON creation_clips(creation_id, created_at);
