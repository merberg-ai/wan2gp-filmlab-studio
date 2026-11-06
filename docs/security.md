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

- Your projects, takes, library images, voices and exports stay **on your PC**, inside the FilmLab distro.
- The **director** sends prompts, briefs, chat messages and library *names and descriptions* to its model. With the default **cloud** model (`gemma4:31b-cloud`) that means Ollama's cloud. Your images, videos and voice samples are not sent to it. Choose a local Ollama model under **Settings → Director** if you want everything to stay on your PC.
- **Take inspection** sends four frames of each take to the inspection model through Ollama.
- WanGP receives the shots' prompts and the reference images and voice samples it needs to render.

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
