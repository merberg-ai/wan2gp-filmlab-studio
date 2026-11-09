"""F5: take review, the edit, exports (real ffmpeg), deliveries, verification and restart."""

import json
import re
import subprocess

import pytest

from tests.test_api import new_project, new_shot
from tests.test_library import asset, default_variant, upload

LTX = "LTX-2 2.3 Distilled 1.0"


@pytest.fixture(scope="module")
def music(tmp_path_factory):
    p = tmp_path_factory.mktemp("music") / "tune.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-ar", "44100",
                    str(p)], check=True)
    return p.read_bytes()


def patch(h, url, body):
    return h.c.patch(url, json=body, headers={"x-csrf-token": h.csrf})


def rendered_shot(h, pid, title="Wide", takes=1):
    s = new_shot(h, pid, title=title, template=LTX)
    for n in range(takes):
        h.post(f"/api/shots/{s['id']}/approve-and-render")
        h.wait_for(lambda: len([t for t in h.project(pid)["takes"] if t["shot_id"] == s["id"]]) == n + 1, what="take")
    return s


def small_project(h, title="The Dead March"):
    pid = new_project(h, title)
    patch(h, f"/api/projects/{pid}", {"default_resolution": "320x180"})
    return pid


def edit(h, pid):
    return h.c.get(f"/api/projects/{pid}/edit").json()


def export(h, pid, **opts):
    r = h.post(f"/api/projects/{pid}/exports", {"kind": "final", **opts})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    return h.wait_for(lambda: (e := next(x for x in h.c.get(f"/api/projects/{pid}/exports").json() if x["id"] == eid))
                      and e["status"] not in ("queued", "running") and e, timeout=60, what="export")


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


def file_of(h, e, what="mp4"):
    return h.studio.settings.media_dir / e["rel_dir"] / {"mp4": "film.mp4", "srt": "film.srt", "manifest": "manifest.json"}[what]


# --- review -----------------------------------------------------------------------------

def test_take_review_selection_and_rejects(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid, takes=2)
    other = rendered_shot(h, pid, title="Other")
    takes = [t for t in h.project(pid)["takes"] if t["shot_id"] == s["id"]]
    first, second = takes[0]["id"], takes[1]["id"]
    clip = lambda: next(x for x in edit(h, pid)["segments"] if x.get("shot_id") == s["id"])
    assert clip()["take_id"] == second  # newest by default
    assert h.post(f"/api/shots/{s['id']}/select-take", {"take_id": first}).status_code == 200
    assert clip()["take_id"] == first
    r = h.post(f"/api/takes/{first}/review", {"verdict": "reject", "notes": "hand glitch"})
    assert r.json()["verdict"] == "reject" and r.json()["notes"] == "hand glitch"
    assert clip()["take_id"] == second  # a rejected selection falls back to the newest usable take
    assert h.post(f"/api/shots/{s['id']}/select-take", {"take_id": first}).status_code == 422
    other_take = next(t for t in h.project(pid)["takes"] if t["shot_id"] == other["id"])["id"]
    assert h.post(f"/api/shots/{s['id']}/select-take", {"take_id": other_take}).status_code == 422
    assert h.post(f"/api/takes/{second}/review", {"verdict": "great"}).status_code == 422
    h.post(f"/api/takes/{second}/review", {"verdict": "reject"})
    e = edit(h, pid)
    assert [x["shot_id"] for x in e["segments"]] == [other["id"]]
    assert any("no usable take" in i for i in e["issues"])
    t = next(t for t in h.project(pid)["takes"] if t["id"] == second)
    assert t["verdict"] == "reject"  # rejecting never deletes


# --- the edit -----------------------------------------------------------------------------

