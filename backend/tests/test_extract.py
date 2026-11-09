"""Library → Extract: a frame of a take (or a picture) becomes a new asset, as it is or reworked by an image model."""

from tests.test_edit import rendered_shot, small_project
from tests.test_library import images  # noqa: F401


def grab(h, **body):
    return h.post("/api/library/frames", body)


def test_a_frame_of_a_take_becomes_an_asset(make_harness):
    h = make_harness().login()
    pid = small_project(h)
    s = rendered_shot(h, pid, "Gym")
    take = next(t for t in h.project(pid)["takes"] if t["shot_id"] == s["id"])
    r = grab(h, media_id=take["media_id"], t=0.5)
    assert r.status_code == 201, r.text
    frame = r.json()
    assert (frame["width"], frame["height"]) == (take["width"], take["height"])
    assert "Frame at 0.50 s of shot 1 “Gym”" in frame["source_note"]
    # A selection of it: half the width and height, from the middle.
    crop = grab(h, file_id=frame["file_id"], crop={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}).json()
    assert crop["width"] == take["width"] // 2 // 2 * 2 and crop["height"] <= take["height"] // 2
    assert grab(h, file_id=frame["file_id"], crop={"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.5}).status_code == 422
    assert grab(h, media_id=take["media_id"], file_id=frame["file_id"]).status_code == 422
    assert grab(h, media_id="nope").status_code == 422
    # As it is: the picture is the asset's look, with where it came from.
    r = h.post("/api/library/extract", {"file_id": crop["file_id"], "kind": "location", "name": "Old gym",
                                        "source_note": frame["source_note"] + " (cropped)"})
    assert r.status_code == 201, r.text
    assert r.json()["job_id"] is None
    a = h.c.get(f"/api/library/assets/{r.json()['asset_id']}").json()
    assert a["kind"] == "location" and [v["name"] for v in a["variants"]] == ["default"]
    f = a["variants"][0]["files"][0]
    assert f["file_id"] == crop["file_id"] and f["caption"] == "extracted" and "Gym" in f["source_note"]
    # Sounds aren't pictures.
    assert h.post("/api/library/extract", {"file_id": crop["file_id"], "kind": "sound", "name": "x"}).status_code == 422


def test_an_extracted_picture_reworked_by_a_reference_model(make_harness, images):
    h = make_harness().login()
    up = h.c.post("/api/library/frames/upload", content=images[0],
                  headers={"x-csrf-token": h.csrf, "content-type": "application/octet-stream"})
    assert up.status_code == 201, up.text
    fid = up.json()["file_id"]
    r = h.post("/api/library/extract", {"file_id": fid, "kind": "character", "name": "Rider",
                                        "edit": {"prompt": "The same person, full body, plain grey background",
                                                 "count": 2, "resolution": "832x1216"}})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["job_id"] and not out["error"]
    a = h.c.get(f"/api/library/assets/{out['asset_id']}").json()
    looks = {v["name"]: v for v in a["variants"]}
    assert list(looks) == ["default", "source frame"]  # the main look first
    assert looks["default"]["files"] == [] and looks["source frame"]["files"][0]["file_id"] == fid
    job = next(j for j in a["images"]["jobs"] if j["id"] == out["job_id"])
    assert job["variant_id"] == looks["default"]["id"] and job["template"] == "Flux Klein 9B" and job["count"] == 2
    gen = [x for n, x in h.wangp.calls if n == "wangp_generate"][-1]
    assert len(gen["settings"]) == 2 and gen["settings"][0]["image_refs"] and gen["settings"][0]["resolution"] == "832x1216"
    # A text-only model can't rework a picture: refused, and the asset stays for another try on its page.
    r = h.post("/api/library/extract", {"file_id": fid, "kind": "prop", "name": "Helmet",
                                        "edit": {"prompt": "The helmet alone", "template": "Z Image Turbo"}})
    assert r.status_code == 201 and "takes no reference" in r.json()["error"]
