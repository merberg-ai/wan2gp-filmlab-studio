-- When WanGP started working on a job (left its queue), for render ETAs in the cockpit.

ALTER TABLE render_jobs ADD COLUMN started_at TEXT;
ALTER TABLE image_jobs ADD COLUMN started_at TEXT;