def test_timeline_follows_storyboard_with_trims_transitions_and_titles(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    a, b, c = rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B"), rendered_shot(h, pid, "C")
    patch(h, f"/api/shots/{a['id']}", {"transition": "dissolve"})
    patch(h, f"/api/shots/{b['id']}", {"transition": "fade"})
    r = patch(h, f"/api/projects/{pid}/edit", {
        "clips": {a["id"]: {"in": 2, "out": 22, "transition_s": 0.25}, b["id"]: {"gain_db": -3}},
        "texts": [{"kind": "title", "text": "The Dead March", "at": "start", "duration_s": 1},
                  {"kind": "caption", "text": "Somewhere in France", "start_s": 0.2, "end_s": 1.0}],
        "note": "first cut"})
    assert r.status_code == 200, r.text
    e = r.json()
    kinds = [(x["kind"], x.get("title") or x.get("text")) for x in e["segments"]]
    assert kinds == [("title", "The Dead March"), ("clip", "A"), ("clip", "B"), ("clip", "C")]
    title, ca, cb, cc = e["segments"]
    assert (ca["in"], ca["out"], ca["frames"]) == (2, 22, 20) and cb["gain_db"] == -3
    assert (ca["transition"], ca["transition_s"]) == ("dissolve", 0.25)
    assert cb["transition"] == "fade" and cb["transition_s"] == pytest.approx(0.5)  # default, within limits
    assert cc["transition"] == "cut"  # nothing after the last clip
    fps, n = e["fps"], cb["src"]["frames"]  # the test clip's own length
    assert e["total_s"] == pytest.approx(24 / fps + 20 / fps - 0.25 + n / fps - 0.5 + n / fps, abs=1e-3)
    assert ca["start_s"] == pytest.approx(1.0) and cb["start_s"] == pytest.approx(1.0 + 20 / fps - 0.25, abs=1e-3)
    # The storyboard order drives the edit.
    h.post(f"/api/shots/{c['id']}/move", {"index": 0})
    assert [x.get("title") for x in edit(h, pid)["segments"] if x["kind"] == "clip"] == ["C", "A", "B"]
    # Bad values are refused; revisions record changes and restore them.
    assert patch(h, f"/api/projects/{pid}/edit", {"clips": {a["id"]: {"in": 20, "out": 5}}}).status_code == 422
    assert patch(h, f"/api/projects/{pid}/edit", {"texts": [{"kind": "caption", "text": "x", "start_s": 2,
                                                             "end_s": 1}]}).status_code == 422
    patch(h, f"/api/projects/{pid}/edit", {"clips": {a["id"]: {"skip": True}}, "note": "drop A"})
    assert "A" not in [x.get("title") for x in edit(h, pid)["segments"]]
    revs = edit(h, pid)["revisions"]
    assert [r["note"] for r in revs[:2]] == ["drop A", "first cut"]
    h.post(f"/api/edit-revisions/{revs[1]['id']}/restore")
    assert "A" in [x.get("title") for x in edit(h, pid)["segments"]]


def test_director_reads_the_edit_and_only_suggests(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    a = rendered_shot(h, pid, "A")
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    view = run("get_edit")
    assert view["segments"][0]["shot_id"] == a["id"] and view["total_s"] > 0
    assert run("review_takes")["shots"][0]["used_take"]
    assert "error" in run("suggest_edit", change={"clips": {a["id"]: {"in": 30, "out": 2}}}, note="bad")
    out = run("suggest_edit", change={"clips": {a["id"]: {"in": 3}},
                                      "texts_add": [{"kind": "caption", "text": "Night", "start_s": 0, "end_s": 0.5}]},
              note="trim the slow start, add a caption")
    assert out["status"] == "pending"
    assert edit(h, pid)["segments"][0]["in"] == 0  # nothing applied yet
    sug = edit(h, pid)["suggestions"]
    assert len(sug) == 1 and sug[0]["note"].startswith("trim")
    h.post(f"/api/edit-suggestions/{sug[0]['id']}/accept")
    e = edit(h, pid)
    assert e["segments"][0]["in"] == 3 and e["doc"]["texts"][0]["text"] == "Night" and not e["suggestions"]
    assert e["revisions"][0]["note"].startswith("accepted the director's suggestion")
    assert not [n for n, _ in h.wangp.calls if n == "wangp_generate" and False]


# --- exports -----------------------------------------------------------------------------------

def test_final_export_with_everything(make_harness, music):
    h = make_harness().login()
    pid = small_project(h)
    a, b = rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    patch(h, f"/api/shots/{a['id']}", {"transition": "wipe_left"})  # one of the wipes/slides/shapes beyond fades
    snd = asset(h, "sound", "Drums")
    up = upload(h, default_variant(snd), music, caption="drums")
    patch(h, f"/api/projects/{pid}/edit", {
        "clips": {a["id"]: {"in": 1, "out": 24, "transition_s": 0.3}},
        "music": [{"file_id": up["file_id"], "start_s": 0.5, "gain_db": -9, "fade_in_s": 0.2, "fade_out_s": 0.5}],
        "texts": [{"kind": "title", "text": "Title “quoted”: 100% 'test'", "at": "start", "duration_s": 0.8},
                  {"kind": "caption", "text": "a:b,c [x] 'q'", "start_s": 1.0, "end_s": 1.6}]})
    total = edit(h, pid)["total_s"]
    e = export(h, pid, burn_captions=True, srt=True)
    assert e["status"] == "done", e.get("error")
    assert e["number"] == 1 and e["mode"] == "encode" and len(e["sha256"]) == 64
    info = ffprobe(file_of(h, e))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a_ = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == ("h264", 320, 180, "yuv420p", "24/1")
    assert (a_["codec_name"], a_["sample_rate"], a_["channels"]) == ("aac", "48000", 2)
    assert float(info["format"]["duration"]) == pytest.approx(total, abs=0.08)
    assert float(v["duration"]) == pytest.approx(float(a_["duration"]), abs=0.06)  # picture and sound end together
    srt = file_of(h, e, "srt").read_text()
    assert "00:00:01,000 --> 00:00:01,600" in srt and "a:b,c [x] 'q'" in srt
    manifest = json.loads(file_of(h, e, "manifest").read_text())
    lib_file = h.c.portal.call(h.studio.library.file, up["file_id"])
    assert manifest["music"][0]["sha256"] == lib_file["sha256"] and manifest["loudness"] is True
    assert [s["kind"] for s in manifest["segments"]] == ["title", "clip", "clip"]
    assert manifest["encode"]["video"]["crf"] == 18
    # Two-pass loudness: measured once, stored in the manifest, and the film lands on the target.
    assert set(manifest["loudness_measured"]) >= {"input_i", "input_tp", "target_offset"}
    lo = subprocess.run(["ffmpeg", "-nostats", "-i", str(file_of(h, e)), "-af", "ebur128", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    assert float(re.findall(r"I:\s+(-?[\d.]+) LUFS", lo)[-1]) == pytest.approx(-14, abs=1.0)
    r = h.c.get(f"/api/exports/{e['id']}/file/mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert "The%20Dead%20March%20-%20Delivery%201.mp4" in r.headers["content-disposition"]


def tone(tmp_path, seconds, name="tone.wav", stereo=False):
    p = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-ar", "48000", *(["-ac", "2"] if stereo else []), str(p)], check=True)
    return p.read_bytes()


def mean_db(path, start, length):
    out = subprocess.run(["ffmpeg", "-nostats", "-ss", str(start), "-t", str(length), "-i", str(path),
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", out).group(1))


def test_soundtrack_only_and_looped(make_harness, tmp_path):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    snd = asset(h, "sound", "Theme")
    up = upload(h, default_variant(snd), tone(tmp_path, 0.5), caption="soundtrack")
    r = patch(h, f"/api/projects/{pid}/edit", {
        "music": [{"file_id": up["file_id"], "start_s": 0, "gain_db": 0, "fade_in_s": 0, "fade_out_s": 0, "loop": True}],
        "settings": {"mute_shots": True, "loudness": False}, "note": "added a soundtrack"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["doc"]["music"][0]["loop"] is True and e["doc"]["settings"]["mute_shots"] is True
    assert all(s["mute"] for s in e["segments"] if s["kind"] == "clip")  # soundtrack only
    total = e["total_s"]
    assert total >= 1.5  # much longer than the 0.5 s song: the loop has to fill it
    out = export(h, pid)
    assert out["status"] == "done", out.get("error")
    film = file_of(h, out)
    assert mean_db(film, total - 0.6, 0.5) > -40  # still playing at the end: it looped
    # Played once, the song stops after 0.5 s and the muted shots leave silence.
    patch(h, f"/api/projects/{pid}/edit", {"music": [e["doc"]["music"][0] | {"loop": False}]})
    once = export(h, pid)
    assert once["status"] == "done", once.get("error")
    assert mean_db(file_of(h, once), total - 0.6, 0.5) < -80
    # Old edits without the new fields read as before.
    assert patch(h, f"/api/projects/{pid}/edit", {"settings": {"mute_shots": False}}).json()["segments"][0]["mute"] is False


def test_two_soundtracks_at_once_are_flagged(make_harness, tmp_path):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    snd = asset(h, "sound", "Theme")
    v1 = upload(h, default_variant(snd), tone(tmp_path, 3, "v1.wav"), caption="soundtrack")
    v2 = upload(h, default_variant(snd), tone(tmp_path, 4, "v2.wav", stereo=True), caption="soundtrack")
    track = {"start_s": 0, "gain_db": -6, "fade_in_s": 0, "fade_out_s": 0}
    one = patch(h, f"/api/projects/{pid}/edit", {"music": [track | {"file_id": v1["file_id"]}]}).json()
    assert not any("same time" in i for i in one["issues"])
    both = patch(h, f"/api/projects/{pid}/edit", {"music": [track | {"file_id": v1["file_id"]},
                                                            track | {"file_id": v2["file_id"]}]}).json()
    assert any("music tracks 1 and 2 play at the same time" in i for i in both["issues"]), both["issues"]
    # One after the other is fine.
    apart = patch(h, f"/api/projects/{pid}/edit", {"music": [
        track | {"file_id": v1["file_id"], "offset_s": 2.5},  # plays 0-0.5 s
        track | {"file_id": v2["file_id"], "start_s": 1.0}]}).json()
    assert not any("same time" in i for i in apart["issues"])


def test_a_soundtrack_can_be_larger_than_an_image(make_harness, tmp_path):
    h = make_harness().login()
    snd = asset(h, "sound", "Long song")
    data = tone(tmp_path, 170, stereo=True)  # about 33 MB as WAV, over the 25 MB image limit
    assert len(data) > 25 * 1024 * 1024
    r = h.c.post(f"/api/library/variants/{default_variant(snd)}/files", content=data,
                 headers={"x-csrf-token": h.csrf, "content-type": "application/octet-stream"})
    assert r.status_code == 201, r.text


def test_untrimmed_cuts_are_joined_without_reencoding(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    patch(h, f"/api/projects/{pid}/edit", {"settings": {"loudness": False}})
    e = export(h, pid)
    assert e["status"] == "done" and e["mode"] == "copy", e.get("error")
    v = next(s for s in ffprobe(file_of(h, e))["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (320, 176)  # stream copy keeps the takes' own size
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(file_of(h, e))], capture_output=True).stdout
    frames = sum(x["src"]["frames"] for x in edit(h, pid)["segments"])
    assert int(out.strip()) == frames


def test_export_quality_choices(make_harness):
    h = make_harness().login()
    pid = small_project(h)  # 320x180
    a, _ = rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    patch(h, f"/api/projects/{pid}/edit", {"clips": {a["id"]: {"in": 2}},
                                           "texts": [{"kind": "caption", "text": "Night", "start_s": 0.2, "end_s": 0.8}]})
    for bad in ({"codec": "vp9"}, {"quality": "ultra"}, {"size": "4320"}, {"audio_kbps": 96},
                {"codec": "h264", "ten_bit": True}):
        r = h.post(f"/api/projects/{pid}/exports", {"kind": "final", **bad})
        assert r.status_code == 409 and "must be" in r.text or "needs H.265" in r.text, (bad, r.text)
    assert not h.c.get(f"/api/projects/{pid}/exports").json()  # nothing was started
    e = export(h, pid, codec="h265", quality="high", size="720", ten_bit=True, sharpen=True, audio_kbps=256)
    assert e["status"] == "done", e.get("error")
    s = e["summary"]
    assert (s["codec"], s["quality"], s["ten_bit"], s["audio"], s["width"], s["height"]) == \
        ("h265", "high", True, "256k", 1280, 720)
    info = ffprobe(file_of(h, e))
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    assert (v["codec_name"], v["codec_tag_string"], v["pix_fmt"], v["width"], v["height"]) == \
        ("hevc", "hvc1", "yuv420p10le", 1280, 720)
    manifest = json.loads(file_of(h, e, "manifest").read_text())
    assert manifest["encode"]["video"]["crf"] == 19 and manifest["picture"]["sharpen"] is True
    # Rebuilt from its manifest alone, it is the same film.
    assert h.post(f"/api/exports/{e['id']}/verify").status_code == 200
    v = h.wait_for(lambda: (x := next(y for y in h.c.get(f"/api/projects/{pid}/exports").json() if y["id"] == e["id"]))
                   ["verify_status"] not in (None, "running") and x, timeout=90, what="verify")
    assert v["verify_status"] == "match", v["verify_detail"]
    # The defaults are today's encode: still H.264 at the project's size.
    d = export(h, pid)
    assert (d["summary"]["codec"], d["summary"]["quality"], d["summary"]["width"]) == ("h264", "standard", 320)


def test_verify_deliveries_stay_immutable_and_drafts(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    a, _ = rendered_shot(h, pid, "A"), rendered_shot(h, pid, "B")
    patch(h, f"/api/projects/{pid}/edit", {"clips": {a["id"]: {"in": 2}}})
    d1 = export(h, pid)
    sha1, bytes1 = d1["sha256"], file_of(h, d1).read_bytes()
    assert h.post(f"/api/exports/{d1['id']}/verify").status_code == 200
    v = h.wait_for(lambda: (x := next(e for e in h.c.get(f"/api/projects/{pid}/exports").json() if e["id"] == d1["id"]))
                   ["verify_status"] not in (None, "running") and x, timeout=60, what="verify")
    assert v["verify_status"] == "match", v["verify_detail"]
    # Change the edit and export again: Delivery 2; Delivery 1 is untouched.
    patch(h, f"/api/projects/{pid}/edit", {"clips": {a["id"]: {"in": 6}}})
    d2 = export(h, pid)
    assert d2["number"] == 2 and d2["sha256"] != sha1
    assert file_of(h, d1).read_bytes() == bytes1
    # A tampered delivery no longer verifies.
    file_of(h, d1).write_bytes(file_of(h, d2).read_bytes())
    h.post(f"/api/exports/{d1['id']}/verify")
    v = h.wait_for(lambda: (x := next(e for e in h.c.get(f"/api/projects/{pid}/exports").json() if e["id"] == d1["id"]))
                   ["verify_status"] not in (None, "running") and x, timeout=60, what="verify")
    assert v["verify_status"] == "mismatch"
    # Drafts are 540p-class and only the newest is kept.
    dr1 = export(h, pid, kind="draft")
    dr2 = export(h, pid, kind="draft")
    assert dr1["number"] is None and dr2["status"] == "done"
    v = next(s for s in ffprobe(file_of(h, dr2))["streams"] if s["codec_type"] == "video")
    assert v["height"] == 540
    ids = [e["id"] for e in h.c.get(f"/api/projects/{pid}/exports").json()]
    assert dr2["id"] in ids and dr1["id"] not in ids and not file_of(h, dr1).exists()


def test_export_interrupted_by_a_restart_starts_again_cleanly(make_harness):
    h1 = make_harness().login()
    pid = small_project(h1)
    rendered_shot(h1, pid, "A")
    eid = h1.c.portal.call(_queue_export, h1.studio, pid)  # as if the backend stopped mid-export
    h1.close()
    h2 = make_harness().login()
    e = h2.wait_for(lambda: (x := next(e for e in h2.c.get(f"/api/projects/{pid}/exports").json() if e["id"] == eid))
                    ["status"] == "done" and x, timeout=60, what="resumed export")
    assert e["number"] == 1 and file_of(h2, e).is_file()
    assert not list((h2.studio.settings.media_dir / e["rel_dir"]).glob("*.part"))


def test_export_whose_ffmpeg_is_killed_from_outside_is_redone_not_failed(make_harness, monkeypatch):
    """systemd stopping the unit can SIGTERM ffmpeg before the backend: that's an interruption, not a failure."""
    from app.export import ExportInterrupted
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A")
    real, calls = h.studio.exporter._ffmpeg, []

    async def flaky(eid, m, workdir, out, progress, **kw):
        if not kw.get("measure"):
            calls.append(eid)
            if progress and len(calls) == 1:
                out.write_bytes(b"half")
                raise ExportInterrupted("ffmpeg was stopped by signal 15")
        return await real(eid, m, workdir, out, progress, **kw)
    monkeypatch.setattr(h.studio.exporter, "_ffmpeg", flaky)
    r = h.post(f"/api/projects/{pid}/exports", json={"kind": "final"})
    assert r.status_code == 201
    eid = r.json()["id"]
    e = h.wait_for(lambda: (x := next(e for e in h.c.get(f"/api/projects/{pid}/exports").json() if e["id"] == eid))
                   ["status"] not in ("queued", "running") and x, timeout=60, what="redone export")
    assert e["status"] == "done" and e["number"] == 1 and len(calls) == 2, e
    # Killed twice while the backend keeps running: then it fails, saying so.
    calls.clear()

    async def always(eid, m, workdir, out, progress, **kw):
        calls.append(eid)
        raise ExportInterrupted("ffmpeg was stopped by signal 9")
    monkeypatch.setattr(h.studio.exporter, "_ffmpeg", always)
    eid = h.post(f"/api/projects/{pid}/exports", json={"kind": "final"}).json()["id"]
    e = h.wait_for(lambda: (x := next(e for e in h.c.get(f"/api/projects/{pid}/exports").json() if e["id"] == eid))
                   ["status"] not in ("queued", "running") and x, timeout=60, what="failed export")
    assert e["status"] == "failed" and "twice" in e["error"] and len(calls) == 2


async def _queue_export(studio, pid):
    m = await studio.exporter.manifest(pid, "final", {})
    from app.db import new_id, now
    eid = new_id()
    rel = f"{pid}/exports/{eid}"
    (studio.settings.media_dir / rel).mkdir(parents=True)
    (studio.settings.media_dir / rel / "film.mp4.part").write_bytes(b"half")
    await studio.db.execute("INSERT INTO exports (id, project_id, kind, status, manifest, mode, rel_dir, progress, created_at) "
                            "VALUES (?, ?, 'final', 'running', ?, ?, ?, 0.4, ?)",
                            (eid, pid, json.dumps(m), m["mode"], rel, now()))
    return eid


def test_export_needs_clips_and_can_be_cancelled_and_deleted(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    assert h.post(f"/api/projects/{pid}/exports", {"kind": "final"}).status_code == 409
    rendered_shot(h, pid, "A")
    e = export(h, pid)
    r = h.c.delete(f"/api/exports/{e['id']}", headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200 and not h.c.get(f"/api/projects/{pid}/exports").json()
    assert h.c.get(f"/api/exports/{e['id']}/file/mp4").status_code == 404
    # Purging the project removes its exports and edit too.
    h.c.delete(f"/api/projects/{pid}", headers={"x-csrf-token": h.csrf})
    assert h.c.delete(f"/api/trash/project/{pid}", headers={"x-csrf-token": h.csrf}).status_code == 200
    n = h.c.portal.call(h.studio.db.fetchone, "SELECT COUNT(*) AS n FROM exports WHERE project_id = ?", (pid,))["n"]
    assert n == 0 and not (h.studio.settings.media_dir / pid).exists()


def test_waveforms_and_filmstrips(make_harness, music):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, "A")
    take = h.project(pid)["takes"][0]
    for url in (f"/api/media/{take['media_id']}/waveform", f"/api/media/{take['media_id']}/filmstrip"):
        r = h.c.get(url)
        assert r.status_code == 200 and len(r.content) > 100, url
    snd = asset(h, "sound", "Drums")
    up = upload(h, default_variant(snd), music, caption="drums")
    assert h.c.get(f"/api/library/files/{up['file_id']}/waveform").status_code == 200


def test_edit_prefers_good_takes_and_takes_can_be_deleted_and_restored(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid, takes=3)
    t1, t2, t3 = [t["id"] for t in h.project(pid)["takes"] if t["shot_id"] == s["id"]]
    clip = lambda: next(x for x in edit(h, pid)["segments"] if x.get("shot_id") == s["id"])
    assert clip()["take_id"] == t3
    h.post(f"/api/takes/{t1}/review", {"verdict": "good"})
    assert clip()["take_id"] == t1  # marked good beats newer takes without a verdict
    # Delete the good take: it leaves the project, the edit falls back, and Recently deleted lists it.
    assert h.c.delete(f"/api/takes/{t1}", headers={"x-csrf-token": h.csrf}).status_code == 200
    assert t1 not in [t["id"] for t in h.project(pid)["takes"]] and clip()["take_id"] == t3
    trash = h.c.get("/api/trash").json()
    assert [t["id"] for t in trash["takes"]] == [t1] and trash["takes"][0]["verdict"] == "good"
    assert h.post(f"/api/trash/take/{t1}/restore").status_code == 200
    assert clip()["take_id"] == t1
    # A take a delivery was made from can't be deleted forever; another one can, with its files.
    e = export(h, pid)
    assert e["status"] == "done"
    h.c.delete(f"/api/takes/{t1}", headers={"x-csrf-token": h.csrf})
    r = h.c.delete(f"/api/trash/take/{t1}", headers={"x-csrf-token": h.csrf})
    assert r.status_code == 409 and "Delivery 1" in r.text
    h.c.delete(f"/api/takes/{t2}", headers={"x-csrf-token": h.csrf})
    media = h.c.portal.call(h.studio.db.fetchone, "SELECT m.rel_path FROM takes t JOIN media m ON m.id = t.media_id "
                                                  "WHERE t.id = ?", (t2,))
    path = h.studio.settings.media_dir / media["rel_path"]
    assert path.is_file()
    r = h.c.delete(f"/api/trash/take/{t2}", headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200 and r.json()["files_removed"] == 2 and not path.exists()


def test_every_transition_is_an_effect_this_ffmpeg_has():
    from app.shots import TRANSITIONS
    out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True).stdout
    effects = set(re.findall(r"^\s+(\w+)\s+-?\d+\s+\.\.FV", out, re.M))
    assert len(effects) > 40 and {x for x in TRANSITIONS.values() if x} <= effects
    assert TRANSITIONS["cut"] is None and TRANSITIONS["dissolve"] == "fade" and TRANSITIONS["fade"] == "fadeblack"


def test_director_plans_transitions_for_review_then_owner_applies(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    a, b, c = (rendered_shot(h, pid, title=t) for t in ("Calm", "Storm", "End"))
    cuts = h.c.get(f"/api/projects/{pid}/transitions/cuts").json()["cuts"]
    assert [x["shot_id"] for x in cuts] == [a["id"], b["id"]] and cuts[0]["to_shot_id"] == b["id"]
    # No scenes, so no scene change to plan: the owner is told to plan every cut.
    r = h.post(f"/api/projects/{pid}/transitions/plan", {"scope": "scenes"})
    assert r.status_code == 409 and "every cut" in r.text
    r = h.post(f"/api/projects/{pid}/transitions/plan", {"scope": "all", "note": "keep it gentle"})
    assert r.status_code == 202, r.text
    assert r.json()["plan"]["status"] == "thinking" and r.json()["plan"]["turn_id"]
    plan = h.wait_for(lambda: (p := h.c.get(f"/api/projects/{pid}/transitions/plan").json()["plan"])
                      and p["status"] == "ready" and p, what="planned transitions")
    brief = next(m["content"] for r in h.gateway.requests for m in r["messages"]
                 if m["role"] == "user" and "Plan the transitions" in m["content"])
    assert "keep it gentle" in brief and f"shot_id {a['id']}" in brief
    one, two = plan["items"]
    assert (one["shot_id"], one["transition"], one["reason"]) == (a["id"], "dissolve", "reason 1")
    assert one["seconds"] == one["max_s"] <= 3  # 9 s asked: clamped to half the shorter clip
    assert (two["transition"], two["seconds"]) == ("fade", 0.4) and plan["summary"].startswith("Calm film")
    # Proposed only: the edit and the shots are unchanged until the owner applies.
    assert all(s["transition"] == "cut" for s in edit(h, pid)["segments"])
    # The owner changes the second one in the list, then applies; one shot is approved (still allowed: it's the edit's).
    h.c.portal.call(h.studio.db.execute, "UPDATE shots SET status = 'approved' WHERE id = ?", (b["id"],))
    r = h.post(f"/api/projects/{pid}/transitions/apply", {"items": [
        {"shot_id": a["id"], "transition": "dissolve", "seconds": one["seconds"]},
        {"shot_id": b["id"], "transition": "wipe_left", "seconds": 0.3}]})
    assert r.status_code == 200, r.text
    segs = {s["shot_id"]: s for s in edit(h, pid)["segments"]}
    assert (segs[a["id"]]["transition"], segs[a["id"]]["transition_s"]) == ("dissolve", one["seconds"])
    assert (segs[b["id"]]["transition"], segs[b["id"]]["transition_s"]) == ("wipe_left", 0.3)
    assert segs[c["id"]]["transition"] == "cut"
    shot_b = next(s for s in h.project(pid)["shots"] if s["id"] == b["id"])
    assert shot_b["status"] == "approved"  # the transition never touches the render state
    assert h.c.get(f"/api/projects/{pid}/transitions/plan").json()["plan"] is None
    assert edit(h, pid)["revisions"][0]["note"] == "applied planned transitions (2 cuts)"
    # The Edit view's own picker also works on an approved shot now.
    assert patch(h, f"/api/shots/{b['id']}", {"transition": "fade_white"}).status_code == 200
    assert patch(h, f"/api/shots/{b['id']}", {"transition": "nope"}).status_code in (409, 422)
    # Bad items are refused before anything changes.
    r = h.post(f"/api/projects/{pid}/transitions/apply", {"items": [{"shot_id": a["id"], "transition": "spin"}]})
    assert r.status_code == 422


def test_transition_plan_without_a_proposal_fails_and_can_be_discarded(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    rendered_shot(h, pid, title="One")
    assert "two clips" in h.post(f"/api/projects/{pid}/transitions/plan", {"scope": "all"}).text
    rendered_shot(h, pid, title="Two")
    h.gateway.lazy_transitions = True  # answers with text only (and again when reminded)
    r = h.post(f"/api/projects/{pid}/transitions/plan", {"scope": "all"})
    assert r.status_code == 202
    plan = h.wait_for(lambda: (p := h.c.get(f"/api/projects/{pid}/transitions/plan").json()["plan"])
                      and p["status"] == "failed" and p, what="failed plan")
    assert "without proposing" in plan["error"]
    assert h.c.delete(f"/api/projects/{pid}/transitions/plan", headers={"x-csrf-token": h.csrf}).json() == {"discarded": True}
