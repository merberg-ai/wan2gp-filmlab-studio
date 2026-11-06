# FilmLab Studio — Improvement TODO

> **Status:** Proposed improvements, not a list of known broken features. FilmLab Studio v1.0 has already been installed, run, and tested successfully by its owner.
>
> **Updated:** 2026-11-06  
> **Scope:** Privacy clarity, dialogue verification, automated regression checks, GPU release controls, and clean-install reliability.  
> **Rule:** Preserve the existing working workflows. Each item should be implemented in small, independently testable changes; do not add a second renderer or interfere with WanGP's own queue.

## Priority overview

| ID | Priority | Improvement | State |
| --- | --- | --- | --- |
| FL-01 | P0 | Clarify cloud vision-inspection data handling and controls | [ ] Proposed |
| FL-02 | P1 | Optional speech-to-text verification of generated dialogue | [ ] Proposed |
| FL-03 | P1 | CI checks plus a repeatable real-render regression suite | [ ] Proposed |
| FL-04 | P1 | Explicit GPU hold / finish / release modes | [ ] Proposed |
| FL-05 | P2 | Clean Windows installation and repair test matrix | [ ] Proposed |
| FL-06 | P2 | Repeatable end-to-end short-film acceptance scenario | [ ] Proposed |

## FL-01 — Cloud inspection privacy and disclosure (P0)

**Why:** The README and `docs/security.md` explain that reference images and voice samples stay local during director conversations. However, `backend/app/inspect_takes.py` extracts four video frames and passes them as images to the configured Ollama vision model. The default `gemma4:31b-cloud` model can process those frames remotely. This distinction should be immediately visible to users rather than hidden in implementation details.

**Tasks**

- [ ] Update `README.md`, `docs/security.md`, `docs/user-guide.md`, and relevant setup text to distinguish **director text prompts**, **vision-inspection frames**, **render references**, and **locally stored media**.
- [ ] Explain clearly that automatic take inspection may transmit four extracted frames to the configured vision provider when a cloud model is selected; technical FFmpeg inspection remains local.
- [ ] Display the active vision model and a **Local / Cloud** indicator in the inspection settings and before enabling cloud-based inspection.
- [ ] Provide a separately controlled option for vision inspection: **Off**, **local model**, or **cloud model**. Keep technical inspection available regardless.
- [ ] Never describe a cloud-model workflow as fully local/offline, and do not silently fall back from a local vision model to a cloud model.
- [ ] Add tests for disabled vision inspection, local-model selection, cloud consent/disclosure, and failed vision calls.

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
- The local real-render acceptance suite can be deliberately run on the OMEN without altering unrelated WanGP work.
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
