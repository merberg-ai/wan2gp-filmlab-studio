# FilmLab Studio — Improvement TODO

> **Status:** Proposed improvements, not a list of known broken features. FilmLab Studio v1.0 has already been installed, run, and tested successfully by its owner.
>
> **Updated:** 2026-11-10  
> **Scope:** Safe dev-branch deployment and lifecycle management, privacy clarity, dialogue verification, automated regression checks, GPU release controls, and clean-install reliability.  
> **Rule:** Preserve the existing working workflows. Each item should be implemented in small, independently testable changes; do not add a second renderer or interfere with WanGP's own queue.

## Priority overview

| ID | Priority | Improvement | State |
| --- | --- | --- | --- |
| DEV-01 | P0 | Safe `dev` branch deployment, updates, backend restarts, and guarded rollback | [ ] Planned; audit completed, implementation not started |
| FL-01 | P0 | Clarify cloud vision-inspection data handling and controls | [x] Done (2026-11-06) |
| FL-02 | P1 | Optional speech-to-text verification of generated dialogue | [ ] Proposed — deferred for now |
| FL-03 | P1 | CI checks plus a repeatable real-render regression suite | [ ] Proposed — deferred for now |
| FL-04 | P1 | Explicit GPU hold / finish / release modes | [ ] Proposed — deferred for now |
| FL-05 | P2 | Clean Windows installation and repair test matrix | [ ] Proposed — deferred for now |
| FL-06 | P2 | Repeatable end-to-end short-film acceptance scenario | [ ] Proposed — deferred for now |

## DEV-01 — Safe development deployments and service management (P0)

**Status:** Audit completed 2026-11-10. This is a **plan only**: no installation, update, service, or application scripts have been changed or tested on the owner's OMEN for this task.

**Repository/branch rule:** `merberg-ai/wan2gp-filmlab-studio`. All DEV-01 development commits must target **`dev`**. **Never merge, push changes to, or otherwise modify `main` without the owner's explicit instruction.** Switching the *running installation* between already-existing code versions is a separate operation and must never implicitly merge branches.

### Goal and existing installation

The owner already has a working FilmLab Studio installation on a Windows 11 OMEN, using a **FilmLab WSL2 distro** and a **Git checkout stored on Windows**. The WSL backend executes source from that checkout; the built React cockpit is copied to `~/filmlab/app/static`. Persistent state is stored outside the repository (SQLite database, media/library assets, backups, Python venv, OpenClaw agent workspace, environment files and secrets). WanGP and Ollama run separately on Windows.

The development workflow should let the owner select `dev`, obtain new GitHub commits, deploy changes and restart the backend with **one predictable PowerShell command** instead of manually coordinating Git, Windows and WSL. Preserve the existing installation, projects, assets, credentials, trusted LAN HTTPS setup, service ports and optional autostart.

**Existing relevant files and behavior (confirmed by repository inspection):**

- `scripts/filmlabctl.ps1`: Windows-side start/stop/status/restart/restart-backend/logs, WSL keep-alive, WanGP Windows helper; distro name read from `%LOCALAPPDATA%\FilmLab\config.json`.
- `scripts/filmlabctl.sh`: transient user-systemd backend and OpenClaw gateway units in the FilmLab distro. Backend serves port 19300; gateway uses port 19189.
- `scripts/update.sh`: checks Git state and some jobs, SQLite backup, Git checkout, `uv sync`, frontend build, Director workspace install and backend restart; **does not fetch Git**.
- `scripts/rollback.sh`: returns to recorded prior commit and can restore pre-update SQLite after a migration; must not silently discard FilmLab work made since deployment.
- `scripts/build-frontend.sh`: copies frontend to a WSL build directory and installs into the shared static directory; currently removes the previous static build before copying the new one.
- `scripts/install/Setup-FilmLab.ps1`, `scripts/install/distro-user.sh`, `scripts/install/f7-startup-task.ps1`: original installer, WSL bootstrap and optional Windows logon task.
- `docs/installation.md` and `docs/running.md`: current manual update instructions target `origin/main` and must be revised for dev deployments.

**Risks identified during audit:**

1. Checkout of `origin/dev` directly can leave the repo on a **detached HEAD**, making repeated branch work awkward.
2. Source code is switched before the old backend is stopped, risking a partially changed running installation.
3. The updater guards some jobs, but lacks a comprehensive Director/planning and active-work preflight.
4. Dependency or build failures may leave a new checkout without a healthy deployed cockpit/backend; the health check only prints an HTTP status and does not enforce success. `/api/health` requires login, so **401 is not a sufficient positive health check**.
5. Database migrations are not automatically reversible; restoring a database on ordinary branch switching can lose new projects/edits. Rollback needs a clearly separated, explicit data-recovery action.
6. Checkout, built frontend, backend process and installation metadata can disagree after an interrupted update. The status command should expose this.

