"""Brief coverage: a plan the director calls done is compared with the brief, and skipped parts are asked for."""

import re

from app import coverage
from tests.test_api import new_project
from tests.test_storyboard import patch

BRIEF = "A soldier waits in the trench at dusk. A shell hits the trench. Then he marches away into the fog."
BEATS = ["A soldier waits in the trench", "A shell hits the trench", "He marches away"]


def shown_by(mapping):
    """FakeVision's answers to "which shot is closest to this beat?" (the shot whose title is mapped to a word of
    the beat, else shot 1) and to "does this one shot show it?" (yes for that mapped shot only)."""
    def answer(question):
        beat = question.split("\n")[1]
        for word, title in mapping.items():
            if word not in beat:
                continue
            if "One planned shot of the film:" in question:
                return {"answer": "yes" if f"\n“{title}”: " in question else "no"}
            if m := re.search(rf"^(\d+)\. “{title}”", question, re.M):
                return {"closest_shot": int(m.group(1)), "shows_it": "yes"}
        return {"closest_shot": 1, "shows_it": "partly", "answer": "no"}
    return answer


def planned(h, pid, replace=True):
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": replace}).status_code == 202
    proj = h.wait_for(lambda: (p := h.project(pid))["scenes"] and not p["director_busy"] and p, what="planned")
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    return proj, chat, [m["content"] for m in chat if m["role"] == "note"]


def local_project(h, **fields):
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": BRIEF, **fields})
    assert h.c.put("/api/director/settings", json={"model": "ollama/qwen3.5:9b"},
                   headers={"x-csrf-token": h.csrf}).status_code == 200
    h.gateway.more_parts, h.gateway.carry_parts = 0, True
    return pid


def rows(scenes):
    """Shot rows for gaps(): scenes as lists of shot counts, e.g. [2, 1] is shots 1-2 in scene a, 3 in scene b."""
    out = []
    for n, count in enumerate(scenes):
        out += [{"position": len(out) + k + 1, "scene_id": f"s{n}", "id": f"shot{len(out) + k + 1}"} for k in range(count)]
    return out


def test_gaps_name_the_shot_each_skipped_part_belongs_after():
    r = rows([2, 2, 1])
    where = lambda g: [(x["beats"], x["after"] and x["after"]["position"], x["boundary"]) for x in g]  # noqa: E731
    assert coverage.gaps(["a", "b"], [[1], [5]], r) == []
    # between two beats: at a scene's end when one lies between them, else just before the next beat's first shot
    assert where(coverage.gaps(["a", "x", "b"], [[1], [], [5]], r)) == [(["x"], 4, True)]
    assert where(coverage.gaps(["a", "x", "b"], [[1], [], [2]], r)) == [(["x"], 1, False)]
    assert where(coverage.gaps(["a", "x", "b"], [[3], [], [4]], r)) == [(["x"], 3, False)]
    # neighbours together; the brief's opening at the very start, its ending after the last shot
    assert where(coverage.gaps(["x", "y", "a", "z"], [[], [], [1, 2], []], r)) == [(["x", "y"], None, True), (["z"], 5, True)]
    # answers out of film order: before the next beat, never after it
    assert where(coverage.gaps(["a", "x", "b"], [[4], [], [2]], r)) == [(["x"], 1, False)]
    # no beat matched any shot: the answers can't be trusted, so nothing is asked for
    assert coverage.gaps(["a", "b"], [[], []], r) == []


