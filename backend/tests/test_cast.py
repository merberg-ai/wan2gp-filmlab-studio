"""A film's cast (Cast panel): in "cast" mode the director sees and may use only these assets, and a scene with no
place gets the cast location its description clearly names."""

from tests.test_api import new_project
from tests.test_library import asset, default_variant, images, refs_setup, upload  # noqa: F401
from tests.test_storyboard import patch, run, shot


def put_cast(h, pid, ids, status=200):
    r = h.c.put(f"/api/projects/{pid}/cast", json={"asset_ids": ids}, headers={"x-csrf-token": h.csrf})
    assert r.status_code == status, r.text
    return r.json()


def test_a_cast_film_uses_only_its_cast(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    assert patch(h, f"/api/projects/{pid}", {"plan_library": "cast"}).json()["plan_library"] == "cast"
    assert [a["name"] for a in put_cast(h, pid, [hale["id"]])["cast"]] == ["Corporal Hale"]
    assert [a["name"] for a in h.c.get(f"/api/projects/{pid}/cast").json()["cast"]] == ["Corporal Hale"]
    put_cast(h, pid, ["nope"], status=422)
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "time_place": "a trench at dusk", "shots": [
        shot("Both", "{Corporal Hale} waits in {The Trench} at dusk, fog")]}])
    assert "{The Trench} isn't in this film's cast" in "\n".join(out["errors"])
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "time_place": "a trench at dusk", "shots": [
        shot("Hale", "{Corporal Hale} waits in a trench at dusk, fog")]}])
    assert "error" not in out, out
    sid = out["created_shots"][0]
    trench_img = h.c.get(f"/api/library/assets/{trench['id']}").json()["variants"][0]["files"][0]["file_id"]
    assert "isn't in this film's cast" in run(h, pid, "assign_refs", shot_id=sid, refs=[{"file_id": trench_img}])["error"]
    assert [a["name"] for a in run(h, pid, "list_assets")["assets"]] == ["Corporal Hale"]
    # The planning brief lists only the cast.
    patch(h, f"/api/projects/{pid}", {"brief": "Hale waits in the trench at dusk."})
    h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="planned")
    brief = [m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"]
    assert "This film's cast" in brief and "{Corporal Hale} (character)" in brief and "{The Trench}" not in brief
    # Back to the whole library: the trench may be used again.
    patch(h, f"/api/projects/{pid}", {"plan_library": "fit"})
    sid = h.project(pid)["shots"][-1]["id"]  # the plan above replaced the drafts
    assert run(h, pid, "assign_refs", shot_id=sid, refs=[{"file_id": trench_img}]).get("references")


def test_a_scene_without_a_place_gets_the_cast_location_it_names(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    names = [("character", "Heavy_Metal_Antihero"), ("location", "Heavy_Metal_Alien_Weapons_Factory"),
             ("location", "Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior"), ("location", "Heavy_Metal_Outlaw_Ship_Interior"),
             ("location", "Heavy_Netal_Alien_Planet_Surface"), ("location", "Heavy_Metal_Alien_Planet_Orbit")]
    ids = {}
    pictures = {"Heavy_Metal_Antihero": 0, "Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior": 1,
                "Heavy_Metal_Outlaw_Ship_Interior": 2}  # distinct files: the library stores identical ones once
    for kind, name in names:
        a = asset(h, kind, name)
        if name in pictures:
            upload(h, default_variant(a), images[pictures[name]])
        ids[name] = a["id"]
    patch(h, f"/api/projects/{pid}", {"plan_library": "cast"})
    put_cast(h, pid, list(ids.values()))
    out = run(h, pid, "plan_storyboard", scenes=[
        {"title": "Battle", "time_place": "abandoned alien weapons factory interior battle zone, night", "shots": [
            shot("Fight", "{Heavy_Metal_Antihero} fires his pistol in the factory interior at night")]},
        {"title": "Cockpit", "time_place": "spaceship cockpit interior, night", "shots": [
            shot("Crew", "{Heavy_Metal_Antihero} at the controls in the cockpit interior at night")]},
        {"title": "Approach", "time_place": "outer space near the alien planet, night", "shots": [
            shot("Space", "A battered ship crosses outer space toward the alien planet at night")]}])
    assert "error" not in out, out
    proj = h.project(pid)
    by = {s["title"]: s for s in proj["scenes"]}
    assert by["Battle"]["location_asset_id"] == ids["Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior"]
    assert by["Cockpit"]["location_asset_id"] == ids["Heavy_Metal_Outlaw_Ship_Interior"]  # "ship" in "spaceship"
    assert not by["Approach"]["location_asset_id"]  # orbit and surface tie on "alien planet": not guessed
    fight = next(s for s in proj["shots"] if s["title"] == "Fight")
    # with a cast the scene's place isn't pasted onto every shot (a broad scene spans several places): the
    # who-and-where pass picks each shot's place (test below)
    assert [r["asset_name"] for r in fight["refs"]] == ["Heavy_Metal_Antihero"]
    assert any("set to Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior from the scene's description" in w
               for w in out["warnings"])


def test_who_and_where_attaches_the_cast_each_new_shot_shows(make_harness, images):
    """A small director describes "the soldier" without naming the asset: after the part is saved FilmLab asks the
    model, per shot, which cast members and place it shows (a list to pick from) and attaches them."""
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    patch(h, f"/api/projects/{pid}", {"plan_library": "cast"})
    put_cast(h, pid, [hale["id"], trench["id"]])
    assert h.c.put("/api/director/settings", json={"model": "ollama/qwen3.5:9b"},
                   headers={"x-csrf-token": h.csrf}).status_code == 200
    h.vision.who = lambda body: ({"characters": ["Corporal Hale"], "place": "The Trench"}
                                 if "Waits" in body["messages"][0]["content"] else {"characters": [], "place": "none"})
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "Dusk", "time_place": "a trench at dusk", "shots": [
        shot("Waits", "The soldier waits in the trench at dusk, fog"),
        shot("Sky", "Grey clouds roll over at dusk, wind")]}])
    assert "error" not in out, out
    waits, sky = h.project(pid)["shots"]
    assert [r["asset_name"] for r in waits["refs"]] == ["Corporal Hale", "The Trench"]
    assert waits["prompt"].endswith("<Picture 1> is Corporal Hale. Setting: <Picture 2>.")
    assert not sky["refs"] and sky["prompt"] == "Grey clouds roll over at dusk, wind"  # nothing to add
    asked = h.vision.who_asked[0]
    assert asked["model"] == "qwen3.5:9b" and asked["format"]["properties"]["place"]["enum"] == ["The Trench", "none"]
    assert "Corporal Hale" in asked["messages"][0]["content"] and len(h.vision.who_asked) == 2
    assert out["who_and_where"] == ["shot 1 “Waits”: added Corporal Hale, The Trench"]
    # Without a cast, no question is asked.
    patch(h, f"/api/projects/{pid}", {"plan_library": "fit"})
    run(h, pid, "plan_storyboard", scenes=[{"title": "More", "time_place": "a trench at night", "shots": [
        shot("Night", "The soldier waits in the trench at night, fog")]}])
    assert len(h.vision.who_asked) == 2


def test_who_and_where_trouble_never_fails_a_saved_plan(make_harness, images):
    """Live: the pass's prompt update was refused and the error made plan_storyboard report a failure for a plan it had
    saved (the director then re-sent it again and again). Now the shot is put back as planned and the plan stands."""
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    patch(h, f"/api/projects/{pid}", {"plan_library": "cast"})
    put_cast(h, pid, [hale["id"], trench["id"]])
    h.c.put("/api/director/settings", json={"model": "ollama/qwen3.5:9b"}, headers={"x-csrf-token": h.csrf})
    h.vision.who = {"characters": ["Corporal Hale"], "place": "The Trench"}

    async def refuse(*a, **k):
        raise ValueError("refused for the test")
    h.studio.tools.update_shot = refuse
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "Dusk", "time_place": "a trench at dusk", "shots": [
        shot("Waits", "The soldier waits in the trench at dusk, fog")]}])
    assert "error" not in out and len(out["created_shots"]) == 1, out
    assert "couldn't add Corporal Hale, The Trench (refused for the test)" in out["who_and_where"][0]
    s = h.project(pid)["shots"][0]
    assert not s["refs"] and s["prompt"] == "The soldier waits in the trench at dusk, fog"  # as planned, nothing unnamed
