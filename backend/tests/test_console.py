"""The Console: renders, the director's tool calls (with their full result) and FilmLab's warnings, one line each."""

import json
import logging

from tests.test_api import new_project, new_shot
from tests.test_manage import LTX, rendered


def lines(h, **params):
    return h.c.get("/api/console", params=params).json()


def test_console_lines_from_renders_tool_calls_and_warnings(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    s = new_shot(h, pid, template=LTX, title="Opening")
    rendered(h, pid, s["id"])
    h.wait_for(lambda: any(l["message"].startswith("Done: shot 1 “Opening”") for l in lines(h)), what="done line")
    msgs = [l["message"] for l in lines(h, project_id=pid)]
    assert any(m.startswith("Sent shot 1 “Opening” to WanGP") for m in msgs), msgs
    # a refused plan: an error line whose details hold exactly what was sent and why it was refused
    out = h.c.portal.call(h.studio.tools.run, pid, "plan_storyboard", json.dumps({"scenes": [{"title": "X", "shots": [
        {"title": "No why", "prompt": "Fog over the trench at dusk", "seconds": 4}]}]}))
    h.c.portal.call(h.studio.chat._console_tool, pid, "plan_storyboard", json.dumps({"scenes": []}), out)
    err = lines(h, level="error", source="Plan")[0]
    assert err["message"].startswith("plan_storyboard was refused") and err["has_detail"], err
    d = h.c.get(f"/api/console/{err['id']}").json()
    assert "rationale is required" in d["detail"] and '"tool": "plan_storyboard"' in d["detail"]
    # FilmLab's own warnings become System lines
    logging.getLogger("filmlab.test").warning("something odd happened")
    h.wait_for(lambda: any("something odd happened" in l["message"] for l in lines(h, source="System")), what="warning line")
    assert lines(h, level="warn", source="System")[0]["level"] == "warn"
    # search, paging and the other project's lines staying out
    assert all("Opening" in l["message"] for l in lines(h, q="Opening"))
    first = lines(h, limit=2)
    assert len(first) == 2 and lines(h, before_id=first[-1]["id"], limit=1)[0]["id"] < first[-1]["id"]
    assert lines(h, project_id=new_project(h, "Other")) == []
    assert h.c.get("/api/console/999999").status_code == 404
