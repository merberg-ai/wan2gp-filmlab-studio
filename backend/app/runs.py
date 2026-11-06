"""Bounded redo runs (autonomy A3, opt-in per project) and the redo proposals they render.

The director never renders. It *proposes* a redo (a revised prompt and/or seed, with a reason). Outside a run,
a proposal waits for the owner to Accept it. Inside a run, which only the owner can start from the cockpit,
FilmLab renders proposals itself, one at a time, until the run's caps are reached:

- max_retries: new takes per shot;
- max_renders: new takes in the whole run;
- gpu_minutes: GPU time, counted from each render's own job times. A render whose estimate (this machine's
  past renders of the same model) would cross the budget isn't started;
- wall_minutes: the run's own clock.

A shot is "done" when the take the edit would use passes inspection. Locked shots, shots whose take the
owner chose explicitly, and shots with a take marked good are never touched; takes are never deleted and
verdicts never set. Pause, Stop and the global GPU hold are owner controls in the backend, independent of
anything the director says.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from . import shots
from .db import Database, new_id, now
from .events import EventBus
from .renders import ACTIVE, RenderError

log = logging.getLogger("filmlab.runs")

CAPS = {"max_retries": (1, 5, 2), "max_renders": (1, 30, 6), "gpu_minutes": (1, 240, 20), "wall_minutes": (5, 480, 60)}
OPEN = ("running", "paused")
ASK_TIMEOUT_S = 300
RENDERABLE = ("draft", "pending_approval", "failed", "ready")


class RunError(ValueError):
    pass


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _age_s(s: str) -> float:
    return (datetime.now(timezone.utc) - _ts(s)).total_seconds()


def clean_caps(raw: dict | None) -> dict:
    out = {}
    for k, (lo, hi, default) in CAPS.items():
        v = (raw or {}).get(k, default)
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise RunError(f"{k} must be a whole number") from None
        if not lo <= v <= hi:
            raise RunError(f"{k} must be {lo}-{hi}")
        out[k] = v
    return out


class Runs:
    def __init__(self, db: Database, bus: EventBus, renders, storyboard, edit, inspector, chat_send):
        self.db, self.bus, self.renders, self.storyboard, self.edit = db, bus, renders, storyboard, edit
        self.inspector, self.chat_send = inspector, chat_send
        self._tasks: dict[str, asyncio.Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self.closing = False
        self.held = None  # async () -> reason | None (the global GPU hold), set by Studio
        inspector.listeners.append(self._inspected)

    # --- reading --------------------------------------------------------------

    async def get(self, run_id: str) -> dict:
        r = await self.db.fetchone("SELECT * FROM director_runs WHERE id = ?", (run_id,))
        if not r:
            raise RunError("no such run")
        for k in ("shot_ids", "caps", "shot_state", "log"):
            r[k] = json.loads(r[k])
        r["used_gpu_min"] = round(r["used_gpu_s"] / 60, 1)
        return r

    async def latest(self, project_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT id FROM director_runs WHERE project_id = ? ORDER BY CASE WHEN status IN "
                                     "('running', 'paused') THEN 0 ELSE 1 END, created_at DESC LIMIT 1", (project_id,))
        return await self.get(row["id"]) if row else None

    async def proposals(self, project_id: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT p.*, s.title, s.position, s.prompt AS current_prompt FROM redo_proposals p JOIN shots s ON s.id = p.shot_id "
            "WHERE p.project_id = ? ORDER BY p.created_at DESC LIMIT 40", (project_id,))
        for r in rows:
            r["changes"] = json.loads(r["changes"])
        return rows

    # --- what needs work -------------------------------------------------------

    async def needs_work(self, shot: dict, inspect: bool = False) -> tuple[bool, str, dict | None]:
        """(needs a redo, why, the take it would replace). inspect=True starts a missing inspection (runs only:
        looking at the project never does)."""
        if shot["locked"]:
            return False, "locked", None
        takes = await self.db.fetchall("SELECT id, verdict FROM takes WHERE shot_id = ? AND deleted_at IS NULL ORDER BY rowid", (shot["id"],))
        if not takes:
            return False, "no takes yet (render it first)", None
        if any(t["verdict"] == "good" for t in takes):
            return False, "has a take marked good", None
        if shot["selected_take_id"]:
            sel = next((t for t in takes if t["id"] == shot["selected_take_id"]), None)
            if sel and sel["verdict"] != "reject":
                return False, "you chose its take", None
        chosen = await self.edit.chosen_take(shot)
        if not chosen:
            return True, "every take is rejected", takes[-1]
        insp = await self.inspector.get(chosen["id"])
        if not insp and not inspect:
            return False, "not inspected yet", chosen
        if not insp:
            self.inspector.ensure(chosen["id"])
        if not insp or insp["status"] == "running":
            return False, "inspecting", chosen
        if insp["status"] == "failed":
            return False, "inspection failed", chosen
        if insp["verdict"] == "flagged":
            return True, insp["summary"], chosen
        return False, "looks right", chosen

    # --- proposals ---------------------------------------------------------------

    async def propose(self, project_id: str, shot_id: str, reason: str, prompt: str | None, seed: int | None,
                      actor: str = "director") -> dict:
        shot = await shots.get_shot(self.db, shot_id, project_id)
        if shot["locked"]:
            raise RunError(f"shot {shot['position']} is locked by the owner; it can't be redone")
        reason = " ".join(str(reason or "").split())
        if len(reason) < 5:
            raise RunError("reason is required: say what's wrong with the take and what the change fixes")
        changes = {}
        if prompt is not None and str(prompt).strip() and str(prompt).strip() != shot["prompt"]:
            changes["prompt"] = shots.clean_fields({"prompt": prompt}, partial=True)["prompt"]
        if seed is not None:
            changes["seed"] = int(seed)
        if not changes:
            changes["seed"] = -1  # same prompt: at least a new seed
        run = await self.db.fetchone("SELECT id, shot_ids FROM director_runs WHERE project_id = ? AND status IN "
                                     "('running', 'paused') ORDER BY created_at DESC LIMIT 1", (project_id,))
        run_id = run["id"] if run and shot_id in json.loads(run["shot_ids"]) else None
        _, _, take = await self.needs_work(shot)
        pid, ts = new_id(), now()

        def write(c):  # a newer proposal for the same shot replaces an older pending one
            c.execute("UPDATE redo_proposals SET status = 'superseded', decided_at = ? WHERE shot_id = ? AND status = 'pending'",
                      (ts, shot_id))
            c.execute("INSERT INTO redo_proposals (id, project_id, shot_id, take_id, run_id, changes, reason, status, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                      (pid, project_id, shot_id, take and take["id"], run_id, json.dumps(changes), reason[:1000], ts))
        await self.db.transaction(write)
        await self.db.audit(actor, "redo.propose", shot_id, {"proposal_id": pid, "run_id": run_id})
        await self._changed(project_id)
        if run_id:
            self._poke(run_id)
        return {"proposal_id": pid, "run_id": run_id, "changes": changes}

    async def accept(self, proposal_id: str, actor: str) -> dict:
        """The owner's Accept: apply the change and render one take, now."""
        p = await self._proposal(proposal_id)
        if p["status"] != "pending":
            raise RunError(f"the proposal is {p['status']}")
        if p["run_id"]:
            raise RunError("this proposal belongs to a running redo run; it renders there")
        if reason := (self.held and await self.held()):
            raise RunError(reason)
        job = await self._render(p, actor)
        return {"proposal_id": proposal_id, "job_id": job["id"]}

    async def reject(self, proposal_id: str, actor: str) -> dict:
        p = await self._proposal(proposal_id)
        if p["status"] != "pending":
            raise RunError(f"the proposal is {p['status']}")
        await self.db.execute("UPDATE redo_proposals SET status = 'rejected', decided_at = ? WHERE id = ?", (now(), proposal_id))
        await self.db.audit(actor, "redo.reject", proposal_id)
        if p["run_id"]:  # inside a run: the shot is given up rather than asked again
            await self._shot_state(p["run_id"], p["shot_id"], state="given_up", note="you rejected the proposal")
            self._poke(p["run_id"])
        await self._changed(p["project_id"])
        return {"rejected": proposal_id}

    async def _proposal(self, proposal_id: str) -> dict:
        p = await self.db.fetchone("SELECT * FROM redo_proposals WHERE id = ?", (proposal_id,))
        if not p:
            raise RunError("no such proposal")
        p["changes"] = json.loads(p["changes"])
        return p

    async def _render(self, p: dict, actor: str) -> dict:
        shot = await shots.get_shot(self.db, p["shot_id"])
        if shot["locked"]:
            raise RunError("the shot was locked; it can't be redone")
        if shot["status"] not in RENDERABLE:
            raise RunError(f"the shot is {shot['status']}")
        if await self.db.fetchone(f"SELECT 1 FROM render_jobs WHERE shot_id = ? AND status IN {ACTIVE}", (shot["id"],)):
            raise RunError("the shot is already rendering")
        if p["changes"]:
            shot = await shots.update_shot(self.db, self.bus, shot, p["changes"], "director",
                                           rationale=f"redo: {p['reason']}"[:500])
        try:
            approved = await shots.approve(self.db, self.bus, shot, "owner")
            job = await self.renders.submit(approved, "owner")
        except (RenderError, shots.ShotError) as exc:
            await shots.undo_approve(self.db, self.bus, shot, "owner")
            await self.db.execute("UPDATE redo_proposals SET status = 'failed', outcome = ?, decided_at = ? WHERE id = ?",
                                  (str(exc)[:500], now(), p["id"]))
            await self._changed(p["project_id"])
            raise RunError(f"couldn't render: {exc}") from exc
        await self.db.execute("UPDATE redo_proposals SET status = 'rendering', job_id = ?, decided_at = ? WHERE id = ?",
                              (job["id"], now(), p["id"]))
        await self.db.audit(actor, "redo.render", p["id"], {"job_id": job["id"], "run_id": p["run_id"]})
        await self._changed(p["project_id"])
        return job

    # --- runs ------------------------------------------------------------------

    async def start(self, project_id: str, shot_ids: list[str] | None, caps: dict | None, actor: str) -> dict:
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
        if not project:
            raise RunError("no such project")
        if project["autonomy"] != "A3":
            raise RunError("bounded redo (A3) is off for this project; turn it on in Project settings first")
        if reason := (self.held and await self.held()):
            raise RunError(reason)
        if await self.db.fetchone("SELECT 1 FROM director_runs WHERE project_id = ? AND status IN ('running', 'paused')",
                                  (project_id,)):
            raise RunError("this project already has a redo run; stop it first")
        if await self.db.fetchone("SELECT 1 FROM batches WHERE project_id = ? AND status IN ('running', 'paused')",
                                  (project_id,)):
            raise RunError("a storyboard batch is running in this project; let it finish or cancel it first")
        caps = clean_caps(caps)
        order = await self.storyboard.order(project_id)
        if shot_ids is None:
            picked = []
            for sid in order:
                if (await self.needs_work(await shots.get_shot(self.db, sid)))[0]:
                    picked.append(sid)
        else:
            if any(s not in order for s in shot_ids):
                raise RunError("some shots aren't in this project")
            picked = [s for s in order if s in set(shot_ids)]
        if not picked:
            raise RunError("no shot needs a redo: none is flagged by inspection or fully rejected")
        rid, ts = new_id(), now()
        await self.db.execute(
            "INSERT INTO director_runs (id, project_id, status, shot_ids, caps, requested_by, created_at, updated_at) "
            "VALUES (?, ?, 'running', ?, ?, ?, ?, ?)", (rid, project_id, json.dumps(picked), json.dumps(caps), actor, ts, ts))
        await self.db.audit(actor, "run.start", rid, {"shots": picked, "caps": caps})
        await self._log(rid, None, f"started for {len(picked)} shot(s): up to {caps['max_retries']} redo(s) each, "
                                   f"{caps['max_renders']} renders, {caps['gpu_minutes']} GPU min, {caps['wall_minutes']} min")
        self._spawn(rid)
        return await self.get(rid)

    async def pause(self, run_id: str, actor: str) -> dict:
        r = await self.get(run_id)
        if r["status"] != "running":
            raise RunError(f"the run is {r['status']}")
        await self._set(run_id, "paused", "paused by the owner")
        await self.db.audit(actor, "run.pause", run_id)
        await self._log(run_id, None, "paused by you: the current render (if any) finishes, nothing new starts")
        return await self.get(run_id)

    async def resume(self, run_id: str, actor: str) -> dict:
        r = await self.get(run_id)
        if r["status"] != "paused":
            raise RunError(f"the run is {r['status']}")
        if reason := (self.held and await self.held()):
            raise RunError(reason)
        await self._set(run_id, "running", None)
        await self.db.audit(actor, "run.resume", run_id)
        await self._log(run_id, None, "resumed by you")
        self._spawn(run_id)
        return await self.get(run_id)

    async def stop(self, run_id: str, actor: str, reason: str = "stopped by the owner", cancel: bool = True) -> dict:
        r = await self.get(run_id)
        if r["status"] not in OPEN:
            raise RunError(f"the run is {r['status']}")
        await self.db.execute("UPDATE redo_proposals SET status = 'superseded', decided_at = ? WHERE run_id = ? "
                              "AND status = 'pending'", (now(), run_id))  # the owner stopped it: drop what was queued
        await self._finish(run_id, "stopped", reason)
        if cancel:
            for p in await self.db.fetchall("SELECT job_id FROM redo_proposals WHERE run_id = ? AND status = 'rendering'",
                                            (run_id,)):
                try:
                    await self.renders.cancel(p["job_id"], actor)
                except RenderError as exc:
                    log.info("run %s: cancel of %s: %s", run_id, p["job_id"], exc)
        await self.db.execute("UPDATE redo_proposals SET status = 'superseded', decided_at = ? WHERE run_id = ? "
                              "AND status = 'pending'", (now(), run_id))
        await self.db.audit(actor, "run.stop", run_id, {"reason": reason})
        self._poke(run_id)
        return await self.get(run_id)

    async def resume_all(self):
        for r in await self.db.fetchall("SELECT DISTINCT p.run_id FROM redo_proposals p JOIN director_runs r ON "
                                        "r.id = p.run_id WHERE p.status = 'pending' AND r.status NOT IN ('running', 'paused')"):
            await self._release_proposals(r["run_id"])
        for r in await self.db.fetchall("SELECT id FROM director_runs WHERE status = 'running'"):
            self._spawn(r["id"])

    async def _release_proposals(self, run_id: str):
        """A run that ended on a limit leaves its last proposal unrendered: it becomes the owner's to accept or reject."""
        await self.db.execute("UPDATE redo_proposals SET run_id = NULL WHERE run_id = ? AND status = 'pending'", (run_id,))

    def _spawn(self, run_id: str):
        if run_id not in self._tasks or self._tasks[run_id].done():
            self._wake.setdefault(run_id, asyncio.Event())
            self._tasks[run_id] = asyncio.create_task(self._loop(run_id))

    def _poke(self, run_id: str):
        if ev := self._wake.get(run_id):
            ev.set()

    async def _loop(self, run_id: str):
        try:
            while not self.closing:
                wait = await self._step(run_id)
                if wait is None:
                    return
                ev = self._wake.setdefault(run_id, asyncio.Event())
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), wait)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a run problem stops the run, visibly, never the backend
            log.exception("run %s failed", run_id)
            try:
                await self._finish(run_id, "stopped", f"stopped by an internal error: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass

    async def _step(self, run_id: str) -> float | None:
        """One decision. Returns how long to wait before the next one, or None when the run is over."""
        r = await self.get(run_id)
        if r["status"] != "running":
            return None
        if reason := (self.held and await self.held()):
            await self._set(run_id, "paused", reason)
            await self._log(run_id, None, f"paused: {reason}")
            return None
        caps = r["caps"]
        # Something of ours still rendering: wait for it (its end wakes us).
        busy = await self.db.fetchone("SELECT p.id FROM redo_proposals p WHERE p.run_id = ? AND p.status = 'rendering'",
                                      (run_id,))
        if busy:
            return 30
        if _age_s(r["created_at"]) > caps["wall_minutes"] * 60:
            return await self._finish(run_id, "stopped", f"time limit reached ({caps['wall_minutes']} min)")
        state = r["shot_state"]
        for sid in r["shot_ids"]:
            st = state.get(sid, {})
            if st.get("state") in ("fixed", "given_up"):
                continue
            try:
                shot = await shots.get_shot(self.db, sid)
            except shots.ShotError:
                await self._shot_state(run_id, sid, state="given_up", note="the shot was deleted")
                continue
            need, why, take = await self.needs_work(shot, inspect=True)
            if why in ("inspecting",):
                return 10  # the newest take is still being inspected
            if not need:
                if st.get("attempts"):
                    await self._shot_state(run_id, sid, state="fixed", note=why)
                    await self._log(run_id, sid, f"shot {shot['position']} “{shot['title']}”: {why}"
                                                 + ("" if why != "looks right" else f" (take {take['id'][:8]})"))
                else:
                    await self._shot_state(run_id, sid, state="given_up" if why != "looks right" else "fixed", note=why)
                    await self._log(run_id, sid, f"shot {shot['position']} skipped: {why}")
                continue
            if st.get("attempts", 0) >= caps["max_retries"]:
                await self._shot_state(run_id, sid, state="given_up", note=f"still flagged after {st['attempts']} redo(s): {why}")
                await self._log(run_id, sid, f"shot {shot['position']} “{shot['title']}”: out of redos ({why})")
                continue
            if r["used_renders"] >= caps["max_renders"]:
                return await self._finish(run_id, "stopped", f"render limit reached ({caps['max_renders']} renders)")
            pending = await self.db.fetchone("SELECT * FROM redo_proposals WHERE run_id = ? AND shot_id = ? AND "
                                             "status = 'pending' ORDER BY created_at DESC LIMIT 1", (run_id, sid))
            if pending:
                return await self._render_next(r, shot, pending | {"changes": json.loads(pending["changes"])})
            if st.get("asked_at") and _age_s(st["asked_at"]) < ASK_TIMEOUT_S:
                return 15  # waiting for the director's proposal
            if st.get("asked_at"):
                await self._shot_state(run_id, sid, state="given_up", note="the director proposed no redo in time")
                await self._log(run_id, sid, f"shot {shot['position']}: no proposal from the director; skipped")
                continue
            return await self._ask(r, shot, why, take, st.get("attempts", 0) + 1)
        return await self._finish(run_id, "done", self._summary(await self.get(run_id)))

    async def _render_next(self, r: dict, shot: dict, p: dict) -> float | None:
        caps = r["caps"]
        est = await self.storyboard.estimate([shot])
        est_s = est["seconds"]
        left = caps["gpu_minutes"] * 60 - r["used_gpu_s"]
        if left <= 0 or (est_s is not None and est_s > left):
            need = f"~{est_s / 60:.1f} min" if est_s is not None else "more"
            return await self._finish(r["id"], "stopped", f"GPU budget reached: the next render needs {need}, "
                                                          f"{max(left, 0) / 60:.1f} of {caps['gpu_minutes']} min left")
        # Queue-aware: one FilmLab render at a time anywhere, and WanGP's own jobs go first.
        if await self.db.fetchone(f"SELECT 1 FROM render_jobs WHERE status IN {ACTIVE}") or \
                await self.db.fetchone(f"SELECT 1 FROM image_jobs WHERE status IN {ACTIVE}"):
            return 15
        try:
            job = await self._render(p, "owner")
        except RunError as exc:
            await self._shot_state(r["id"], shot["id"], state="given_up", note=str(exc))
            await self._log(r["id"], shot["id"], f"shot {shot['position']}: {exc}")
            return 1
        st = r["shot_state"].get(shot["id"], {})
        await self._shot_state(r["id"], shot["id"], attempts=st.get("attempts", 0) + 1, asked_at=None, state="rendering")
        await self.db.execute("UPDATE director_runs SET used_renders = used_renders + 1, updated_at = ? WHERE id = ?",
                              (now(), r["id"]))
        what = ", ".join(f"{k} changed" if k == "prompt" else f"seed {v}" for k, v in p["changes"].items())
        await self._log(r["id"], shot["id"], f"shot {shot['position']} “{shot['title']}”: redo {st.get('attempts', 0) + 1} "
                                             f"rendering ({what}): {p['reason']}")
        return 30

    async def _ask(self, r: dict, shot: dict, why: str, take: dict | None, attempt: int) -> float:
        notes = await self.db.fetchall("SELECT verdict, notes FROM takes WHERE shot_id = ? AND (notes != '' OR verdict "
                                       "IS NOT NULL) ORDER BY rowid", (shot["id"],))
        text = "\n".join([
            f"[Redo run] Shot {shot['position']} “{shot['title']}” (shot_id {shot['id']}), redo {attempt} of "
            f"{r['caps']['max_retries']}.",
            f"The take the edit would use{' (take_id ' + take['id'] + ')' if take else ''} needs a redo: {why}.",
            *([f"Owner's verdicts and notes so far: " + "; ".join(
                f"{n['verdict'] or 'no verdict'}{': ' + n['notes'] if n['notes'] else ''}" for n in notes)] if notes else []),
            f"Current prompt: “{shot['prompt']}”",
            "Call inspect_take for the details if you need them, then call propose_redo ONCE with a revised prompt that "
            "fixes exactly these problems (keep what works; follow the setting and sound rules), or the same prompt and "
            "seed -1 if only luck was bad. Give the reason in one sentence. Don't request renders: FilmLab renders the "
            "proposal within the owner's limits and inspects the new take.",
        ])
        try:
            await self.chat_send(r["project_id"], text, expect=("propose_redo", "a redo proposal"))
        except Exception as exc:  # noqa: BLE001 - the chat is busy (the owner is talking): try again shortly
            log.info("run %s: director busy (%s)", r["id"], exc)
            return 20
        await self._shot_state(r["id"], shot["id"], asked_at=now(), state="asked")
        await self._log(r["id"], shot["id"], f"shot {shot['position']}: asked the director for redo {attempt} ({why})")
        return 15

    # --- events -------------------------------------------------------------------

    async def job_finished(self, job: dict, status: str):
        """Called by the render service when any render ends."""
        p = await self.db.fetchone("SELECT * FROM redo_proposals WHERE job_id = ? AND status = 'rendering'", (job["id"],))
        if not p:
            return
        j = await self.renders.job(job["id"])
        take = await self.db.fetchone("SELECT id FROM takes WHERE job_id = ?", (job["id"],))
        gpu_s = max(0.0, (_ts(j["finished_at"]) - _ts(j["submitted_at"])).total_seconds()) if j["finished_at"] else 0
        outcome = {"done": "rendered; inspecting the new take"}.get(status, f"render {status}: {j.get('error') or ''}")
        await self.db.execute("UPDATE redo_proposals SET status = ?, result_take_id = ?, outcome = ? WHERE id = ?",
                              ("done" if status == "done" else "failed", take and take["id"], outcome[:500], p["id"]))
        if p["run_id"]:
            await self.db.execute("UPDATE director_runs SET used_gpu_s = used_gpu_s + ?, updated_at = ? WHERE id = ?",
                                  (gpu_s, now(), p["run_id"]))
            await self._shot_state(p["run_id"], p["shot_id"], state="inspecting" if status == "done" else "render_failed")
            await self._log(p["run_id"], p["shot_id"], f"render {status} after {gpu_s / 60:.1f} min")
            self._poke(p["run_id"])
        await self._changed(p["project_id"])

    async def _inspected(self, take_id: str, insp: dict | None):
        p = await self.db.fetchone("SELECT * FROM redo_proposals WHERE result_take_id = ?", (take_id,))
        if not p or not insp:
            return
        result = ("looks right: " + (((insp.get("vision") or {}).get("summary")) or insp.get("summary") or "")
                  if insp.get("verdict") == "ok" else f"still flagged: {insp.get('summary')}"
                  if insp.get("verdict") else f"inspection {insp.get('status')}: {insp.get('error') or ''}")
        await self.db.execute("UPDATE redo_proposals SET outcome = ? WHERE id = ?", (result[:500], p["id"]))
        if p["run_id"]:
            await self._log(p["run_id"], p["shot_id"], f"new take {take_id[:8]} {result}")
            self._poke(p["run_id"])
        await self._changed(p["project_id"])

    # --- helpers -----------------------------------------------------------------------

    def _summary(self, r: dict) -> str:
        fixed = sum(1 for s in r["shot_state"].values() if s.get("state") == "fixed" and s.get("attempts"))
        gave = sum(1 for s in r["shot_state"].values() if s.get("state") == "given_up")
        return (f"finished: {fixed} shot(s) fixed, {gave} left for you; {r['used_renders']} render(s), "
                f"{r['used_gpu_s'] / 60:.1f} GPU min")

    async def _finish(self, run_id: str, status: str, reason: str) -> None:
        await self.db.execute("UPDATE director_runs SET status = ?, stop_reason = ?, finished_at = ?, updated_at = ? "
                              "WHERE id = ? AND status IN ('running', 'paused')", (status, reason, now(), now(), run_id))
        await self._release_proposals(run_id)
        await self._log(run_id, None, reason)
        await self.db.audit("system", f"run.{status}", run_id, {"reason": reason})
        return None

    async def _set(self, run_id: str, status: str, reason: str | None):
        await self.db.execute("UPDATE director_runs SET status = ?, stop_reason = ?, updated_at = ? WHERE id = ?",
                              (status, reason, now(), run_id))
        r = await self.db.fetchone("SELECT project_id FROM director_runs WHERE id = ?", (run_id,))
        await self._changed(r["project_id"])

    async def _shot_state(self, run_id: str, shot_id: str, **fields):
        def write(c):
            row = c.execute("SELECT shot_state FROM director_runs WHERE id = ?", (run_id,)).fetchone()
            st = json.loads(row[0])
            st[shot_id] = {**st.get(shot_id, {}), **fields}
            c.execute("UPDATE director_runs SET shot_state = ?, updated_at = ? WHERE id = ?", (json.dumps(st), now(), run_id))
        await self.db.transaction(write)

    async def _log(self, run_id: str, shot_id: str | None, text: str):
        def write(c):
            row = c.execute("SELECT log, project_id FROM director_runs WHERE id = ?", (run_id,)).fetchone()
            entries = json.loads(row[0])[-199:] + [{"at": now(), "shot_id": shot_id, "text": text}]
            c.execute("UPDATE director_runs SET log = ?, updated_at = ? WHERE id = ?", (json.dumps(entries), now(), run_id))
            return row[1]
        project_id = await self.db.transaction(write)
        await self._changed(project_id)

    async def _changed(self, project_id: str):
        await self.bus.publish("run.updated", {}, project_id)

    async def shutdown(self):
        self.closing = True
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
