-- The owner's own words for a shot. Enhance always rewrites the prompt from this idea, so
-- repeated enhancing starts clean instead of patching an earlier rewrite.
ALTER TABLE shots ADD COLUMN idea TEXT NOT NULL DEFAULT '';
-- Owner shots: the prompt of their first render (before any rewrite), else the current prompt.
UPDATE shots SET idea = COALESCE(
    (SELECT json_extract(j.settings, '$.prompt') FROM render_jobs j WHERE j.shot_id = shots.id
     ORDER BY j.submitted_at LIMIT 1), prompt)
WHERE created_by = 'owner';
