-- Project settings and recoverable deletion.
-- deleted_at set = in "Recently deleted": hidden everywhere, restorable; "Delete forever" removes rows and files.

ALTER TABLE projects ADD COLUMN deleted_at TEXT;
ALTER TABLE projects ADD COLUMN default_resolution TEXT NOT NULL DEFAULT '1280x720';
ALTER TABLE projects ADD COLUMN default_frames INTEGER NOT NULL DEFAULT 121;
ALTER TABLE shots ADD COLUMN deleted_at TEXT;
ALTER TABLE assets ADD COLUMN deleted_at TEXT;
ALTER TABLE variants ADD COLUMN deleted_at TEXT;

-- The old, unused "archived" flags become deletions (e.g. throwaway test assets), so they show up to be cleared.
UPDATE projects SET deleted_at = updated_at WHERE archived = 1;
UPDATE assets SET deleted_at = updated_at WHERE archived = 1;