### Proposed interface

Create **`scripts/FilmLab-Dev.ps1`** as the Windows entry point, delegating to hardened WSL scripts and using the distro configured at installation. Proposed command names (subject to implementation):

| Command | Intent |
| --- | --- |
| `status` | Show checkout branch/commit, deployed revision, dirty state, WSL/backend/gateway/host helper status, relevant job state and health |
| `switch dev` | Preflight, fetch, create or check out local tracking branch `dev`, deploy it safely |
| `switch main` | Opt-in return to the existing stable branch **without modifying or merging into `main`**; apply the same migration safeguards |
| `update` | Fetch and fast-forward the currently selected branch, rebuild and restart; no force reset/rebase |
| `restart-backend` | Restart only FastAPI, not OpenClaw gateway, WanGP, Ollama or unrelated WSL distros |
| `restart-all` | Restart only FilmLab backend and Director gateway using existing lifecycle controls |
| `logs` | Show backend or gateway logs using current service controller |
| `rollback` | Return to the previous *code deployment* only when safe; database restore is a separate explicit, confirmed recovery |
| `-DryRun` | Show intended branch movement, job checks, affected services and rebuild steps without changes |

Keep `scripts/filmlabctl.ps1` backward-compatible; do not repurpose the installer as the day-to-day update command.

### Implementation checklist

- [ ] **DEV-01.1 — State and preflight:** Determine installed checkout from the controller's path, distro from saved Windows config, currently running services, active source/deployed commit and selected branch. Detect uncommitted and untracked changes; refuse destructive checkout. Check for in-progress renders, images, exports, batches and Director planning, including valid handling of a missing/uninitialized database. Distinguish safe idle from unknown state and fail closed when unable to check.
- [ ] **DEV-01.2 — Branch-aware Git workflow:** `git fetch origin`; create `dev` tracking `origin/dev` if necessary; use a **local named branch** rather than checking out a remote ref directly; fast-forward only; refuse divergence and dirty worktree; never `push`, merge, rebase or force-reset `main`. Avoid fetching or updating on backend-only restarts.
- [ ] **DEV-01.3 — Safe deployment:** Take a consistent SQLite backup and record prior deployment; quiesce/stop backend before changing checkout; sync locked Python deps via `uv sync --frozen`; build React from lockfile; stage and atomically publish cockpit assets so a failed build does not wipe the previously working UI; install Director workspace instructions from the selected commit only after checks. Preserve all install-time environment and host-helper configuration.
- [ ] **DEV-01.4 — Controlled restart and verification:** Start/restart the appropriate FilmLab systemd user units; leave WanGP/Ollama/other distros alone. Verify process/unit state, listener, authenticated or otherwise trustworthy API readiness, frontend response and selected deployed revision. Handle both HTTP localhost and configured HTTPS/LAN. Report failures with actionable logs and nonzero exit status.
- [ ] **DEV-01.5 — Failure handling and rollback:** Record **successful** deployments with previous/new commit, branch, backup path and migration state. Define safe recovery after failures at fetch, checkout, `uv sync`, frontend build, workspace install, backend start and health verification. Code rollback must not silently restore older SQLite or discard post-update media/projects. Warn/refuse when schema incompatibility cannot be established; offer explicitly confirmed database recovery with current-state preservation.
- [ ] **DEV-01.6 — Windows integration:** Test PowerShell quoting and paths (including spaces), Windows-to-WSL conversion, distro configuration, keep-alive behavior, exit-code propagation and current optional logon task. Ensure a normal backend restart does not kill the host helper or WanGP and that nothing calls `wsl --shutdown`.
- [ ] **DEV-01.7 — Documentation:** Update `docs/installation.md`, `docs/running.md` and the command reference with a concise **first switch from running `main` to `dev`**, routine `dev` updates, backend-only restart, status/logs, return to stable and disaster recovery. Explain difference between Git branch and deployed runtime. Preserve existing full-install documentation.
- [ ] **DEV-01.8 — Tests and acceptance:** Add automated/mockable checks where practical for branch creation, clean/dirty/diverged worktree, service/job gate, failed download/build, stopped service, corrupt/missing DB, auth-protected health endpoint, HTTPS/LAN and migration mismatch. Conduct a short, **owner-run** OMEN smoke test before declaring real-host success; do not claim local commands were executed by the GitHub connector.

