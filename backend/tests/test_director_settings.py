"""Settings → Director: studio notes and reply style reach the director, rounds and default autonomy apply,
fresh conversations, the read-only views and the connection test."""

import json

from tests.test_api import new_project


def put(h, body):
    return h.c.put("/api/director/settings", json=body, headers={"x-csrf-token": h.csrf})


def last_user_text(h):
    return h.gateway.requests[-1]["messages"][0]["content"]


def test_settings_are_validated_and_reach_every_message(make_harness):
    h = make_harness().login()
    d = h.c.get("/api/director").json()["settings"]
    assert d == {"notes": "", "reply_style": "standard", "chat_rounds": 6, "plan_rounds": 14, "default_autonomy": "A2",
                 "model": ""}
    for bad in ({"reply_style": "loud"}, {"chat_rounds": 1}, {"plan_rounds": 99}, {"default_autonomy": "A9"},
                {"notes": "x" * 2001}):
        assert put(h, bad).status_code == 422, bad
    r = put(h, {"notes": "Always 832x480. Prefer static cameras.", "reply_style": "short", "chat_rounds": 4})
    assert r.status_code == 200 and r.json()["chat_rounds"] == 4 and r.json()["plan_rounds"] == 14
    pid = new_project(h)
    assert h.post(f"/api/projects/{pid}/chat", {"text": "Hello there"}).status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    sent = last_user_text(h)
    assert sent.startswith("[FilmLab project: The Dead March]\nHello there")
    assert "Always 832x480. Prefer static cameras." in sent and "Keep replies short" in sent
    # The chat shows what the owner wrote, not the notes.
    mine = [m for m in h.c.get(f"/api/projects/{pid}/chat").json() if m["role"] == "user"][-1]["content"]
    assert mine == "Hello there"
    put(h, {"notes": "", "reply_style": "standard"})
    h.post(f"/api/projects/{pid}/chat", {"text": "Again"})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    assert "studio notes" not in last_user_text(h)


def test_the_project_style_reaches_every_chat_message_and_get_project(make_harness):
    from app.director.chat import project_header
    from app.templates import TEMPLATES
    h = make_harness().login()
    pid = h.post("/api/projects", {"title": "Paper Moon", "template": "illustrated_story"}).json()["id"]
    style = TEMPLATES["illustrated_story"]["style"]
    assert h.post(f"/api/projects/{pid}/chat", {"text": "Add a shot of the moon"}).status_code == 202
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    sent = last_user_text(h)
    assert sent.startswith(f"[FilmLab project: Paper Moon]\n[Style for this film (follow it in every prompt you write): "
                           f"{style}]\nAdd a shot of the moon")
    view = h.c.portal.call(h.studio.tools.run, pid, "get_project", "{}")
    assert view["style"] == style
    # Plan with director and Enhance carry the style in their own message: not twice.
    assert project_header({"title": "T", "style": style}, "Style for this film: x") == "[FilmLab project: T]"
    assert project_header({"title": "T", "style": ""}) == "[FilmLab project: T]"


def test_default_autonomy_applies_to_new_projects_only(make_harness):
    h = make_harness().login()
    old = new_project(h, "Old")
    put(h, {"default_autonomy": "A0"})
    assert h.project(new_project(h, "New"))["autonomy"] == "A0"
    assert h.project(old)["autonomy"] == "A2"


def test_fresh_conversation_changes_the_session_and_keeps_the_chat(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    h.post(f"/api/projects/{pid}/chat", {"text": "First"})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    first = h.gateway.requests[-1]["user"]
    assert first == f"filmlab:project:{pid}"
    r = h.post(f"/api/projects/{pid}/chat/fresh")
    assert r.status_code == 200 and r.json()["conversation"] == 1
    h.post(f"/api/projects/{pid}/chat", {"text": "Second"})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    assert h.gateway.requests[-1]["user"] == f"filmlab:project:{pid}:1"
    chat = h.c.get(f"/api/projects/{pid}/chat").json()
    assert [m["content"] for m in chat if m["role"] == "user"] == ["First", "Second"]
    assert any(m["role"] == "note" and "starts fresh" in m["content"] for m in chat)
    assert h.post("/api/projects/nope/chat/fresh").status_code == 404


def test_overview_shows_model_instructions_and_the_test_turn(make_harness, settings):
    settings.openclaw_config.write_text(json.dumps({
        "gateway": {"auth": {"token": "must-not-leak"}},
        "agents": {"defaults": {"model": {"primary": "ollama/gemma4:31b-cloud"}}, "entries": {"director": {}}}}))
    settings.agent_workspace.mkdir()
    (settings.agent_workspace / "AGENTS.md").write_text("# Director\nYou plan films.")
    h = make_harness().login()
    o = h.c.get("/api/director").json()
    assert o["model"] == "ollama/gemma4:31b-cloud" and o["connected"] is True and o["agent"] == "director"
    assert [i["name"] for i in o["instructions"]] == ["AGENTS.md"] and "plan films" in o["instructions"][0]["text"]
    assert "plan_storyboard" in o["tools"] and "wangp_python" not in o["tools"]
    assert "must-not-leak" not in json.dumps(o)
    t = h.post("/api/director/test").json()
    assert t["ok"] and t["reply"] and t["error"] is None
    assert h.gateway.requests[-1]["tools"] == [] and h.gateway.requests[-1]["user"].startswith("filmlab:test:")


def test_model_choice_lists_tool_models_tries_them_and_reaches_every_request(make_harness):
    h = make_harness().login()
    models = h.c.get("/api/director/models").json()
    assert [m["id"] for m in models] == ["ollama/gemma4:31b-cloud", "ollama/qwen3.5:9b"]  # tools only, cloud first
    assert models[0]["cloud"] is True and models[1]["cloud"] is False
    # Unknown, tool-less or malformed models are refused; the default stays.
    for bad in ("ollama/llava:latest", "ollama/retired:cloud", "openai/gpt-x", "ollama/$(rm)"):
        assert put(h, {"model": bad}).status_code == 422, bad
    # Try before saving: the test runs on the model asked for, without saving it.
    t = h.post("/api/director/test", {"model": "ollama/qwen3.5:9b"}).json()
    assert t["ok"] and t["model"] == "ollama/qwen3.5:9b" and h.gateway.requests[-1]["_model_header"] == "ollama/qwen3.5:9b"
    assert h.c.get("/api/director").json()["settings"]["model"] == ""
    bad = h.post("/api/director/test", {"model": "ollama/broken:cloud"}).json()
    assert not bad["ok"] and "404" in bad["error"]
    # Saved, it goes with every director request; cleared, OpenClaw's own model is used again.
    assert put(h, {"model": "ollama/qwen3.5:9b"}).json()["model"] == "ollama/qwen3.5:9b"
    pid = new_project(h)
    h.post(f"/api/projects/{pid}/chat", {"text": "Hi"})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    assert h.gateway.requests[-1]["_model_header"] == "ollama/qwen3.5:9b"
    assert put(h, {"model": ""}).json()["model"] == ""
    h.post(f"/api/projects/{pid}/chat", {"text": "Again"})
    h.wait_for(lambda: not h.project(pid)["director_busy"], what="idle")
    assert h.gateway.requests[-1]["_model_header"] is None
