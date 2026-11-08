"""The Projects page's cards: a picture (a picked cover, else the opening take, else a reference image) and counts."""

from tests.test_api import new_project, new_shot
from tests.test_library import images, refs_setup, set_refs  # noqa: F401
from tests.test_manage import LTX, rendered


def card(h, pid):
    return next(p for p in h.c.get("/api/projects").json() if p["id"] == pid)


def test_cards_show_a_picture_and_counts(make_harness, images):
    h = make_harness().login()
    pid = new_project(h)
    assert card(h, pid)["cover"] is None and card(h, pid)["shot_count"] == 0  # a placeholder
    _, _, hv, _ = refs_setup(h, images)
    first = new_shot(h, pid, template=LTX, frames=73)
    second = new_shot(h, pid, title="Second", template=LTX, frames=121)
    set_refs(h, first["id"], hv)
    c = card(h, pid)
    assert c["cover"]["kind"] == "ref" and c["cover"]["thumb"].endswith("?thumb=1")  # planned, nothing rendered yet
    assert (c["shot_count"], c["shots_rendered"], c["seconds"], c["rendering"]) == (2, 0, 8.0, False)
    t2 = rendered(h, pid, second["id"])[-1]
    c = card(h, pid)
    assert c["cover"]["kind"] == "take" and c["cover"]["take_id"] == t2["id"] and not c["cover"]["picked"]
    assert c["cover"]["video"] == f"/api/media/{t2['media_id']}" and c["shots_rendered"] == 1
    t1 = rendered(h, pid, first["id"])[-1]
    assert card(h, pid)["cover"]["take_id"] == t1["id"]  # the opening shot's take wins
    put = lambda body: h.c.put(f"/api/projects/{pid}/cover", json=body, headers={"x-csrf-token": h.csrf})  # noqa: E731
    assert put({"take_id": t2["id"]}).status_code == 200
    c = card(h, pid)["cover"]
    assert c["take_id"] == t2["id"] and c["picked"] is True
    other = new_project(h, "Other")
    assert h.c.put(f"/api/projects/{other}/cover", json={"take_id": t2["id"]},
                   headers={"x-csrf-token": h.csrf}).status_code == 409
    assert put({"take_id": None}).status_code == 200
    assert card(h, pid)["cover"]["take_id"] == t1["id"]
    h.c.delete(f"/api/takes/{t1['id']}", headers={"x-csrf-token": h.csrf})
    assert card(h, pid)["cover"]["take_id"] == t2["id"]  # a deleted take is never the cover