### Acceptance criteria

- [ ] From the existing healthy OMEN installation, a documented single command can switch to and deploy `dev` without reinstalling FilmLab or losing existing projects/media.
- [ ] Subsequent `dev` updates require one command and produce a clear success/failure status showing deployed SHA and branch.
- [ ] `restart-backend` works independently of updates and does not restart the Director gateway, WanGP or Ollama.
- [ ] In-progress work or a dirty/diverged checkout prevents unsafe deployment; a failed deployment leaves an understandable, recoverable state.
- [ ] Startup, localhost and configured phone/LAN HTTPS continue working, and the existing Windows autostart behavior remains intact.
- [ ] Migrations and code rollback cannot silently erase post-update work. Data restore is separately backed up and explicitly authorized.
- [ ] **No changes to `main`** (no merge, push, or commit); all implementation work remains on `dev`.

### Boundaries / non-goals

No second FilmLab instance, no second WSL distro, no replacement of the WanGP installation, no renderer/GPU workflow rewrite, and no forced migration to a new install path. Do not modify live OMEN runtime or schedule automatic deployments as part of authoring this roadmap. Build and test the controller first, then perform the initial development-branch deployment deliberately.

---

## FL-01 — Cloud inspection privacy and disclosure (P0)

**Why:** The README and `docs/security.md` explain that reference images and voice samples stay local during director conversations. However, `backend/app/inspect_takes.py` extracts four video frames and passes them as images to the configured Ollama vision model. The default `gemma4:31b-cloud` model can process those frames remotely. This distinction should be immediately visible to users rather than hidden in implementation details.

**Tasks**

- [x] Update `README.md`, `docs/security.md`, `docs/user-guide.md`, and relevant setup text to distinguish **director text prompts**, **vision-inspection frames**, **render references**, and **locally stored media**.
- [x] Explain clearly that automatic take inspection may transmit four extracted frames to the configured vision provider when a cloud model is selected; technical FFmpeg inspection remains local.
- [x] Display the active vision model and a **Local / Cloud** indicator in the inspection settings and before enabling cloud-based inspection.
- [x] Provide a separately controlled option for vision inspection: **Off**, **local model**, or **cloud model**. Keep technical inspection available regardless.
- [x] Never describe a cloud-model workflow as fully local/offline, and do not silently fall back from a local vision model to a cloud model.
- [x] Add tests for disabled vision inspection, local-model selection, cloud consent/disclosure, and failed vision calls.

**Done (2026-11-06).** What changed:

