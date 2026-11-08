-- Music videos: one song plays under the whole film. music_mode is NULL (an ordinary film), 'lipsync' (each shot
-- is rendered from its slice of the song by a song-driven model) or 'soundtrack' (shots are picture only, the
-- song plays over them). The song is an audio file of the Library; the lyrics and style are what it was made from.
-- render_jobs.status_text keeps WanGP's own status line (e.g. while it downloads a model).

ALTER TABLE projects ADD COLUMN music_mode TEXT;
ALTER TABLE projects ADD COLUMN song_file_id TEXT;
ALTER TABLE projects ADD COLUMN song_asset_id TEXT;
ALTER TABLE projects ADD COLUMN lyrics TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN song_style TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN speech_template TEXT;
ALTER TABLE render_jobs ADD COLUMN status_text TEXT;
