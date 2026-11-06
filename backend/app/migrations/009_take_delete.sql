-- Recoverable deletion of single takes (Recently deleted, like shots).

ALTER TABLE takes ADD COLUMN deleted_at TEXT;
