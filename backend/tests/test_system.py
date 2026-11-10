"""F7: backups (create, incremental, verify, restore drill, retention), health (integrity, files, storage,
jobs, startup), storage and GPU-sharing gates, project templates, the contact sheet, pre-migration backups."""

import json
import sqlite3

from tests.test_edit import export, patch, rendered_shot, small_project


def backups(h):
    return h.c.get("/api/backups").json()


def test_backup_is_incremental_verifies_and_drills_a_delivery(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A")
    rendered_shot(h, pid, "B")
    d = export(h, pid)
    assert d["status"] == "done"
    r = h.post("/api/backups", {"note": "before the test"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["counts"]["takes"] == 2 and first["files"] >= 5 and first["bytes"] > 0
    root = h.studio.settings.backup_dir / first["name"]
    assert (root / "filmlab.sqlite").is_file() and not (root / "media" / "cache").exists()
    assert not list(root.rglob("secrets.env"))
    # A second backup of unchanged media costs almost nothing: the files are hard links to the first.
    second = h.post("/api/backups", {}).json()
    assert second["new_bytes"] < first["bytes"] / 10
    v = h.post(f"/api/backups/{second['name']}/verify?deep=true").json()
    assert v["ok"] and v["hashed"] >= 4, v
    drill = h.post(f"/api/backups/{second['name']}/drill").json()
    assert drill["ok"] and drill["delivery"] == 1 and "identical" in drill["detail"], drill
    assert drill["schema"] == "current"
    listed = backups(h)["backups"]
    assert [b["name"] for b in listed] == [second["name"], first["name"]] and listed[0]["drill"]["ok"]
    # A damaged backup is caught.
    victim = next(p for p in (h.studio.settings.backup_dir / first["name"] / "media").rglob("*.mp4"))
    victim.unlink()
    v = h.post(f"/api/backups/{first['name']}/verify").json()
    assert not v["ok"] and any("gone" in p or "missing" in p for p in v["problems"])
    assert h.post("/api/backups/../../etc/verify").status_code == 404
    assert h.c.delete(f"/api/backups/{first['name']}", headers={"x-csrf-token": h.csrf}).status_code == 200


def test_automatic_backups_are_pruned_and_manual_ones_kept(make_harness):
    h = make_harness().login()
    small_project(h)
    assert h.c.put("/api/backups/settings", json={"keep": 2}, headers={"x-csrf-token": h.csrf}).json()["keep"] == 2
    assert h.c.put("/api/backups/settings", json={"copy_dir": "C:\\\\nope"}, headers={"x-csrf-token": h.csrf}).status_code == 422
    h.post("/api/backups", {})
    for _ in range(3):
        h.c.portal.call(h.studio.backups.create, "daily", "", "system")
    kinds = [b["kind"] for b in backups(h)["backups"]]
    assert kinds.count("daily") == 2 and kinds.count("manual") == 1


def test_backups_can_be_copied_to_a_second_folder(make_harness, tmp_path):
    h = make_harness().login()
    small_project(h)
    target = tmp_path / "copies"
    # copy_dir accepts only distro paths (/mnt/... or /home/...); the test folder lives under /tmp, so set it directly.
    h.c.portal.call(h.studio.db.kv_set, "backups.settings", {"daily": True, "keep": 10, "copy_dir": str(target)})
    b = h.post("/api/backups", {}).json()
    assert b["copied_to"] and (target / b["name"] / "manifest.json").is_file()


def test_health_reports_files_storage_and_cleans_up(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid)
    rep = h.c.get("/api/system/health").json()
    assert rep["database"]["integrity"] == "ok" and rep["database"]["foreign_key_problems"] == 0
    assert rep["files"]["missing"] == [] and rep["files"]["orphans"] == []
    # A deleted delivery's files are listed apart from orphans, and can be removed.
    e = export(h, pid)
    h.c.delete(f"/api/exports/{e['id']}", headers={"x-csrf-token": h.csrf})
    rep = h.c.get("/api/system/health").json()
    assert rep["files"]["orphans"] == [] and rep["files"]["deleted_deliveries"] == 1
    assert not list((h.studio.settings.media_dir / e["rel_dir"]).glob("*.txt"))
    assert h.post("/api/system/remove-deleted-deliveries").json()["removed"] == 1
    assert not (h.studio.settings.media_dir / e["rel_dir"]).exists()
    assert rep["storage"]["free_gb"] > 0 and rep["storage"]["takes_mb"] > 0
    assert rep["jobs"]["active"] == 0 and rep["startup"]["manual"] in (True, False)
    # A stray file and a missing one.
    stray = h.studio.settings.media_dir / pid / "stray.bin"
    stray.write_bytes(b"x" * 100)
    take = h.project(pid)["takes"][0]
    media = h.c.portal.call(h.studio.db.fetchone, "SELECT rel_path FROM media WHERE id = ?", (take["thumb_media_id"],))
    (h.studio.settings.media_dir / media["rel_path"]).unlink()
    rep = h.c.get("/api/system/health").json()
    assert f"{pid}/stray.bin" in rep["files"]["orphans"] and media["rel_path"] in rep["files"]["missing"]
    assert not rep["ok"] and any("missing" in p for p in rep["problems"])
    r = h.post("/api/system/remove-orphans", {"files": [f"{pid}/stray.bin", media["rel_path"], "../../etc/passwd"]}).json()
    assert r["removed"] == 1 and not stray.exists()
    h.c.get(f"/api/media/{take['media_id']}/waveform")
    assert h.post("/api/system/clear-caches").json()["freed_mb"] >= 0
    assert not (h.studio.settings.media_dir / "cache" / "waveforms").exists()
    assert s


def test_low_disk_and_gpu_sharing_hold_renders(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid)
    put = lambda body: h.c.put("/api/system/settings", json=body, headers={"x-csrf-token": h.csrf})
    assert put({"min_free_gb": 50, "warn_free_gb": 10}).status_code == 422
    assert put({"warn_free_gb": 2000, "min_free_gb": 2000}).status_code == 200
    r = h.post(f"/api/shots/{s['id']}/approve-and-render")
    assert r.status_code >= 400 and "GB free" in r.text
    put({"warn_free_gb": 20, "min_free_gb": 0})
    # A local Ollama model on the GPU: with "warn" a warning, with "wait" a wait (the default unloads it).
    put({"gpu_share": "warn"})
    h.vision.loaded = [{"name": "llama3.1:8b", "size_vram": 6_000_000_000},
                       {"name": "gemma4:31b-cloud", "size_vram": 0}]
    checks = h.c.get(f"/api/shots/{s['id']}/checks").json()["issues"]
    assert any("llama3.1:8b (6.0 GB)" in i["message"] and i["level"] == "warn" for i in checks)
    put({"gpu_share": "wait"})
    r = h.post(f"/api/shots/{s['id']}/approve-and-render")
    assert r.status_code >= 400 and "Ollama has llama3.1:8b" in r.text
    h.vision.loaded = []
    assert h.post(f"/api/shots/{s['id']}/approve-and-render").status_code == 200
    assert put({"gpu_share": "always"}).status_code == 422


def test_projects_from_templates_carry_their_style_into_planning(make_harness):
    h = make_harness().login()
    ts = h.c.get("/api/project-templates").json()
    assert {t["id"] for t in ts} == {"horror_teaser", "music_video", "illustrated_story", "character_dialogue"}
    r = h.post("/api/projects", {"title": "Teaser", "template": "music_video"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["template"] == "music_video" and p["default_frames"] == 73 and p["target_seconds"] == 30
    assert "No dialogue" in p["style"]
    assert h.post("/api/projects", {"title": "X", "template": "nope"}).status_code == 422
    patch(h, f"/api/projects/{p['id']}", {"brief": "A singer in neon rain"})
    from app.director.planning import plan_brief
    proj = h.c.portal.call(h.studio.db.fetchone, "SELECT * FROM projects WHERE id = ?", (p["id"],))
    brief = h.c.portal.call(plan_brief, h.studio, proj, False)
    assert "Style for this film" in brief and "short shots of 2-4 s" in brief


def test_contact_sheet_and_continuity_checks(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    sc = h.post(f"/api/projects/{pid}/scenes", {"title": "Street", "time_place": "Night, street"}).json()
    h.vision.answers = [
        {"setting_matches": True, "setting_seen": "rainy suburban street at night", "characters": [{"name": "Anthony", "visible": True}],
         "artifacts": [], "prompt_match": 4, "issues": [], "summary": "ok"},
        {"setting_matches": True, "setting_seen": "sunny beach in daylight", "characters": [], "artifacts": [],
         "prompt_match": 4, "issues": [], "summary": "ok"}]
    from tests.test_api import new_shot
    for title in ("One", "Two"):
        s = new_shot(h, pid, title=title, template="LTX-2 2.3 Distilled 1.0", frames=25, scene_id=sc["id"])
        h.post(f"/api/shots/{s['id']}/approve-and-render")
        h.wait_for(lambda: any(t["shot_id"] == s["id"] for t in h.project(pid)["takes"]), what="take")
        tid = next(t["id"] for t in h.project(pid)["takes"] if t["shot_id"] == s["id"])
        h.wait_for(lambda: (i := h.c.get(f"/api/takes/{tid}/inspection").json()["inspection"]) and i["status"] == "done",
                   what="inspection")
    sheet = h.c.get(f"/api/projects/{pid}/contact-sheet").json()
    assert [g["title"] for g in sheet["scenes"]] == ["Street"] and len(sheet["scenes"][0]["shots"]) == 2
    texts = [c["text"] for c in sheet["checks"]]
    assert any("the setting looks different" in t for t in texts), texts
    assert any("Anthony not seen any more" in t for t in texts), texts


def test_an_existing_database_is_copied_before_a_migration(tmp_path):
    from app.db import MIGRATIONS, Database
    path = tmp_path / "filmlab.sqlite"
    db = Database(path)
    db.close()
    c = sqlite3.connect(path)
    last = sorted(MIGRATIONS.glob("*.sql"))[-1].name
    c.execute("DELETE FROM schema_migrations WHERE name = ?", (last,))
    c.commit()
    c.close()
    try:
        Database(path).close()
    except sqlite3.OperationalError:
        pass  # re-applying the last migration may fail on this copy (columns exist); the copy is what matters
    assert (tmp_path / f"filmlab-pre-{last[:-4]}.sqlite").is_file()
    assert json  # keep the import used
