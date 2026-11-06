# 🛡️ Security

FilmLab Studio is a single-user application for your own PC. This page explains what runs where, what can reach what, and what the AI director can and can't do.

## What runs where

| Component | Where | Listens on | Reachable from |
|---|---|---|---|
| FilmLab backend and cockpit | FilmLab WSL distro | `:19300` | This PC only, or your **home network** if you set up phone access (HTTPS) |
| Director gateway (OpenClaw) | FilmLab WSL distro | `127.0.0.1:19189` | This PC only (token required) |
| WanGP MCP server | Windows | `127.0.0.1:8000` | This PC only |
| WanGP web UI | Windows | WanGP's own setting | As you configured WanGP (FilmLab doesn't use it) |
| Ollama | Windows | `127.0.0.1:11434` | This PC only |

FilmLab is **never exposed to the internet**. Don't forward port 19300 on your router.

## Your data

What FilmLab sends where, by kind of data:

| Data | Where it goes | Your control |
|---|---|---|
| **Stored media**: projects, takes, exports, library images, voice samples, backups | Stays **on your PC**, inside the FilmLab distro | — |
| **Render references**: a shot's prompt, its reference images and voice samples | **WanGP on your PC** (loopback only), to render the shot | — |
| **Director text**: briefs, shot prompts, chat, library *names and descriptions* | The director's **Ollama model**: with the default `gemma4:31b-cloud`, **Ollama's cloud**. No images, videos or audio are sent to it | Pick a local model under **Settings → Director** to keep this on your PC |
| **Vision-check frames**: four keyframes of each new take, with the shot's prompt and scene | **Nowhere** by default. If you turn the vision check on: a **local** Ollama model (stays on your PC), or a **cloud** model (the frames go to Ollama's servers) | **Settings → Take inspection**: Off / Local model / Cloud model. Setup asks you during install |

- Take inspection's **technical checks** (length, black or frozen picture, silent or clipping sound) always run with ffmpeg on your PC.
- The vision check's mode must match its model: FilmLab refuses a cloud model under *Local* (and the reverse), and never falls back from a local model to a cloud one. With the check off, no frames are sent to any model.
- Turning on the **cloud** vision check asks you to confirm first, and Settings always shows whether the model is **Local** or **Cloud**.

## The cockpit

- **One owner login**, set during setup: a password of 12+ characters, hashed with Argon2. Failed logins are throttled per device.
- Sessions use secure, HttpOnly cookies; every change also needs a CSRF token.
- With phone access, FilmLab serves **HTTPS** with its own **private certificate authority**. It's limited (by X.509 name constraints) to your home network's addresses, `localhost` and your PC's name, so it can't vouch for any other site. Its private key never leaves the distro.
- Requests from outside the allowed networks are refused before they reach the app.

## The director

The director is an AI agent. FilmLab treats it as untrusted and **enforces** its limits rather than relying on its instructions.

| It can | It can't |
|---|---|
| Read the project, the storyboard and the Library | Run commands, read or write files, browse the web, or use other agents (those tool groups are **denied** in OpenClaw's policy) |
| Draft and edit **unlocked** shots and scenes, with a reason the owner sees | Change locked shots, takes you chose, or your verdicts |
| Ask for reference images and draft new assets for you to approve | Generate images, render, or spend GPU time |
| Request renders and batches for you to approve | Change autonomy, raise redo limits, or release a GPU hold |
| Propose redos and edit changes | Cancel or change WanGP jobs |

All of the director's actions go through FilmLab's own tools, which check every argument; every action is recorded in the audit log and the shot's history. In **A3** redo runs, FilmLab (not the director) renders, strictly within the limits you set.

## WanGP

- FilmLab connects to WanGP's MCP server on **loopback only**, in its own WanGP workspace.
- It uses a fixed allowlist of WanGP tools: templates, validate, generate, gallery, uploads to its own workspace, its own jobs, and read-only media settings. It never uses WanGP's file-system or Python tools, and it uses the queue-cancel action only for its own tasks.
- Setup warns if WanGP is launched with `--mcp-allow-read-file-system` or with MCP bound beyond `127.0.0.1`. FilmLab needs neither.
- WanGP's own web UI is outside FilmLab. If you expose it on your network, protect it with WanGP's own `--auth` options.

## Secrets

| Secret | Where |
|---|---|
| Director gateway token | `~/.config/filmlab/secrets.env` in the distro (mode 600) |
| TLS private keys | `~/.config/filmlab/tls/` in the distro (mode 600) |
| Your password | Argon2 hash in FilmLab's database |

None of these are included in backups, written to logs, or committed to the repository.

## Reporting a vulnerability

Please report security problems privately via GitHub's **Security → Report a vulnerability** on this repository, not in a public issue.