def test_a_part_of_the_brief_the_plan_skips_is_asked_for_and_put_in_film_order(make_harness):
    """Live (Heavy Metal): the plan went from the warlord's entrance straight to the escape; the battle between them
    was never planned. FilmLab lists the brief's beats, asks which shots show each, and has the director add the
    one that's missing, as a new scene between the two."""
    h = make_harness().login()
    pid = local_project(h)
    h.vision.beats = BEATS
    h.vision.shown = shown_by({"waits": "Hale waits", "shell": "Impact", "marches": "March"})
    proj, chat, notes = planned(h, pid)
    assert [s["title"] for s in proj["scenes"]] == ["The wait", "The shell", "The march"]
    assert [(s["position"], s["title"]) for s in proj["shots"]] == [(1, "Hale waits"), (2, "Fog"), (3, "Impact"), (4, "March")]
    assert not [m for m in chat if m["role"] == "error"]
    assert any("Brief check: the plan skips “A shell hits the trench”" in n for n in notes)
    assert any("put the added part of your brief in its place: shot 3 “Impact”" in n for n in notes)
    ask = next(r for r in h.gateway.requests if "skips this part of my brief" in r["messages"][0]["content"])
    text = ask["messages"][0]["content"]
    assert ask["user"].endswith(":gap1") and "My brief: “A soldier waits" in text and "Current storyboard:" in text
    assert "right after shot 2 “Fog”" in text and "as ONE new scene" in text and "about 8 s" in text
    assert "All planned" not in text and "Carry on from it" not in text
    questions = [b["messages"][0]["content"] for b in h.vision.shown_asked]
    assert len(questions) == 3 and "1. “Hale waits”: Hale waits in the trench [picture: " in questions[0]
    assert all(b["model"] == "qwen3.5:9b" and b["format"]["properties"]["closest_shot"]["enum"] == [1, 2, 3]
               for b in h.vision.shown_asked)
    judged = [b["messages"][0]["content"] for b in h.vision.requests if list(b["format"]["properties"]) == ["answer"]]
    assert "“A shell hits the trench”" in judged[1] and "“Hale waits”: Hale waits in the trench [picture: " in judged[1]
    # Plan more on the covered storyboard: "All planned" with nothing new to save is right, not an error.
    _, chat, notes = planned(h, pid, replace=False)
    assert chat[-1]["role"] == "note" and chat[-1]["content"] == "Brief check: all 3 parts of your brief are in the storyboard."
    assert len(h.project(pid)["shots"]) == 4


def test_a_skipped_part_inside_a_scene_joins_that_scene(make_harness):
    h = make_harness().login()
    pid = local_project(h)
    h.vision.beats = ["A soldier waits in the trench", "A shell hits the trench", "Fog drifts over", "He marches away"]
    h.vision.shown = shown_by({"waits": "Hale waits", "shell": "Impact", "Fog": "Fog", "marches": "March"})
    proj, chat, notes = planned(h, pid)
    assert [s["title"] for s in proj["scenes"]] == ["The wait", "The march"]  # the director's new scene is gone
    assert [(s["title"], s["scene_id"]) for s in proj["shots"]] == [
        ("Hale waits", proj["scenes"][0]["id"]), ("Impact", proj["scenes"][0]["id"]), ("Fog", proj["scenes"][0]["id"]),
        ("March", proj["scenes"][1]["id"])]
    ask = next(r for r in h.gateway.requests if "skips this part of my brief" in r["messages"][0]["content"])
    assert (f"right after shot 1 “Hale waits”. Plan only that now, in ONE plan_storyboard call, added to that shot's "
            f"scene (give scene_id {proj['scenes'][0]['id']})") in ask["messages"][0]["content"]


def test_coverage_never_fails_a_plan_and_respects_the_target(make_harness):
    h = make_harness().login()
    # the model doesn't answer the question: the plan stands, and the chat says it wasn't compared
    pid = local_project(h)
    proj, chat, notes = planned(h, pid)
    assert len(proj["shots"]) == 3 and chat[-3]["content"].startswith("All planned")
    assert not [m for m in chat if m["role"] == "error"]
    assert "couldn't compare the plan with your brief" in chat[-1]["content"]
    # the director doesn't add the part: said plainly, nothing else changes
    pid = local_project(h)
    h.vision.beats, h.gateway.ignore_gap = BEATS, True
    h.vision.shown = shown_by({"waits": "Hale waits", "marches": "March"})
    proj, chat, notes = planned(h, pid)
    assert len(proj["shots"]) == 3 and not [m for m in chat if m["role"] == "error"]
    assert "The director didn't add the skipped part of your brief (“A shell hits the trench”)" in notes[-1]
    # no room left under the film's target: the owner is told what's missing, the director isn't asked
    h.gateway.ignore_gap = False
    pid = local_project(h, target_seconds=8)
    proj, chat, notes = planned(h, pid)
    assert len(proj["shots"]) == 3
    assert "already runs 12 s of the 8 s target" in notes[-1] and "“A shell hits the trench”" in notes[-1]
    # a director that isn't an Ollama model, or a music video: not checked at all
    asked = len(h.vision.shown_asked)
    assert not h.c.portal.call(coverage.applies, h.studio, pid, "anthropic/some-model")
    assert h.c.portal.call(coverage.applies, h.studio, pid, "ollama/qwen3.5:9b") and len(h.vision.shown_asked) == asked
