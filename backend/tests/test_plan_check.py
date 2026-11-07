"""The brief check before planning: length, style and Library names."""

from app.plan_check import ACTION_STYLE, brief_seconds, check
from app.templates import TEMPLATES
from tests.test_api import new_project
from tests.test_storyboard import patch

WASTELAND = ("Create a 90–120 second illustrated sci-fi animated short inspired by the 1980s Heavy Metal animated film. "
             "Wasteland_Survivor leaves Red_Rocket_Wasteland_Home with Wasteland_Dog. Suddenly, four Raiders appear and "
             "a violent firefight erupts; they escape through falling debris as the building is collapsing. "
             "AUDIO: ABSOLUTELY NO DIALOGUE.")


def test_the_length_a_brief_asks_for():
    assert brief_seconds(WASTELAND) == (105, "90–120 second")  # a range gives its middle; "1980s" is a decade
    assert brief_seconds("A 2-minute music video about rain.")[0] == 120
    assert brief_seconds("A short film, about 45 seconds long, of a cat.")[0] == 45
    assert brief_seconds("Each shot is 6 seconds. A girl walks home.") is None  # a shot's length, not the film's
    assert brief_seconds("A dog in the 1980s.") is None


def test_the_wasteland_brief_against_its_illustrated_story_settings():
    tpl = TEMPLATES["illustrated_story"]
    project = {"brief": WASTELAND, "style": tpl["style"], "template": "illustrated_story", "target_seconds": 60}
    assets = [{"name": "Wasteland_Survivor", "kind": "character", "cover_file_id": "f1"},
              {"name": "Wasteland Dog", "kind": "character", "cover_file_id": None}]
    issues = {i["id"].split(":")[0] + ":" + i["id"].split(":")[1]: i for i in check(project, assets)}
    length = issues["length:105"]
    assert "about 105 s (“90–120 second”)" in length["message"] and length["fixes"][0]["patch"] == {"target_seconds": 105}
    style = issues["style:action"]
    assert "firefight" in style["message"] and style["fixes"][0]["patch"] == {"style": ACTION_STYLE}
    assert "Red_Rocket_Wasteland_Home" in issues["library:missing"]["message"]
    assert "Wasteland_Dog" in issues["library:bare"]["message"]  # matched as "Wasteland Dog", but it has no images
    # with the fixes applied and the library complete, nothing is left to say
    project |= {"target_seconds": 105, "style": ACTION_STYLE}
    assets = [{"name": n, "kind": "character", "cover_file_id": "f"} for n in
              ("Wasteland_Survivor", "Red_Rocket_Wasteland_Home", "Wasteland_Dog")]
    assert check(project, assets) == []


def test_speech_disagreeing_with_the_style():
    style = TEMPLATES["character_dialogue"]["style"]
    out = check({"brief": "Two friends on a bench. No dialogue, only looks.", "style": style, "target_seconds": 30}, [])
    assert [i["id"] for i in out] == ["style:speech"]


def test_plan_check_endpoint(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    patch(h, f"/api/projects/{pid}", {"brief": "A 90 second short film about a lighthouse keeper.", "target_seconds": 30})
    issues = h.c.get(f"/api/projects/{pid}/plan-check").json()["issues"]
    assert issues[0]["fixes"][0]["patch"] == {"target_seconds": 90}
    patch(h, f"/api/projects/{pid}", issues[0]["fixes"][0]["patch"])
    assert h.c.get(f"/api/projects/{pid}/plan-check").json()["issues"] == []
