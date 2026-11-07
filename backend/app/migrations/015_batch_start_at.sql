-- A batch can wait for a set time before it starts (render overnight): it stays paused until start_at.

ALTER TABLE batches ADD COLUMN start_at TEXT;
