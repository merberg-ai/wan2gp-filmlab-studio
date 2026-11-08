"""The Creator: the director drafts a library asset, WanGP generates its parts, the owner approves it."""

from tests.test_api import new_project
import json


def run(h, pid, tool, **args):
    return h.c.portal.call(h.studio.tools.run, pid, tool, json.dumps(args))


def post(h, url, body=None):
    return h.c.post(url, json=body or {}, headers={"x-csrf-token": h.csrf})


def ready(h, cid):
    return h.wait_for(lambda: (c := h.c.get(f"/api/creations/{cid}").json())["status"] != "drafting" and c, what="draft")


def asset(h, c):
    return h.c.get(f"/api/library/assets/{c['asset_id']}").json()


def finish(h, c, label):
    """Wait for the generation labelled `label`, then keep its first candidate."""
    a = h.wait_for(lambda: (x := asset(h, c)) and any(k["label"] == label for k in x["images"]["candidates"]) and x,
                   what=f"{label} candidates")
    cand = next(k for k in a["images"]["candidates"] if k["label"] == label)
    r = post(h, f"/api/library/candidates/{cand['id']}/keep")
    assert r.status_code == 200, r.text
    return cand


def test_character_from_description_to_library(make_harness):
    h = make_harness().login()
    r = post(h, "/api/creations", {"kind": "character", "brief": "A friendly young android who just woke up in a lab"})
    assert r.status_code == 201, r.text
    c = ready(h, r.json()["id"])
    assert c["status"] == "ready" and c["plan"]["name"] == "Anthony_science" and len(c["plan"]["views"]) == 2
    assert c["plan"]["voice"]["line"] == "Hello! I am awake."
    # a draft: hidden from the library (and so from the director and shots) until approved
    assert not [a for a in h.c.get("/api/library").json() if a["id"] == c["asset_id"]]
    assert asset(h, c)["name"] == "Anthony_science" and asset(h, c)["draft"] == 1
    # the other views need a kept anchor; approval needs an image
    assert post(h, f"/api/creations/{c['id']}/generate", {"part": "views"}).status_code == 422
    assert post(h, f"/api/creations/{c['id']}/approve").status_code == 422

    assert post(h, f"/api/creations/{c['id']}/generate", {"part": "anchor"}).status_code == 201
    anchor = finish(h, c, "front portrait")
    assert post(h, f"/api/creations/{c['id']}/generate", {"part": "views"}).status_code == 201
    finish(h, c, "full body")
    gens = [a for n, a in h.wangp.calls if n == "wangp_generate"]
    assert gens[0]["settings"][0]["model_type"] == "z_image"                 # anchor: text to image
    view = gens[1]["settings"][0]
    assert view["model_type"] == "flux2_klein_9b" and len(view["image_refs"]) == 1  # views: edits of the anchor

    assert post(h, f"/api/creations/{c['id']}/generate", {"part": "voice"}).status_code == 201
    finish(h, c, "voice")
    voice = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]["settings"][0]
    assert voice["model_type"] == "qwen3_tts_voicedesign" and voice["prompt"] == "Hello! I am awake."
    assert voice["alt_prompt"].startswith("young male android")
    a = asset(h, c)
    assert a["voice"] and a["voice"]["duration_s"] > 5
    captions = [f["caption"] for v in a["variants"] for f in v["files"]]
    # (the fake returns the same picture for every view, so the second dedups into the first)
    assert captions[0] == "front portrait" and a["variants"][0]["files"][0]["file_id"] == anchor["file_id"]

    r = post(h, f"/api/creations/{c['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert [x["name"] for x in h.c.get("/api/library").json() if x["id"] == c["asset_id"]] == ["Anthony_science"]
    assert h.c.get("/api/creations").json() == []


def test_a_draft_with_errors_goes_back_to_the_director(make_harness):
    h = make_harness().login()
    h.gateway.creator_bad_first = True
    c = ready(h, post(h, "/api/creations", {"kind": "character", "brief": "A friendly android", "voice": False}).json()["id"])
    assert c["status"] == "ready" and c["plan"]["voice"] is None, c
    creator = [r for r in h.gateway.requests if r["tools"][0]["function"]["name"] == "propose_asset"]
    # a missing name goes back to the director to fix; a missing description is filled from the owner's words
    assert len(creator) == 2 and "name is required" in creator[1]["messages"][1]["content"]
    assert "description is required" not in creator[1]["messages"][1]["content"]
    # the owner edits the draft; a bad edit is refused with every problem
    plan = c["plan"] | {"name": "Tin", "views": c["plan"]["views"][:1]}
    r = h.c.put(f"/api/creations/{c['id']}/plan", json={"plan": plan}, headers={"x-csrf-token": h.csrf})
    assert r.status_code == 200 and r.json()["plan"]["name"] == "Tin" and asset(h, c)["name"] == "Tin"
    bad = h.c.put(f"/api/creations/{c['id']}/plan", json={"plan": {"views": []}}, headers={"x-csrf-token": h.csrf})
    assert bad.status_code == 422 and "name is required" in bad.text and "at least one image" in bad.text


def test_sound_is_music_and_discard(make_harness):
    h = make_harness().login()
    c = ready(h, post(h, "/api/creations", {"kind": "sound", "brief": "A bubbly theme for the lab scenes"}).json()["id"])
    assert c["plan"]["music"]["style"].startswith("playful synth") and c["plan"]["views"] == []
    assert post(h, f"/api/creations/{c['id']}/generate", {"part": "music"}).status_code == 201
    finish(h, c, "music")
    music = [a for n, a in h.wangp.calls if n == "wangp_generate"][-1]["settings"][0]
    assert music["model_type"] == "ace_step_v1_5_turbo_lm_1_7b" and music["prompt"] == "[Instrumental]"
    assert music["duration_seconds"] == 20
    assert post(h, f"/api/creations/{c['id']}/approve").json()["status"] == "approved"

    other = ready(h, post(h, "/api/creations", {"kind": "prop", "brief": "A big brass lab switch"}).json()["id"])
    r = post(h, f"/api/creations/{other['id']}/discard")
    assert r.status_code == 200 and r.json()["status"] == "discarded"
    assert h.c.get(f"/api/library/assets/{other['asset_id']}").status_code == 404  # in Recently deleted


def test_director_drafts_an_asset_from_the_chat(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    out = run(h, pid, "propose_asset", kind="location", name="Jim's lab", why="the brief needs it",
              description="A cluttered electro-mechanical lab", views=[{"label": "wide", "prompt": "Wide view of a lab"}])
    assert out["status"] == "waiting for the owner", out
    [c] = h.c.get("/api/creations").json()
    assert c["status"] == "ready" and c["asset"]["name"] == "Jim's lab" and c["brief"] == "the brief needs it"
    assert "not drafted" in run(h, pid, "propose_asset", kind="prop", name="x", description="y")["error"]


def test_emptying_recently_deleted_removes_discarded_and_deleted_creator_assets(make_harness):
    """Live: an asset made by the Creator kept its creations row, so deleting it forever (or emptying Recently
    deleted) failed with a foreign-key error and an Internal Server Error, and nothing was deleted."""
    h = make_harness().login()
    c = ready(h, post(h, "/api/creations", {"kind": "sound", "brief": "A bubbly theme for the lab scenes"}).json()["id"])
    post(h, f"/api/creations/{c['id']}/generate", {"part": "music"})
    finish(h, c, "music")
    post(h, f"/api/creations/{c['id']}/approve")
    h.c.delete(f"/api/library/assets/{c['asset_id']}", headers={"x-csrf-token": h.csrf})  # approved, then deleted
    other = ready(h, post(h, "/api/creations", {"kind": "prop", "brief": "A big brass lab switch"}).json()["id"])
    post(h, f"/api/creations/{other['id']}/discard")
    assert len(h.c.get("/api/trash").json()["assets"]) == 2
    r = post(h, "/api/trash/empty")
    assert r.status_code == 200, r.text
    assert r.json()["purged"] == {"assets": 2} and r.json()["failed"] == []
    assert h.c.get("/api/trash").json()["assets"] == []
    left = h.c.portal.call(h.studio.db.fetchone, "SELECT COUNT(*) AS n FROM creations")["n"]
    assert left == 0
