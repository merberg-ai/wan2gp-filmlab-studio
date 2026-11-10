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
    assert "right after shot 2 “Fog”" in text and "as ONE new scene" in text and "about 5 s" in text
    assert "All planned" not in text and "Carry on from it" not in text
    # "A soldier waits in the trench" is in shot 1's own words: the model is only asked about the other two beats
    questions = [b["messages"][0]["content"] for b in h.vision.shown_asked]
    assert len(questions) == 2 and "1. “Hale waits”: Hale waits in the trench [picture: " in questions[0]
    assert all(b["model"] == "qwen3.5:9b" and b["format"]["properties"]["closest_shot"]["enum"] == [1, 2, 3]
               for b in h.vision.shown_asked)
    judged = [b["messages"][0]["content"] for b in h.vision.requests if list(b["format"]["properties"]) == ["answer"]]
    assert "“A shell hits the trench”" in judged[0] and "“Hale waits”: Hale waits in the trench [picture: " in judged[0]
    beats_asked = next(b for b in h.vision.requests if "beats" in b["format"]["properties"])
    assert "At most 3 beats" in beats_asked["messages"][0]["content"]  # never more beats than the plan has shots
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


def test_a_shot_in_the_briefs_own_words_shows_the_beat_whatever_the_model_says():
    """Live (Spaghetti Cat): the model called "the cat slowly turns and glares" skipped beside the shot whose prompt
    says exactly that (as <Picture 3> and <Picture 2>), and a spoken line skipped that is in the shot's Sound."""
    cast = {1: "kitchen_1980s", 2: "Anthony_PJ", 3: "fat_grumpy_orange_cat"}
    mk = lambda n, title, prompt, sound=None: {  # noqa: E731
        "position": n, "scene_id": "s", "id": f"shot{n}", "title": title, "idea": "", "_refs": cast, "sound": sound,
        "prompt": f"During the day in <Picture 1>, {prompt} Medium shot, warm indoor light."}
    r = [mk(1, "The Setup", "<Picture 2> and <Picture 3> sit side by side at a dining table with a bowl of spaghetti."),
         mk(2, "The Swat & Glare", "<Picture 2> swats the paw of <Picture 3> away. <Picture 3> slowly turns its head "
                                   "toward <Picture 2> and gives a furious, unblinking glare."),
         mk(3, "The Final Word", "<Picture 2> turns toward <Picture 3> with sauce dripping down his face.",
            {"lines": [{"speaker": "{Anthony_PJ}", "text": "What the fuck?!"}]})]
    glare = "fat_grumpy_orange_cat slowly turns toward Anthony_Pj and gives him a furious, unblinking glare."
    assert coverage.overlap(glare, r[1], r) == 1.0 and coverage.overlap(glare, r[0], r) < 0.2
    assert coverage.overlap('Anthony_Pj angrily exclaims, "What the fuck?!"', r[2], r) == 1.0
    battle = "An explosive battle erupts between the heroes and the warlord amid sparks and energy blasts."
    assert max(coverage.overlap(battle, x, r) for x in r) < coverage.WORDED
    assert "fat_grumpy_orange_cat slowly turns its head toward Anthony_PJ" in coverage.picture(r[1], 500)
    assert coverage.picture(r[2], 500).endswith("[spoken: Anthony_PJ says “What the fuck?!”]")


def test_an_empty_reply_to_a_gap_ask_gets_only_that_part_as_json(make_harness):
    """Live: the reply to "add the skipped part" came back empty, and the whole-plan fallback then planned the film a
    second time on top of the first. Now the model is asked for just that part, and nothing else is planned."""
    h = make_harness().login()
    pid = local_project(h)
    h.vision.beats, h.gateway.empty_gap = BEATS, True
    h.vision.shown = shown_by({"waits": "Hale waits", "shell": "Impact", "marches": "March"})
    h.vision.answers = [{"all_planned": False, "plan": {"scenes": [{"title": "The shell", "shots": [
        {"title": "Impact", "prompt": "A shell hits the trench at dusk, earth flying", "seconds": 3,
         "rationale": "the skipped part", "sound": {"lines": []}}]}]}}]
    proj, chat, notes = planned(h, pid)
    assert [(s["position"], s["title"]) for s in proj["shots"]] == [(1, "Hale waits"), (2, "Fog"), (3, "Impact"), (4, "March")]
    assert not [m for m in chat if m["role"] == "error"]
    assert any("asking the model for the skipped part directly" in n for n in notes)
    assert not any("asking the model for the plan directly" in n for n in notes)
    direct = next(b for b in h.vision.requests if "plan" in b["format"]["properties"])
    assert "skips this part of my brief: “A shell hits the trench”" in direct["messages"][1]["content"]
    # the model has nothing to add: said plainly, and still nothing is planned twice
    pid = local_project(h)
    h.vision.shown = shown_by({"waits": "Hale waits", "marches": "March"})
    h.vision.answers = [{"all_planned": True, "summary": "All planned."}]
    proj, chat, notes = planned(h, pid)
    assert len(proj["shots"]) == 3 and "The director didn't add the skipped part of your brief" in notes[-1]


