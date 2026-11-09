"""A shot's sound fields, the MiniMax H3 prompt FilmLab compiles from them, and character voice samples."""

import subprocess

import pytest

from app import sound
from tests.test_api import new_project, new_shot
from tests.test_library import asset, default_variant, images, refs_setup, set_refs, upload  # noqa: F401 (fixture)

REFS = [{"position": 1, "asset_id": "a1", "asset_name": "Anthony_science", "kind": "character"},
        {"position": 2, "asset_id": "a2", "asset_name": "Jim_science", "kind": "character"},
        {"position": 3, "asset_id": "a3", "asset_name": "jim_lab", "kind": "location"},
        {"position": 4, "asset_id": "a1", "asset_name": "Anthony_science", "kind": "character"}]
PROMPT = ("Medium shot in <Picture 3>. <Picture 1> lies on a workbench, jolts awake and turns to <Picture 2>. "
          "Sound: servo whirr, Anthony says \"Dad!\".")


@pytest.fixture(scope="module")
def voice(tmp_path_factory):
    p = tmp_path_factory.mktemp("voice") / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=180:duration=5", "-ar", "32000",
                    str(p)], check=True)
    return p.read_bytes()


def test_clean_sound_normalises_and_reports():
    s = sound.clean_sound({"dialogue": [{"who": "Anthony_science", "line": "“Dad!”", "delivery": "amazed"}],
                           "soundscape": "servo whirr", "music": "none"})
    assert s == {"lines": [{"speaker": "Anthony_science", "text": "Dad!", "delivery": "amazed", "language": ""}],
                 "ambience": "servo whirr", "music": "", "refine": False}
    assert sound.clean_sound(None) is None and sound.clean_sound({}) is None
    with pytest.raises(sound.SoundError, match="text is required"):
        sound.clean_sound({"lines": [{"speaker": "Jim"}]})
    with pytest.raises(sound.SoundError, match="language must be one of"):
        sound.clean_sound({"lines": [{"speaker": "Jim", "text": "Hi", "language": "Klingon"}]})


def test_h3_prompt_has_exact_words_subjects_and_sections():
    s = sound.clean_sound({"lines": [{"speaker": "anthony science", "text": "Dad!", "delivery": "soft, amazed"},
                                     {"speaker": "Jim_science", "text": "Great Scott! He's alive!"}],
                           "ambience": "servo whirr, electrical hum"})
    p = sound.compile_h3(PROMPT, s, REFS, [{"asset_id": "a1", "n": 1}], "Anthony wakes up")
    sections = [ln.split(":")[0] for ln in p.splitlines() if ln.split(":")[0] in (
        "subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape",
        "non_diegetic_music")]
    assert sections == ["subject_definitions", "summary", "retention_analysis", "detailed_description",
                        "overall_soundscape", "non_diegetic_music"]
    # one subject per asset: Anthony's two pictures are views of the same character
    assert "<Subject 1> is Anthony_science from <Picture 1>, <Picture 4>" in p and "<Subject 3> is the setting jim_lab" in p
    assert "<Audio 1> supplies the reference for <Subject 1>'s voice" in p and "audio reference]" in p
    assert "[Shot 1] Medium shot in <Subject 3>. <Subject 1> lies on a workbench" in p
    assert "Sound:" not in p and '"Dad!"' not in p  # the old free-text sound tail is replaced by the fields
    assert "<Subject 1> says, soft, amazed, in the voice referenced from <Audio 1>, (S1) <d>[English] Dad!</d>" in p
    assert "<Subject 2> says (S2) <d>[English] Great Scott! He's alive!</d>" in p
    # Nothing follows the last line (H3 reads it aloud); "no other speech" is in the soundscape instead.
    detailed = next(ln for ln in p.splitlines() if ln.startswith("[Shot 1]"))
    assert detailed.endswith("</d>") and "Lips stay synchronized with the dialogue. <Subject 1> says" in detailed
    assert "nobody else speaks" not in p and "These are the only" not in p
    assert ("overall_soundscape: servo whirr, electrical hum. Apart from the dialogue, no other speech, voices or murmurs."
            in p) and p.endswith("non_diegetic_music: N/A")


def test_stage_directions_in_a_line_are_not_spoken():
    s = sound.clean_sound({"lines": [
        {"speaker": "Fat_Old_Hag", "text": "(Wheezing, raspy voice) Get out of my sight! [cackles]", "delivery": "slow"},
        {"speaker": "Jason", "text": "(screams)"}]})
    assert s["lines"][0]["text"] == "Get out of my sight!"
    assert s["lines"][0]["delivery"] == "slow, Wheezing, raspy voice, cackles"
    assert s["lines"][1]["text"] == "(screams)"  # only a direction: left for the owner to turn into words
    assert sound.split_directions("No brackets here.") == ("No brackets here.", "")


def test_h3_prompt_when_nobody_speaks_and_owner_written_prompts():
    p = sound.compile_h3("A wide shot of <Picture 3> at night.", sound.clean_sound({"lines": [], "music": "low strings"}),
                         REFS[2:3], [])
    assert "Nobody speaks: there is no dialogue" in p and "No speech, voices, murmurs or vocal sounds." in p
    assert "non_diegetic_music: low strings" in p and "<Audio" not in p
    own = "subject_definitions:\n...\ndetailed_description:\n[Shot 1] mine"
    assert sound.compile_h3(own, sound.clean_sound({"lines": []}), REFS, []) == own