- **Setting** `vision` = `off` / `local` / `cloud` (`backend/app/inspect_takes.py`, `PUT /api/settings/autonomy`). The mode must match the model (Ollama's `…-cloud` / `:cloud` names); a mismatch is refused with a plain explanation, so FilmLab never switches between local and cloud by itself. With `off` no frames are sent to any model; the technical ffmpeg checks always run.
- **Defaults:** a new install starts with the vision check **off**. An install that had already run vision checks keeps the model it used (no behaviour change for existing owners).
- **Cockpit:** Settings → Take inspection has Off / Local model / Cloud model with what each sends, a **Local / Cloud** label on the model, the current state in one line, and a confirmation before turning on cloud. Inspections done without a vision check say so on the take.
- **Setup:** after the Models step, setup asks Off / Cloud / Local (`python -m app.cli vision off|local|cloud [--model M]`).
- **Docs:** README, `docs/security.md` (a table of the four kinds of data and where each goes), `docs/user-guide.md`, `docs/installation.md` and `docs/running.md` agree.
- **Tests:** `backend/tests/test_autonomy.py`: vision off on a new install (no frames sent, technical checks run), local/cloud mismatch refused and local model used, existing installs keep their choice, failed vision call (technical result stands).

**Acceptance**

- A user can tell whether film frames will leave their PC before enabling inspection.
- When vision inspection is disabled, no images are submitted to a vision model; technical checks still work.
- README, setup, settings, and security documentation agree.
- Existing successful inspection/render workflows remain usable.

**Implementation pointers:** `backend/app/inspect_takes.py`, `backend/app/autonomy_api.py`, `frontend/src/Autonomy.tsx`, `docs/security.md`.

## FL-02 — Verify generated dialogue against the script (P1)

**Why:** FilmLab generates structured sound instructions for MiniMax H3, but today's take inspector explicitly does not transcribe speech. A correctly formatted prompt does not guarantee that a video model spoke every word faithfully.

**Tasks**

- [ ] Add an **optional** speech-verification stage after a finished take has been ingested, using a configurable speech-to-text engine.
- [ ] Prefer an offline/local transcription option; document any optional cloud backend and its audio-data implications.
- [ ] Compare recognized speech with the shot's `sound.lines[].text` in speaker order; report missing lines, extra speech, and material wording differences.
- [ ] Support a useful **no dialogue expected** check, while distinguishing ambient sounds from spoken words.
- [ ] Show the transcript, confidence/uncertainty, and comparison results alongside existing technical and four-frame visual inspection.
- [ ] Integrate dialogue mismatches into reshoot suggestions, but **do not automatically reject a take or overwrite the owner's verdict**.
- [ ] Handle music-heavy shots, non-English speech, off-screen voices, and clips too short for reliable recognition without presenting false certainty.
- [ ] Add mocked ASR fixtures for exact match, paraphrase/mismatch, missing dialogue, silence, and ASR failure.

**Acceptance**

- A take with expected dialogue shows the script and detected transcript side by side.
- Obvious missing or substituted words can be flagged without declaring every ASR uncertainty a failure.
- Users can disable transcription, and no audio is uploaded without a clearly chosen cloud option.
- Existing sound generation, owner review, and redo behavior are unchanged unless the user enables the new check.

**Implementation pointers:** `backend/app/sound.py`, `backend/app/inspect_takes.py`, `frontend/src/Review.tsx`, `frontend/src/SoundEditor.tsx`.

## FL-03 — Automated CI and integration/regression checks (P1)

**Why:** The repository already contains a substantial test suite. Automating the checks prevents a documentation or UI change from accidentally breaking a working filmmaking pipeline.

**Tasks**

- [ ] Add GitHub Actions for Python tests (`backend/tests`), frontend TypeScript checking, and production Vite build.
- [ ] Use locked dependencies where available (`backend/uv.lock`, `frontend/package-lock.json`); avoid changing dependency versions just to add CI.
- [ ] Run checks on pull requests and pushes to `main`; use test doubles rather than a real GPU in hosted CI.
- [ ] Add a small API-contract suite for WanGP MCP: initialize, list templates, validate, submit with idempotent request ID, poll, retrieve output, and recover after reconnect.
- [ ] Keep real WanGP/GPU tests in a **separate opt-in local acceptance command**; do not expect GitHub-hosted runners to have the installed models or an RTX GPU.
- [ ] Test queue coexistence: FilmLab must not cancel or claim ownership of unrelated WanGP UI/agent jobs.
- [ ] Include interrupted-then-resumed jobs, duplicate submissions, missing models, disconnections, and low-disk/GPU-hold refusal cases.
- [ ] Document the exact commands and expected PASS criteria in a regression guide.

**Acceptance**

- Every PR receives a clear pass/fail for backend tests, frontend typecheck, and production build.
- The local real-render acceptance suite can be deliberately run on the owner's own GPU machine without altering unrelated WanGP work.
- A failed integration test identifies the failing boundary (FilmLab API, MCP, model availability, queue, recovery, media ingestion) rather than merely hanging.

**Implementation pointers:** `backend/tests/`, `backend/app/wangp/client.py`, `backend/app/renders.py`, `frontend/package.json`, new `.github/workflows/` and `docs/regression-testing.md`.

## FL-04 — Make GPU sharing and VRAM release explicit (P1)

**Why:** FilmLab already has **Hold GPU work** and shares WanGP's queue. Holding future work is not necessarily the same as releasing GPU memory from a running model or another process. The UI should not imply it has freed VRAM unless it has verified that outcome.

**Tasks**

- [ ] Preserve the existing **Hold GPU work** option as the safe baseline: block new FilmLab jobs without affecting the owner's own WanGP tasks.
- [ ] Add clearly labeled modes: **Pause new FilmLab work**, **Finish current FilmLab job, then hold**, and **Cancel current FilmLab job, then hold** (the last one requires explicit confirmation).
- [ ] Show FilmLab-owned job state and actual observed GPU/VRAM utilization before and after a hold.
- [ ] Investigate whether WanGP provides a supported model-unload/release operation. If it does not, display **VRAM release not guaranteed** rather than claiming full GPU cleanup.
- [ ] Never unload another application's model, kill unrelated processes, cancel WanGP UI jobs, or terminate an unrelated WSL distro to satisfy a GPU hold.
- [ ] Make pending batch, redo-run, and newly requested actions obey the selected hold mode; require explicit owner action to resume.
- [ ] Add tests for shared queues, active renders, refusal paths, and restart while held.

**Acceptance**

- The owner can distinguish a paused queue from a genuinely idle renderer.
- FilmLab cannot silently destroy non-FilmLab work.
- The UI only reports VRAM released when measured; uncertain states are labeled honestly.
- Existing hold behavior remains the default until the new modes are tested.

**Implementation pointers:** `backend/app/health.py`, `backend/app/gpu_stats.py`, `backend/app/renders.py`, `backend/app/batches.py`, `backend/app/runs.py`, `frontend/src/System.tsx`.

## FL-05 — Clean Windows install and recovery matrix (P2)

**Why:** The guided installer supports fresh Windows 11 machines and pre-existing WanGP/Ollama/WSL installations. A successful established-host install should be preserved while testing the many first-run combinations.

**Tasks**

- [ ] Test from a fresh Windows 11 VM or spare NVIDIA machine (where GPU access is available), and document the exact OS/driver/WSL prerequisites.
- [ ] Test both **Download ZIP** and **git clone** paths, including update linkage for ZIP installs.
- [ ] Test already-installed Ollama, WanGP, WSL, and Git; absent software; wrong versions; and an interrupted setup.
- [ ] Verify the eleven-step setup resumes after a restart and never overwrites unrelated existing configurations.
- [ ] Explicitly warn before changes to global `%USERPROFILE%\.wslconfig`; test backup and restoration of that file. A `wsl --shutdown` affects *all* running distros, not just FilmLab.
- [ ] Test port collisions, LAN certificate installation, firewall opt-in, autostart opt-in and removal, and repair/re-run.
- [ ] Test `-CheckOnly` for genuine read-only behavior and clear failure reporting.
- [ ] Test clean uninstall and recovery from an interrupted update, including database migration rollback.
- [ ] Maintain a compact installation matrix with environment, result, and evidence; do not demand repetitive manual command sequences for routine checks.

**Acceptance**

- A new user can follow the README verbatim to reach a working authenticated cockpit and successful first render.
- Re-running setup does not create duplicate distros, clobber unrelated WSL settings, or alter other WanGP projects.
- Failed and interrupted steps are recoverable, and opt-in operations remain opt-in.

**Implementation pointers:** `Setup-FilmLab.cmd`, `scripts/install/Setup-FilmLab.ps1`, `scripts/filmlabctl.ps1`, `scripts/update.sh`, `scripts/rollback.sh`, `docs/installation.md`.

## FL-06 — Short-film end-to-end regression scenario (P2)

**Why:** The application is already running successfully. Preserve a reproducible **smoke/regression test** that crosses the major features as releases evolve; this is **not** a prerequisite to calling the current installation functional.

**Proposed fixture:** A ~30-second film with three scenes, two recurring characters, a shared location, one reference generated through the Creator, scripted dialogue, a deliberately rejected take, a controlled reshoot, and a final MP4/SRT export.

**Tasks**

- [ ] Document fixture assets, available model presets, expected shots, and approved owner interactions.
- [ ] Verify reference reuse, shot locks, structured sound, approvals, sequential batch rendering, review, trim/transition, and final export.
- [ ] Check continuity and dialogue verification where enabled, without assuming generative output is perfectly deterministic.
- [ ] Separately test WanGP restart and recovery during a queued/running FilmLab job.
- [ ] Capture only actionable results: pass/fail per feature, log paths, media metadata, timing, VRAM peaks, and deviations.
- [ ] Keep this test opt-in: it consumes GPU time and should never automatically run in CI or during installation.

**Acceptance**

- The scenario remains repeatable on the owner’s existing hardware and produces a playable export.
- Failures identify a feature/regression rather than treating a subjective creative variation as a software failure.
- Other WanGP workspace jobs remain untouched.

## Suggested delivery order

> **FL-01 is completed** (2026-11-06). **DEV-01 is the next planned development task** and remains unimplemented. FL-02 through FL-06 remain proposed and deferred.


1. **FL-01:** Fix privacy disclosures and inspection controls first because they affect what users knowingly send to a cloud provider.
2. **FL-03:** Establish CI before adding more processing paths.
3. **FL-02:** Add optional dialogue verification under the existing inspection/review model.
4. **FL-04:** Refine GPU controls without breaking the existing hold semantics.
5. **FL-05 / FL-06:** Run clean-install and film-regression matrices as release gates.

## Change management

- This file is a **backlog only**. No item is implemented or tested merely because it appears here.
- Mark checkboxes only after implementation and verification; link the related commit, issue, or test evidence when available.
- Keep modifications narrowly scoped, with rollback paths for installer, persistence, autonomy, and renderer lifecycle changes.
- Maintain FilmLab's central design contract: **the owner controls approvals and GPU spending; the director proposes; the backend enforces; WanGP remains independently usable.**
