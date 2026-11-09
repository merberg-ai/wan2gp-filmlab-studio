-- How Plan with director and Enhance use the shared Library for this project:
-- 'fit' (assets only where the brief calls for them) or 'none' (a fresh film: no Library references).
ALTER TABLE projects ADD COLUMN plan_library TEXT NOT NULL DEFAULT 'fit';
