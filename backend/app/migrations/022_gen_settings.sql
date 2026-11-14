-- A project's own generation settings (WanGP's Advanced mode: steps, guidance, sampler, LoRAs, ...), per video
-- template: {"<template>": {"<setting>": value}}. NULL: every shot renders with its template's own settings.
ALTER TABLE projects ADD COLUMN gen_settings TEXT;
