"""Music videos: one song under the whole film, lip-synced shot by shot or laid over the picture; and WanGP's own
page and model downloads for the cockpit."""

import json
import subprocess

import httpx

from tests.test_api import new_project, new_shot
from tests.test_library import images, refs_setup, set_refs, upload  # noqa: F401


def song_wav(seconds=12.0) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                           "-ac", "2", "-ar", "44100", "-f", "wav", "-"], capture_output=True, check=True).stdout


def patch(h, url, body):
    return h.c.patch(url, json=body, headers={"x-csrf-token": h.csrf})


def with_song(h, pid, seconds=12.0):
    """Upload a song to the project's song asset and choose it, as the setup popup does."""
    sa = h.post(f"/api/projects/{pid}/music-video/song-asset").json()
    f = upload(h, sa["variant_id"], song_wav(seconds), caption="song")
    r = patch(h, f"/api/projects/{pid}/music-video", {"song_file_id": f["file_id"]})
    assert r.status_code == 200, r.text
    return f["file_id"], r.json()


def test_setup_lists_song_driven_models_and_saves_the_choices(make_harness, monkeypatch):
    import tests.fakes as fakes
    monkeypatch.setattr(fakes, "MISSING_LORA", set())  # the Turbo H3 works here, as on the owner's install
    h = make_harness().login()
    pid = new_project(h)
    v = h.c.get(f"/api/projects/{pid}/music-video").json()
    assert v["mode"] is None and v["song"] is None
    names = [m["template"] for m in v["lipsync_models"]]
    assert names[0] == "MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps With Sound" and "Infinitalk" in names
    assert [m["suggested"] for m in v["lipsync_models"]].count(True) == 1 and v["lipsync_models"][0]["suggested"]
    assert v["speech_template"] == names[0] and v["song_models"][0]["template"].startswith("ACE-Step")
    assert all(m["note"] for m in v["lipsync_models"])
    r = patch(h, f"/api/projects/{pid}/music-video", {"mode": "lipsync", "lyrics": "[Verse]\nLoad up on guns",
                                                       "style": "90s grunge, raspy male voice", "speech_template": "Infinitalk"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["mode"], v["speech_template"], v["style"]) == ("lipsync", "Infinitalk", "90s grunge, raspy male voice")
    assert patch(h, f"/api/projects/{pid}/music-video", {"mode": "karaoke"}).status_code == 409
    assert patch(h, f"/api/projects/{pid}/music-video", {"speech_template": "LTX-2 2.3 Distilled 1.0"}).status_code == 409
    assert h.c.get("/api/projects/nope/music-video").status_code == 404


def test_a_song_driven_model_wangp_rejects_isnt_offered(make_harness):
    h = make_harness().login()  # here the Turbo H3's LoRA is missing, so WanGP refuses it
    pid = new_project(h)
    v = h.c.get(f"/api/projects/{pid}/music-video").json()
    names = [m["template"] for m in v["lipsync_models"]]
    assert "MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps With Sound" not in names
    # H3's reference model following the song comes first; the one without pictures last
    assert names[0] == "MiniMax H3 Ref2VA Pruned PDD 8 Steps" and names[-1] == "MiniMax H3 FL2VA Pruned PDD 8 Steps With Sound"
    assert v["speech_template"] == names[0]


def test_the_song_becomes_the_soundtrack_and_mutes_the_shots(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    new_shot(h, pid, template="LTX-2 2.3 Distilled 1.0", frames=73)
    patch(h, f"/api/projects/{pid}/music-video", {"mode": "soundtrack"})
    fid, v = with_song(h, pid, 12.0)
    assert v["song"]["file_id"] == fid and round(v["song"]["duration_s"]) == 12
    assert h.project(pid)["target_seconds"] == 12  # the film is as long as the song
    tl = h.c.get(f"/api/projects/{pid}/edit").json()
    music = tl["doc"]["music"]
    assert music[0]["file_id"] == fid and music[0]["gain_db"] == 0 and music[0]["duck"] is False
    assert tl["song_only"] is True
    s = h.project(pid)["shots"][0]
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["takes"], what="take")
    tl = h.c.get(f"/api/projects/{pid}/edit").json()
    assert all(seg["mute"] for seg in tl["segments"] if seg["kind"] == "clip")
    # soundtrack mode renders the shot with its usual model, not a song-driven one
    job = list(h.wangp.jobs.values())[-1]
    assert job["settings"]["model_type"] == "ltx2_22B_distilled" and "audio_guide" not in job["settings"]


def test_a_lipsync_shot_renders_from_its_own_slice_of_the_song(make_harness, images, monkeypatch):
    import tests.fakes as fakes
    monkeypatch.setattr(fakes, "MISSING_LORA", set())
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    first = new_shot(h, pid, title="Intro", frames=73)  # 3 s
    second = new_shot(h, pid, title="Verse", frames=97,  # 4 s, starting at 3 s
                      prompt="<Picture 1> sings into a microphone in <Picture 2> at dusk")
    set_refs(h, second["id"], hv, tv)
    patch(h, f"/api/projects/{pid}/music-video", {"mode": "lipsync"})
    with_song(h, pid, 12.0)
    h.post(f"/api/shots/{second['id']}/approve-and-render")
    h.wait_for(lambda: [t for t in h.project(pid)["takes"] if t["shot_id"] == second["id"]], what="take")
    s = list(h.wangp.jobs.values())[-1]["settings"]
    assert s["model_type"] == "minimax_h3_ref2va_pruned" and s["audio_prompt_type"] == "AS"
    assert s["audio_guide"] and len(s["image_refs"]) == 2 and s["video_length"] == 97
    assert "lip movements synchronized" in s["prompt"] and "<Picture 1>" in s["prompt"]
    start = h.c.portal.call(h.studio.music.shot_start, h.c.portal.call(h.studio.db.fetchone,
                                                                         "SELECT * FROM shots WHERE id = ?", (second["id"],)))
    assert start == 3.0
    slices = list(h.studio.music.slices_dir.glob("*.wav"))
    assert len(slices) == 1
    dur = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                                           str(slices[0])], capture_output=True, check=True).stdout)["format"]["duration"])
    assert abs(dur - 4.0) < 0.05
    assert first  # the first shot wasn't rendered: only the second's slice exists


