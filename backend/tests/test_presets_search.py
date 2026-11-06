"""F7: model presets (FilmLab's, WanGP-derived and the owner's) and context-aware library search."""

import json

from tests.test_api import new_project, new_shot
from tests.test_library import REF2VA, asset, default_variant, images, set_refs, upload  # noqa: F401 (fixture)

LTX = "LTX-2 2.3 Distilled 1.0"


def presets(h):
    r = h.c.get("/api/presets")
    assert r.status_code == 200, r.text
    return {p["id"]: p for p in r.json()}


def test_presets_follow_what_is_installed(make_harness):
    h = make_harness().login()
    ps = presets(h)
    char = ps["minimax-character"]
    assert char["status"] == "ready" and char["template"] == REF2VA and char["frames"] == 121
    assert char["resolution"] == "832x480" and char["takes_refs"] and char["audio"] and "<Picture 1>" in char["tips"]
    assert ps["minimax-beat"]["frames"] == 73 and ps["ltx-long"]["frames"] == 193
    assert ps["ltx-establishing"]["status"] == "ready" and ps["ltx-establishing"]["resolution"] == "1280x720"
    # Not downloaded: listed, with the reason, never applied (FilmLab doesn't download models).
    plain = ps["minimax-plain"]
    assert plain["status"] == "needs_download" and "doesn't download models" in plain["reason"]
    assert ps["minimax-omni"]["status"] == "unavailable"
    # Every other installed template gets a plain preset; a template WanGP rejects doesn't.
    assert ps["wangp:Enhanced Lightning v2"]["status"] == "ready"
    assert not any("Turbo" in k for k in ps)
    order = [p["status"] for p in ps.values()]
    assert order == sorted(order, key=["ready", "needs_download", "unavailable"].index)


def test_applying_a_preset_sets_template_size_and_length(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template=LTX, frames=49, resolution="1280x720")
    r = h.post(f"/api/shots/{s['id']}/preset", {"preset_id": "minimax-character"})
    assert r.status_code == 200, r.text
    shot = r.json()
    assert (shot["template"], shot["resolution"], shot["frames"], shot["template_explicit"]) == (REF2VA, "832x480", 121, 1)
    hist = h.c.get(f"/api/shots/{s['id']}/revisions").json()
    assert "Character shot" in hist[-1]["rationale"]
    shot = h.post(f"/api/shots/{s['id']}/preset", {"preset_id": "ltx-establishing", "keep_length": True}).json()
    assert (shot["template"], shot["resolution"], shot["frames"]) == (LTX, "1280x720", 121)
    r = h.post(f"/api/shots/{s['id']}/preset", {"preset_id": "minimax-plain"})
    assert r.status_code == 422 and "download" in r.text
    assert h.post(f"/api/shots/{s['id']}/preset", {"preset_id": "nope"}).status_code == 422


def test_owner_presets_are_validated_saved_and_deleted(make_harness):
    h = make_harness().login()
    mine = {"name": "Teaser close-up", "template": REF2VA, "resolution": "832x480", "frames": 50, "tips": "Tight framing"}
    r = h.post("/api/presets", mine)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["source"] == "owner" and p["frames"] == 49 and p["status"] == "ready" and p["seconds"] == 2.0
    assert h.post("/api/presets", mine).status_code == 422  # same name
    assert h.post("/api/presets", mine | {"name": "x", "template": "MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps"}).status_code == 422
    assert h.post("/api/presets", mine | {"name": "y", "resolution": "9999x9999"}).status_code == 422
    assert p["id"] in presets(h)
    assert h.c.delete("/api/presets/minimax-character", headers={"x-csrf-token": h.csrf}).status_code == 404
    assert h.c.delete(f"/api/presets/{p['id']}", headers={"x-csrf-token": h.csrf}).status_code == 200
    assert p["id"] not in presets(h)


def test_director_lists_and_applies_presets(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template=LTX)
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    listed = {p["id"]: p for p in run("list_presets")["presets"]}
    assert listed["minimax-character"]["status"] == "ready" and listed["minimax-plain"]["status"] == "needs_download"
    assert "rationale" in run("apply_preset", shot_id=s["id"], preset_id="minimax-beat", rationale="")["error"]
    res = run("apply_preset", shot_id=s["id"], preset_id="minimax-beat", rationale="a quick reaction")
    assert res["shot"]["template"] == REF2VA and res["shot"]["frames"] == 73
    assert "download" in run("apply_preset", shot_id=s["id"], preset_id="minimax-plain", rationale="x")["error"]
    h.c.portal.call(h.studio.db.execute, "UPDATE shots SET locked = 1 WHERE id = ?", (s["id"],))
    assert "locked" in run("apply_preset", shot_id=s["id"], preset_id="ltx-long", rationale="x")["error"]


