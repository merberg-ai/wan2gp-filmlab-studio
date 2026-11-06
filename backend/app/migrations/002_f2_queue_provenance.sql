-- F2: queue position, stall detection, translated errors, per-take provenance.
ALTER TABLE render_jobs ADD COLUMN queue_ahead INTEGER;
ALTER TABLE render_jobs ADD COLUMN queue_running TEXT;
ALTER TABLE render_jobs ADD COLUMN last_progress_at TEXT;
ALTER TABLE render_jobs ADD COLUMN stalled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE render_jobs ADD COLUMN error_code TEXT;
ALTER TABLE takes ADD COLUMN provenance TEXT;