def test_a_song_is_generated_from_the_lyrics_and_a_candidate_becomes_the_song(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    assert h.post(f"/api/projects/{pid}/music-video/song", {"style": "x"}).status_code == 409  # style too short
    r = h.post(f"/api/projects/{pid}/music-video/song",
               {"lyrics": "[Verse]\nLoad up on guns, bring your friends", "style": "90s grunge rock, raspy male voice",
                "seconds": 20})
    assert r.status_code == 202, r.text
    job = list(h.wangp.jobs.values())[-1]["settings"]
    job = job[0] if isinstance(job, list) else job
    assert job["model_type"] == "ace_step_v1_5_turbo_lm_1_7b" and "Load up on guns" in job["prompt"]
    v = h.wait_for(lambda: (v := h.c.get(f"/api/projects/{pid}/music-video").json())["song_candidates"] and v,
                   what="song candidates")
    out = h.post(f"/api/projects/{pid}/music-video/song/candidates/{v['song_candidates'][0]['id']}/use")
    assert out.status_code == 200, out.text
    assert out.json()["song"]["file_id"] == v["song_candidates"][0]["file_id"] and out.json()["song_files"]
    assert h.c.get(f"/api/projects/{pid}/music-video").json()["lyrics"].startswith("[Verse]")


def test_plan_brief_carries_the_song_and_lyrics(make_harness):
    from app.director.planning import plan_brief
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "Anthony sings in the gym at night."})
    patch(h, f"/api/projects/{pid}/music-video", {"mode": "lipsync", "lyrics": "Load up on guns, bring your friends"})
    with_song(h, pid, 12.0)
    p = h.c.portal.call(h.studio.db.fetchone, "SELECT * FROM projects WHERE id = ?", (pid,))
    text = h.c.portal.call(plan_brief, h.studio, p, False)
    assert "This is a music video" in text and "12.0 s" in text and "Load up on guns" in text
    assert "lip movements synchronized" in text and "Never write the lyrics" in text


def test_a_model_download_shows_wangps_status_line_and_isnt_a_stall(make_harness, tmp_path):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template="LTX-2 2.3 Distilled 1.0")
    ckpts = tmp_path / "ckpts"
    ckpts.mkdir()
    (ckpts / "model.safetensors").write_bytes(b"x" * 5000)
    h.studio.wangp_ui.ckpts = ckpts
    h.wangp.download_polls = 3
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    job = h.wait_for(lambda: (j := h.project(pid)["jobs"]) and j[0]["status_text"] and j[0], what="status line")
    assert job["status_text"].startswith("Downloading model files") and not job["stalled"]
    events = [e for e in h.c.portal.call(h.studio.bus.replay, 0) if e["type"] == "render.progress"
              and e["payload"].get("download") is not None]
    assert events and events[0]["payload"]["download"]["bytes"] >= 0
    h.wait_for(lambda: h.project(pid)["takes"], what="take after the download")
    assert h.project(pid)["jobs"][0]["status_text"] in (None, "")