def test_library_is_ranked_for_the_shot_with_reasons_and_the_scene_look(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    anthony = asset(h, name="Anthony", description="A boy of ten in a yellow raincoat", tags="kid")
    alien = asset(h, name="The Alien", description="Small green visitor with big eyes")
    street = asset(h, "location", "Maple Street", description="Suburban street, rain, night, streetlights")
    bugle = asset(h, "prop", "Broken bugle", description="A dented brass bugle")
    for a, img in ((anthony, images[0]), (alien, images[1]), (street, images[2]), (bugle, images[0])):
        upload(h, default_variant(a), img)
    # A second look for Anthony; the scene keeps using the first one.
    v2 = h.post(f"/api/library/variants/{default_variant(anthony)}/new-version").json()
    upload(h, v2["id"], images[2], caption="pyjamas")
    sc = h.post(f"/api/projects/{pid}/scenes", {"title": "Night street", "time_place": "Night, rainy suburban street",
                                                "location_asset_id": street["id"]}).json()
    first = new_shot(h, pid, title="Anthony waits", scene_id=sc["id"], template=REF2VA,
                     prompt="<Picture 1> waits under a streetlight in the rain")
    set_refs(h, first["id"], default_variant(anthony))
    shot = new_shot(h, pid, title="The meeting", scene_id=sc["id"], template=REF2VA,
                    prompt="Anthony meets a green visitor under the streetlights, rain falling")
    r = h.c.get(f"/api/shots/{shot['id']}/library-suggestions")
    assert r.status_code == 200, r.text
    res = r.json()
    names = [a["name"] for a in res["assets"]]
    assert names[0] == "Anthony" and names[1] == "Maple Street" and names[-1] == "Broken bugle", names
    a0 = res["assets"][0]
    assert "named in the shot" in a0["reasons"] and any("other shot" in x for x in a0["reasons"])
    assert a0["look"]["variant_id"] == default_variant(anthony) and a0["look"]["why"] == "the look this scene uses"
    loc = res["assets"][1]
    assert "the scene's location" in loc["reasons"] and loc["look"]["why"] == "newest look"
    alien_row = next(a for a in res["assets"] if a["name"] == "The Alien")
    assert alien_row["score"] > 0 and any("green" in x for x in alien_row["reasons"])
    assert res["assets"][-1]["score"] == 0 and res["assets"][-1]["reasons"] == []
    assert res["context"]["scene"] == "Night street"
    assert any("streetlights" in x for x in loc["reasons"]), loc["reasons"]  # words as written, not stems
    # Underscored names count as named: "Anthony_PJ" is named by "Anthony".
    from app.library_context import _named
    assert _named("Anthony_PJ", "anthony meets a visitor") and not _named("The Alien", "a green visitor")
    # A query narrows the list; ranking still applies.
    q = h.c.get(f"/api/shots/{shot['id']}/library-suggestions", params={"q": "street"}).json()["assets"]
    assert [a["name"] for a in q] == ["Maple Street"]
    # On the first shot, Anthony is already a reference.
    on = h.c.get(f"/api/shots/{first['id']}/library-suggestions").json()["assets"]
    assert next(a for a in on if a["name"] == "Anthony")["on_shot"] is True
    # Scene-level suggestions, and the director's ranked list_assets.
    scene_res = h.c.get(f"/api/projects/{pid}/library-suggestions", params={"scene_id": sc["id"]}).json()
    assert scene_res["assets"][0]["name"] == "Maple Street"
    run = lambda name, **args: h.c.portal.call(h.studio.tools.run, pid, name, json.dumps(args))
    ranked = run("list_assets", shot_id=shot["id"])
    assert ranked["assets"][0]["name"] == "Anthony" and ranked["assets"][0]["look"]["file_id"]
    assert "error" in run("list_assets", shot_id="nope")
    assert h.c.get("/api/shots/nope/library-suggestions").status_code == 404
