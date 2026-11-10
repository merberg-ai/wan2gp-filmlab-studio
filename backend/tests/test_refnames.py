"""Library assets named in prompts ({Name} / {Name/view}) become the shot's references (app/refnames.py)."""

from tests.test_api import new_project
from tests.test_library import images, refs_setup, upload  # noqa: F401
from tests.test_storyboard import files_of, run, shot


def test_names_in_a_plan_become_numbered_references(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    upload(h, hv, images[2], caption="front portrait")  # a second view of Hale
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "The wait", "time_place": "a trench at dusk", "shots": [
        shot("Hale", "{Corporal Hale} waits in {The Trench} at dusk; {Corporal Hale} checks his rifle, fog"),
        shot("Face", "Close-up of {corporal_hale/front portrait} in {The Trench} at dusk, rain on his helmet"),
        shot("Typo", "{Corporal Hal} crouches in {The Trench} at dusk, fog")]}])
    assert "error" not in out, out
    shots = h.project(pid)["shots"]
    first, face, typo = shots
    assert first["prompt"] == "<Picture 1> waits in <Picture 2> at dusk; <Picture 1> checks his rifle, fog"
    assert [r["asset_name"] for r in first["refs"]] == ["Corporal Hale", "The Trench"]  # named twice: one picture
    assert first["refs"][0]["file_id"] == files_of(h, hv)[0]  # no view named: the first image
    assert face["refs"][0]["file_id"] == files_of(h, hv)[1]  # the named view, in any case or spacing
    assert [r["asset_name"] for r in typo["refs"]] == ["Corporal Hale", "The Trench"]
    assert any("{Corporal Hal} read as Corporal Hale" in w for w in out["warnings"])  # repaired, and said so


def test_unclear_names_and_ids_are_errors_clear_slips_are_repaired(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    hale, trench, hv, tv = refs_setup(h, images)
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "shots": [
        shot("Bob", "{Bob} waits in {The Trench} at dusk, fog"),
        shot("Mix", "{Corporal Hale} waits in <Picture 2> at dusk")]}])
    errs = "\n".join(out["errors"])
    assert "{Bob} isn't a library asset" in errs and "mixes {asset names} and <Picture N>" in errs
    # Ids a small model mangles: an asset id, a look id, an image id cut short.
    img = files_of(h, hv)[0]
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "time_place": "a trench at dusk", "shots": [
        shot("One", "<Picture 1> waits in <Picture 2> at dusk, fog", refs=[hale["id"], img[:-2]]),
        shot("Two", "<Picture 1> waits at dusk in the trench, fog", refs=[tv])]}])
    assert "error" not in out, out
    one, two = h.project(pid)["shots"]
    assert [r["asset_name"] for r in one["refs"]] == ["Corporal Hale", "Corporal Hale"]
    assert [r["asset_name"] for r in two["refs"]] == ["The Trench"]
    warn = "\n".join(out["warnings"])
    assert "is Corporal Hale's id, not an image id" in warn and "was cut short" in warn
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "A", "shots": [
        shot("Bad", "<Picture 1> waits at dusk in a trench", refs=["0123456789"])]}])
    assert "isn't a library image: name the asset in the prompt" in "\n".join(out["errors"])


def test_update_and_draft_shot_attach_named_assets(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    refs_setup(h, images)
    out = run(h, pid, "draft_shot", title="Wait", prompt="{Corporal Hale} waits in {The Trench} at dusk, fog")
    assert [x.split(" = ")[1].split(" ·")[0] for x in out["references"]] == ["Corporal Hale", "The Trench"]
    sid = out["shot"]["id"]
    s = h.project(pid)["shots"][0]
    assert s["prompt"].startswith("<Picture 1> waits in <Picture 2>") and len(s["refs"]) == 2
    out = run(h, pid, "update_shot", shot_id=sid, prompt="{The Trench} at dawn, empty, rain", rationale="no one left")
    assert "error" not in out, out
    s = h.project(pid)["shots"][0]
    assert s["prompt"] == "<Picture 1> at dawn, empty, rain" and [r["asset_name"] for r in s["refs"]] == ["The Trench"]
    out = run(h, pid, "update_shot", shot_id=sid, prompt="{Nobody} at dawn", rationale="x")
    assert "isn't a library asset" in out["error"] and h.project(pid)["shots"][0]["prompt"].startswith("<Picture 1> at dawn")