def test_a_speaker_named_in_braces_is_that_character():
    """Live: the director wrote the speaker as {Anthony_PJ}, as it names assets in prompts; the shot then warned
    that the speaker wasn't one of its references, and no prompt fix could clear that."""
    from app import sound
    from app.capabilities import _h3_sound_issues
    snd = sound.clean_sound({"lines": [{"speaker": "{Anthony_PJ}", "text": "What?!"}]})
    assert snd["lines"][0]["speaker"] == "Anthony_PJ"
    refs = [{"position": 1, "asset_id": "a1", "asset_name": "Anthony_PJ", "kind": "character"}]
    assert not [i for i in _h3_sound_issues(snd, refs, []) if i["level"] == "warn"]
    other = sound.clean_sound({"lines": [{"speaker": "Narrator", "text": "Later."}]})
    assert [i.get("kind") for i in _h3_sound_issues(other, refs, []) if i["level"] == "warn"] == ["speaker"]


def test_a_model_that_never_answers_is_said_plainly_and_big_local_models_are_marked(make_harness):
    """Live: the director was set to gemma4:31b (20.4 GB on a 16 GB GPU); two plans ended in "internal error:
    ReadTimeout" after 5 minutes. The chat now says what happened, and the model list marks what doesn't fit."""
    h = make_harness().login()
    pid = local_project(h)
    h.gateway.timeout = True
    assert h.post(f"/api/projects/{pid}/plan", {"replace_drafts": True}).status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    last = h.c.get(f"/api/projects/{pid}/chat").json()[-1]
    assert last["role"] == "error" and "ollama/qwen3.5:9b) didn't answer within 5 minutes" in last["content"]
    assert "too big for this GPU" in last["content"] and "Nothing was saved" in last["content"]
    h.gateway.timeout = False
    # the model list: a local model's file against the GPU's memory
    import httpx
    tags = {"models": [{"name": "big:31b", "size": 20_400_000_000, "details": {"parameter_size": "30.7B"}},
                       {"name": "small:9b", "size": 6_600_000_000, "details": {"parameter_size": "9B"}},
                       {"name": "far:cloud", "size": 400, "remote_host": "https://ollama.com", "details": {}}]}
    ds = h.studio.director_settings
    ds.ollama_transport = httpx.MockTransport(lambda r: httpx.Response(
        200, json=tags if r.url.path == "/api/tags" else {"capabilities": ["tools"]}))
    ds.gpu_query = lambda: {"vram_total_gb": 16.0}
    models = {m["name"]: m for m in h.c.get("/api/director/models").json()}
    assert (models["big:31b"]["fits"], models["big:31b"]["gb"], models["big:31b"]["vram_gb"]) == (False, 20.4, 16.0)
    assert models["small:9b"]["fits"] is True and models["far:cloud"]["fits"] is None


def test_a_replan_with_nothing_to_replace_carries_on_instead(make_harness):
    """Live: every shot had a take, so Re-plan could replace none; told its plan replaces the drafts, the director
    planned the whole film again and squeezed it in as 2 s duplicates. Such a re-plan is a Plan more."""
    from app.director.planning import plan_brief
    from tests.test_storyboard import run, shot
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": BRIEF})
    project = lambda: h.c.portal.call(h.studio.db.fetchone, "SELECT * FROM projects WHERE id = ?", (pid,))  # noqa: E731
    assert "Set replace_drafts: true" in h.c.portal.call(plan_brief, h.studio, project(), True)  # an empty storyboard
    out = run(h, pid, "plan_storyboard", scenes=[{"title": "Dusk", "shots": [shot("Waits", "A soldier waits in a trench at dusk")]}])
    assert "Set replace_drafts: true" in h.c.portal.call(plan_brief, h.studio, project(), True)  # a draft to replace
    h.c.portal.call(h.studio.db.execute, "UPDATE shots SET locked = 1 WHERE id = ?", (out["created_shots"][0],))
    text = h.c.portal.call(plan_brief, h.studio, project(), True)
    assert "Don't set replace_drafts" in text and "Carry on from it" in text and "shot 1 “Waits”" in text
