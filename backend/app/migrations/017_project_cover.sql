-- A project's cover on the Projects page: a take the owner picked (else FilmLab picks one; see project_cards).

ALTER TABLE projects ADD COLUMN cover_take_id TEXT;
