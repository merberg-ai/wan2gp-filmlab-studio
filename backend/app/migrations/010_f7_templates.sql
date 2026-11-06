-- F7: projects started from a production template keep its id and style note.

ALTER TABLE projects ADD COLUMN template TEXT;
ALTER TABLE projects ADD COLUMN style TEXT NOT NULL DEFAULT '';
