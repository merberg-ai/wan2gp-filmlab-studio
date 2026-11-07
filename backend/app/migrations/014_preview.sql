-- Preview renders: a quick low-resolution pass to judge timing, framing and continuity before full quality.
-- A preview job and its take are marked, and a batch can be a preview batch.

ALTER TABLE render_jobs ADD COLUMN preview INTEGER NOT NULL DEFAULT 0;
ALTER TABLE takes ADD COLUMN preview INTEGER NOT NULL DEFAULT 0;
ALTER TABLE batches ADD COLUMN preview INTEGER NOT NULL DEFAULT 0;
