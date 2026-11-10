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
    for n, (kind, name) in enumerate(names):
        a = asset(h, kind, name)
        upload(h, default_variant(a), images[n % 3])
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
    assert [r["asset_name"] for r in fight["refs"]] == ["Heavy_Metal_Antihero", "Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior"]
    assert any("set to Heavy_Metal_Ancient_Alien_Weapons_Factory_Interior from the scene's description" in w
               for w in out["warnings"])