def test_h3_settings_carry_voices_and_refinement():
    s = sound.clean_sound({"lines": [{"speaker": "a1", "text": "Hi"}], "refine": True})
    assert "custom_settings" not in sound.apply_h3({}, PROMPT, s, REFS, [])  # PDD/PTD templates don't declare it
    out = sound.apply_h3({"custom_settings": {"audio_refinement": "none", "x": 1}}, PROMPT, s, REFS,
                         [{"asset_id": "a1", "n": 1, "wangp_media_id": "m1"}, {"asset_id": "a2", "n": 2, "wangp_media_id": "m2"}])
    assert (out["audio_prompt_type"], out["audio_guide"], out["audio_guide2"]) == ("AB", "m1", "m2")
    assert out["custom_settings"] == {"audio_refinement": "enabled", "x": 1}


def test_plain_prompt_for_other_audio_models():
    s = sound.clean_sound({"lines": [], "ambience": "rain"})
    assert sound.plain_prompt(PROMPT, s).endswith("turns to <Picture 2>. Sound: No dialogue, no voices. rain.")


def patch(h, url, body):
    return h.c.patch(url, json=body, headers={"x-csrf-token": h.csrf})


def test_voice_sample_on_a_character(make_harness, voice, images):
    h = make_harness().login()
    hale, trench, _, _ = refs_setup(h, images)
    r = h.c.put(f"/api/library/assets/{hale['id']}/voice", content=voice, headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200, r.text
    assert r.json()["voice"]["duration_s"] == pytest.approx(5, abs=0.1)
    assert h.c.get(f"/api/library/files/{r.json()['voice']['file_id']}").status_code == 200
    bad = h.c.put(f"/api/library/assets/{trench['id']}/voice", content=voice, headers={"x-csrf-token": h.csrf})
    assert bad.status_code == 422 and "only characters" in bad.text
    short = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-f", "wav", "-"],
                           capture_output=True, check=True).stdout
    too_short = h.c.put(f"/api/library/assets/{hale['id']}/voice", content=short, headers={"x-csrf-token": h.csrf})
    assert too_short.status_code == 422 and "2-30 s" in too_short.text
    cleared = h.c.delete(f"/api/library/assets/{hale['id']}/voice", headers={"x-csrf-token": h.csrf})
    assert cleared.status_code == 200 and cleared.json()["voice"] is None


def test_h3_render_sends_compiled_prompt_and_the_speakers_voice(make_harness, voice, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, _, hv, tv = refs_setup(h, images)
    h.c.put(f"/api/library/assets/{hale['id']}/voice", content=voice, headers={"x-csrf-token": h.csrf})
    shot = new_shot(h, pid, template=None)
    set_refs(h, shot["id"], hv, tv)
    checks = h.c.get(f"/api/shots/{shot['id']}/checks").json()["issues"]
    assert any("Sound isn't set" in i["message"] for i in checks)  # an H3 shot without sound fields is warned
    r = patch(h, f"/api/shots/{shot['id']}", {"sound": {"lines": [{"speaker": "Corporal Hale", "text": "Hold the line.",
                                                                   "delivery": "hoarse"}], "ambience": "distant shells",
                                                        "refine": True}})
    assert r.status_code == 200, r.text
    assert h.project(pid)["shots"][0]["sound"]["lines"][0]["text"] == "Hold the line."
    checks = h.c.get(f"/api/shots/{shot['id']}/checks").json()["issues"]
    assert not any("Sound" in i["message"] for i in checks)
    assert h.post(f"/api/shots/{shot['id']}/approve-and-render").status_code == 200
    h.wait_for(lambda: h.project(pid)["takes"], what="take")
    gen = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]["settings"]
    assert "(S1) <d>[English] Hold the line.</d>" in gen["prompt"] and "<Audio 1>" in gen["prompt"]
    assert gen["audio_prompt_type"] == "A" and gen["audio_guide"] and gen["audio_guide"] not in gen["image_refs"]
    assert "custom_settings" not in gen  # the PDD template has no refinement pass: skipped, and the checks say so
    assert any("Refine audio is skipped" in i["message"] for i in h.c.get(f"/api/shots/{shot['id']}/checks").json()["issues"])
    # the shot's history records sound changes
    revs = h.c.get(f"/api/shots/{shot['id']}/revisions").json()
    assert any("sound" in r["changed"] for r in revs)


def test_director_plans_shots_with_sound(make_harness):
    from app.director.tools import TOOLS
    from tests.test_storyboard import plan, run, shot
    for name in ("plan_storyboard", "draft_shot", "update_shot"):
        t = next(t for t in TOOLS if t["function"]["name"] == name)["function"]["parameters"]["properties"]
        props = t["scenes"]["items"]["properties"]["shots"]["items"]["properties"] if name == "plan_storyboard" else t
        assert "lines" in props["sound"]["properties"]
    h = make_harness().login()
    pid = new_project(h)
    out = run(h, pid, "plan_storyboard", **plan([{"title": "Boot", "shots": [
        shot("Wake", "A scientist flips a switch in a cluttered lab at night", 5,
             sound={"lines": [{"speaker": "the scientist", "text": "Great Scott!"}], "ambience": "crackling relays"}),
        shot("Quiet", "The android's eyes glow in the dark lab at night", 3, sound={"lines": []})]}]))
    assert "error" not in out, out
    s1, s2 = h.project(pid)["shots"]
    assert s1["sound"]["lines"][0]["text"] == "Great Scott!" and s2["sound"] == {"lines": [], "ambience": "", "music": "",
                                                                                 "refine": False}
    bad = run(h, pid, "plan_storyboard", **plan([{"title": "X", "shots": [
        shot("Bad", "A scientist flips a switch in a cluttered lab", 5, sound={"lines": [{"speaker": "Jim"}]})]}]))
    assert "sound.lines[0].text is required" in "\n".join(bad["errors"])
