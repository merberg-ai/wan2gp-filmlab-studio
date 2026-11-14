"""Generation settings: a project's own values for what WanGP's Advanced mode offers, per video model."""

import pytest

from app.wangp.client import ToolNotAllowed
from tests.test_api import new_project

PDD = "MiniMax H3 Ref2VA Pruned PDD 8 Steps"
TURBO = "MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps"
WAN = "Enhanced Lightning v2"


def view(h, pid, template):
    r = h.c.get(f"/api/projects/{pid}/tuning", params={"template": template})
    assert r.status_code == 200, r.text
    return r.json()


def save(h, pid, template, values):
    return h.c.put(f"/api/projects/{pid}/tuning", json={"template": template, "values": values},
                   headers={"x-csrf-token": h.csrf})


def test_each_model_lists_only_the_settings_it_has(make_harness):
    """A distilled 8-step model fixes its steps and has no guidance or negative prompt; a Wan model has both."""
    h = make_harness().login()
    pid = new_project(h)
    pdd = {f["key"]: f for f in view(h, pid, PDD)["fields"]}
    assert pdd["num_inference_steps"]["locked"].startswith("Fixed by this model") and pdd["num_inference_steps"]["default"] == 8
    assert (pdd["flow_shift"]["default"], pdd["flow_shift"]["min"], pdd["flow_shift"]["max"]) == (12.0, 1, 25)
    assert [c["value"] for c in pdd["skip_steps_cache_type"]["choices"]] == ["", "spectrum", "first_block"]
    assert [c["value"] for c in pdd["skip_steps_multiplier"]["choices"]] == [0.06, 0.08]
    assert (pdd["sliding_window_size"]["min"], pdd["sliding_window_size"]["max"], pdd["sliding_window_size"]["step"]) == (124, 481, 17)
    assert [c["value"] for c in pdd["activated_loras"]["choices"]] == ["grain_v1.safetensors", "style/noir.safetensors"]
    assert not {"guidance_scale", "negative_prompt", "NAG_scale", "sample_solver"} & set(pdd)  # one sampler: no choice
    assert not {"prompt", "seed", "resolution", "video_length", "force_fps", "repeat_generation"} & set(pdd)  # FilmLab's own
    turbo = {f["key"]: f for f in view(h, pid, TURBO)["fields"]}
    assert turbo["num_inference_steps"]["locked"] is None and turbo["num_inference_steps"]["default"] == 8  # the template's, not the model's 20
    assert [c["value"] for c in turbo["sample_solver"]["choices"]] == ["euler", "res_multistep"]
    wan = {f["key"]: f for f in view(h, pid, WAN)["fields"]}
    assert {"guidance_scale", "negative_prompt", "NAG_scale", "sample_solver", "flow_shift"} <= set(wan)
    assert [c["value"] for c in wan["skip_steps_cache_type"]["choices"]] == ["", "mag"]
    assert "activated_loras" not in wan and "loras_multipliers" not in wan  # no LoRAs for this model in WanGP's folder
    assert h.c.get(f"/api/projects/{pid}/tuning", params={"template": "No such"}).status_code == 422


def test_saved_values_reach_the_render_and_only_for_that_project_and_template(make_harness):
    h = make_harness().login()
    pid, other = new_project(h), new_project(h, "Other")
    r = save(h, pid, TURBO, {"num_inference_steps": 12, "sample_solver": "res_multistep", "flow_shift": 12,
                             "activated_loras": ["style/noir.safetensors"], "loras_multipliers": "0.8",
                             "skip_steps_cache_type": "first_block", "skip_steps_multiplier": "0.08"})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"num_inference_steps": 12, "sample_solver": "res_multistep",  # flow_shift 12 is the default
                                  "activated_loras": ["style/noir.safetensors"], "loras_multipliers": "0.8",
                                  "skip_steps_cache_type": "first_block", "skip_steps_multiplier": 0.08}
    shot = lambda project, template: {"project_id": project, "template": template, "prompt": "A trench at dusk",  # noqa: E731
                                      "resolution": "832x480", "frames": 97, "seed": 7}
    sent = h.c.portal.call(h.studio.renders.build_settings, shot(pid, TURBO))
    assert (sent["num_inference_steps"], sent["sample_solver"], sent["activated_loras"]) == (12, "res_multistep", ["style/noir.safetensors"])
    assert (sent["prompt"], sent["resolution"], sent["video_length"], sent["seed"]) == ("A trench at dusk", "832x480", 97, 7)
    assert h.c.portal.call(h.studio.renders.build_settings, shot(pid, PDD))["num_inference_steps"] == 8
    assert "sample_solver" not in h.c.portal.call(h.studio.renders.build_settings, shot(other, TURBO))
    assert view(h, pid, TURBO)["values"]["num_inference_steps"] == 12 and view(h, other, TURBO)["values"] == {}
    # Reset: back to the template's own settings
    assert save(h, pid, TURBO, {}).json()["values"] == {}
    assert h.c.portal.call(h.studio.renders.build_settings, shot(pid, TURBO))["num_inference_steps"] == 8
    assert h.c.portal.call(h.studio.db.fetchone, "SELECT gen_settings FROM projects WHERE id = ?", (pid,))["gen_settings"] is None


def test_values_a_model_cant_take_are_refused_with_the_reason(make_harness):
    h = make_harness().login()
    pid = new_project(h)
    for template, values, needle in (
            (TURBO, {"num_inference_steps": 500}, "Inference steps must be 1-100"),
            (TURBO, {"num_inference_steps": 8.5}, "Inference steps must be a whole number"),
            (PDD, {"num_inference_steps": 20}, "Fixed by this model"),
            (TURBO, {"sample_solver": "bogus"}, "Sampler / scheduler must be one of: Euler, RES Multistep"),
            (TURBO, {"activated_loras": ["nope.safetensors"]}, "pick from the LoRAs WanGP lists"),
            (TURBO, {"prompt": "sneaky", "seed": 1}, "prompt isn't a setting of this model; seed isn't a setting"),
            (PDD, {"negative_prompt": "blurry"}, "negative_prompt isn't a setting of this model"),
            # in range for each field, but WanGP's own check of the whole says no
            (WAN, {"sliding_window_size": 9, "sliding_window_overlap": 17},
             "WanGP doesn't accept these settings: sliding_window_overlap must be smaller")):
        r = save(h, pid, template, values)
        assert r.status_code == 422 and needle in r.json()["detail"], (values, r.text)
    assert view(h, pid, TURBO)["values"] == {} and view(h, pid, WAN)["values"] == {}  # nothing was kept
    # a template that fails WanGP's check for its own reasons (a missing LoRA) still takes values: only new errors count
    assert save(h, pid, TURBO, {"num_inference_steps": 10}).status_code == 200


def test_wangp_model_is_read_only_on_the_allowlist(make_harness):
    h = make_harness().login()
    call = lambda args: h.c.portal.call(h.studio.wangp.call, "wangp_model", args)  # noqa: E731
    assert call({"model_type": "ltx2_22B_distilled", "action": "defaults"})["num_inference_steps"] == 8
    for args in ({"model_type": "ltx2_22B_distilled", "action": "user_settings"}, {"model_type": "ltx2_22B_distilled"},
                 {"model_type": "ltx2_22B_distilled", "action": "definition", "arguments": {"property": "infos"}}):
        with pytest.raises(ToolNotAllowed):
            call(args)