def test_wangps_web_page_is_found_on_its_own_port(tmp_path):
    from app.wangp.webui import WanGPWebUI

    def handle(req: httpx.Request):
        if req.url.port == 8888 and req.url.path == "/config":
            return httpx.Response(200, text='{"components":[{"props":{"value":"<H1>Wan<SUP>GP</SUP> v19 by '
                                             '<I>DeepBeepMeep</I>"}}]}')
        if req.url.port == 7860:
            return httpx.Response(200, text='{"components": "some other gradio app"}')
        raise httpx.ConnectError("refused")
    ui = WanGPWebUI("http://127.0.0.1:8000/mcp", override="", ckpts=str(tmp_path), transport=httpx.MockTransport(handle))
    import asyncio
    found = asyncio.run(ui.locate())
    assert found == {"url": None, "port": 8888, "host_is_local": True}
    remote = WanGPWebUI("http://192.168.1.20:8000/mcp", override="", ckpts=str(tmp_path),
                        transport=httpx.MockTransport(handle))
    assert asyncio.run(remote.locate())["url"] == "http://192.168.1.20:8888/"
    none = WanGPWebUI("http://127.0.0.1:8000/mcp", override="", ckpts=str(tmp_path),
                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert asyncio.run(none.locate()) is None
    fixed = WanGPWebUI("http://127.0.0.1:8000/mcp", override="http://studio-pc:7777/", ckpts=str(tmp_path))
    assert asyncio.run(fixed.locate())["url"] == "http://studio-pc:7777/"


def test_a_shot_with_references_never_lip_syncs_with_a_model_that_drops_them(make_harness, images):
    """Live (Smells like Anthony Spirit): the chosen song-driven model took no pictures (H3 FL2VA), so the shots lost
    Anthony_Retro. A shot with references now uses a song-driven model that takes them: H3 Ref2VA, following the song."""
    h = make_harness().login()
    pid = new_project(h)
    _, _, hv, tv = refs_setup(h, images)
    s = new_shot(h, pid, title="Verse", frames=73, prompt="<Picture 1> sings in <Picture 2> at dusk")
    set_refs(h, s["id"], hv, tv)
    patch(h, f"/api/projects/{pid}/music-video", {"mode": "lipsync", "speech_template": "MiniMax H3 FL2VA Pruned PDD 8 Steps With Sound"})
    with_song(h, pid, 12.0)
    h.post(f"/api/shots/{s['id']}/approve-and-render")
    h.wait_for(lambda: h.project(pid)["takes"], what="take")
    st = list(h.wangp.jobs.values())[-1]["settings"]
    assert st["model_type"].startswith("minimax_h3_ref2va") and len(st["image_refs"]) == 2
    assert st["audio_guide"] and st["audio_prompt_type"] == "AS" and "I" in st["video_prompt_type"]
    # the render review names the model that will render it, not the shot's ordinary one
    row = next(r for r in h.c.get(f"/api/projects/{pid}/batch-review").json()["shots"] if r["shot_id"] == s["id"])
    assert row["template"].startswith("MiniMax H3 Ref2VA")


def test_a_new_project_size_can_be_given_to_the_existing_shots(make_harness):
    """Live: the project was set to 720x1280 after planning, but its 13 shots stayed 480x832 (the size is for new
    shots). Settings now offers to change them too, through the storyboard's bulk change."""
    h = make_harness().login()
    pid = new_project(h)
    a = new_shot(h, pid, title="A", resolution="832x480")
    b = new_shot(h, pid, title="B", resolution="832x480")
    h.post(f"/api/shots/{b['id']}/lock", {"locked": True})
    assert h.c.patch(f"/api/projects/{pid}", json={"default_resolution": "1280x720"},
                     headers={"x-csrf-token": h.csrf}).status_code == 200
    assert {s["resolution"] for s in h.project(pid)["shots"]} == {"832x480"}  # new shots only, unless asked
    r = h.post(f"/api/projects/{pid}/bulk", {"action": "set", "shot_ids": [a["id"], b["id"]],
                                            "fields": {"resolution": "1280x720"}}).json()
    assert r["done"] == [a["id"]] and [x["shot_id"] for x in r["skipped"]] == [b["id"]]
    assert {s["id"]: s["resolution"] for s in h.project(pid)["shots"]} == {a["id"]: "1280x720", b["id"]: "832x480"}
