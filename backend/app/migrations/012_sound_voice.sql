-- A shot's sound as fields (who says which exact words, ambience, music, audio refinement), compiled into
-- MiniMax H3's structured prompt at render time; and a character's voice sample, used as its voice reference.

ALTER TABLE shots ADD COLUMN sound TEXT;                                   -- JSON (see app/sound.py), NULL = not set
ALTER TABLE assets ADD COLUMN voice_file_id TEXT REFERENCES library_files(id);
