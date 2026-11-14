"""Generation settings: a project's own values for what WanGP's Advanced mode offers (steps, guidance, sampler,
step skipping, LoRAs, sliding window, ...), per video model.

What a model accepts differs a lot (a distilled 8-step model fixes its step count; only some models have guidance,
a negative prompt, NAG or step-skipping caches), so nothing here is a fixed form. For a template, FilmLab asks WanGP
for the model's defaults and its definition (wangp_model: read-only) and shows only the settings that model uses,
with the same ranges and choices as WanGP's own Advanced tabs (mirrored from WanGP's shared/extra_settings.py and
wgp.py), and the template's value as the default. The owner's changes are kept per project and template
(projects.gen_settings) and laid over the template's settings when a shot renders.

Never offered: what FilmLab sets itself or relies on (prompt, size, length, seed, references, sound, fps, the
number of videos per prompt, output names)."""

import json
import time

from .wangp.client import WanGPError

OFF_ON = [("Off", 0), ("On", 1)]
SKIP_SPEEDS = [("around x1.5 speed up", 1.5), ("around x1.75 speed up", 1.75), ("around x2 speed up", 2.0),
               ("around x2.25 speed up", 2.25), ("around x2.5 speed up", 2.5)]
CACHES = (("tea_cache", "Tea Cache", "tea"), ("mag_cache", "Mag Cache", "mag"),
          ("spectrum_cache", "Spectrum feature forecasting", "spectrum"),
          ("first_block_cache", "First Block Cache", "first_block"))
SPEED_CACHES = ("tea_cache", "mag_cache", "first_block_cache")
MAX_TEXT, CACHE_S = 1000, 600


class TuningError(ValueError):
    pass


def _has(key):
    return lambda d, m: key in d


def _flag(key, also=None):
    return lambda d, m: bool(m.get(key)) and (also is None or also in d)


def _phase(n, key):
    return lambda d, m: (key in d and int(m.get("guidance_max_phases") or 0) >= n
                         and int(m.get("visible_phases", n) or 0) >= n)


def _any_cache(keys=tuple(c[0] for c in CACHES)):
    return lambda d, m: any(m.get(k) for k in keys)


def _window(bound, fallback):
    def get(d, m):
        w = m.get("sliding_window_defaults") or {}
        if bound == "window_step":
            return w.get(bound, m.get("frames_steps", fallback))
        return w.get(bound, fallback)
    return get


def _own(key, bound, fallback, alias=None):
    """A bound the model's definition may set for this key ({"min", "max", "step"})."""
    def get(d, m):
        cfg = m.get(key) if isinstance(m.get(key), dict) else {}
        return cfg.get(bound, cfg.get(alias, fallback) if alias else fallback)
    return get


def F(key, label, group, kind, when, lo=None, hi=None, step=None, choices=None, default=None, help="", locked=None):
    return {"key": key, "label": label, "group": group, "kind": kind, "when": when, "lo": lo, "hi": hi, "step": step,
            "choices": choices, "default": default, "help": help, "locked": locked}


G, S, Q, P, W, L = "Sampling", "Step skipping", "Quality", "Finishing", "Long shots (sliding window)", "LoRAs"
CATALOGUE = [
    F("num_inference_steps", "Inference steps", G, "int", _has("num_inference_steps"), 1, 100, 1,
      help="More steps: finer detail and a longer render. Fewer: faster, rougher.",
      locked=lambda d, m: "Fixed by this model: it is distilled to run exactly this many steps."
      if m.get("lock_inference_steps") else None),
    F("guidance_scale", "Guidance (CFG)", G, "number", _phase(1, "guidance_scale"), 1, 20, 0.1,
      help="How strictly the picture follows the prompt. 1 turns guidance off (fastest)."),
    F("guidance2_scale", "Guidance, phase 2", G, "number", _phase(2, "guidance2_scale"), 1, 20, 0.1),
    F("switch_threshold", "Phase switch threshold", G, "number", _has("switch_threshold"),
      _own("switch_threshold", "min", 0), _own("switch_threshold", "max", 1000), _own("switch_threshold", "step", 1),
      help="The noise level at which the model moves from phase 1 to phase 2."),
    F("embedded_guidance_scale", "Embedded guidance", G, "number", _flag("embedded_guidance", "embedded_guidance_scale"),
      1, 20, 0.5),
    F("audio_guidance_scale", "Audio guidance", G, "number", _flag("audio_guidance", "audio_guidance_scale"), 1, 20, 0.5),
    F("sample_solver", "Sampler / scheduler", G, "choice",
      lambda d, m: len(m.get("sample_solvers") or []) >= 2, choices=lambda d, m: m["sample_solvers"]),
    F("flow_shift", "Shift scale", G, "number", _flag("flow_shift", "flow_shift"), 1, 25, 0.1,
      help="Higher values spend more of the steps on the overall composition and motion, lower on fine detail."),
    F("negative_prompt", "Negative prompt", G, "text",
      lambda d, m: "negative_prompt" in d and not m.get("no_negative_prompt"),
      help="What the picture should not show. Ignored when guidance is 1, unless NAG is on."),
    F("NAG_scale", "NAG scale", G, "number", _flag("NAG"), _own("NAG_scale", "min", 1), _own("NAG_scale", "max", 20),
      _own("NAG_scale", "step", 0.01), default=1,
      help="Enforces the negative prompt even without guidance. Above 1 turns it on."),
    F("NAG_tau", "NAG tau", G, "number", _flag("NAG"), _own("NAG_tau", "min", 1), _own("NAG_tau", "max", 5),
      _own("NAG_tau", "step", 0.01)),
    F("NAG_alpha", "NAG alpha", G, "number", _flag("NAG"), _own("NAG_alpha", "min", 0), _own("NAG_alpha", "max", 2),
      _own("NAG_alpha", "step", 0.01)),

    F("skip_steps_cache_type", "Step-skipping cache", S, "choice", _any_cache(), default="",
      choices=lambda d, m: [("None", "")] + [(label, value) for flag, label, value in CACHES if m.get(flag)],
      help="Skips some of the model's work on steps that change little: faster renders, a little less quality."),
    F("skip_steps_multiplier", "Cache strength", S, "choice", _any_cache(SPEED_CACHES),
      choices=lambda d, m: m.get("skip_steps_multiplier_choices") or SKIP_SPEEDS),
    F("skip_steps_start_step_perc", "Start skipping at (% of the steps)", S, "int", _any_cache(), 0, 100, 1, default=0,
      help="Keep the first steps whole so the cache has something stable to work from."),

    F("perturbation_switch", "Perturbation", Q, "choice", _flag("perturbation"),
      choices=lambda d, m: m.get("perturbation_choices") or [("Off", 0), ("Skip Layer Guidance", 1)],
      help="Can improve picture quality; needs guidance above 1."),
    F("perturbation_start_perc", "Perturbation from (% of the steps)", Q, "int", _flag("perturbation"), 0, 100, 1),
    F("perturbation_end_perc", "Perturbation until (% of the steps)", Q, "int", _flag("perturbation"), 0, 100, 1),
    F("apg_switch", "Adaptive projected guidance", Q, "choice", _flag("adaptive_projected_guidance"), choices=OFF_ON,
      help="Corrects colours drifting towards over-saturation in long videos; needs guidance above 1."),
    F("cfg_star_switch", "CFG Zero Star", Q, "choice", _flag("cfg_star"), choices=OFF_ON,
      help="Better adherence to the prompt; needs guidance above 1."),
    F("cfg_zero_step", "CFG zero below this layer", Q, "int", _flag("cfg_zero"), -1, 39, 1),
    F("motion_amplitude", "Motion amplitude", Q, "number", _flag("motion_amplitude"), 1, 1.4, 0.01,
      help="Experimental: speeds motion up. 1 is off; WanGP suggests 1.15."),
    F("self_refiner_setting", "Self-refining sampling", Q, "choice", _flag("self_refiner"),
      choices=[("Off", 0), ("On, P1 norm", 1), ("On, P2 norm", 2)], default=0,
      help="Extra passes on some steps: should improve motion, takes longer."),
    F("image_refs_relative_size", "Reference image scale (%)", Q, "int", _has("image_refs_relative_size"),
      _own("image_refs_relative_size", "min", 20), _own("image_refs_relative_size", "max", 400),
      _own("image_refs_relative_size", "step", 1),
      help="How large the references are taken, against the video's size: changes the composition."),
    F("remove_background_images_ref", "Reference backgrounds", Q, "choice", _has("remove_background_images_ref"),
      choices=[("Keep", 0), ("Remove", 1)]),

    F("spatial_upsampling", "Upscale the picture", P, "choice", _has("spatial_upsampling"), default="",
      choices=[("Off", ""), ("Lanczos x1.5", "lanczos1.5"), ("Lanczos x2", "lanczos2")],
      help="Takes come out larger than the shot's size (smoother edges, no new detail)."),
    F("temporal_upsampling", "Smoother motion (frame interpolation)", P, "choice", _has("temporal_upsampling"), default="",
      choices=[("Off", ""), ("RIFE x2", "rife2"), ("RIFE x4", "rife4")],
      help="Adds frames in between: the same length at a higher frame rate."),
    F("film_grain_intensity", "Film grain", P, "number", _has("spatial_upsampling"), 0, 1, 0.01, default=0,
      help="0 is off."),
    F("film_grain_saturation", "Film grain saturation", P, "number", _has("spatial_upsampling"), 0, 1, 0.01, default=0.5),

    F("sliding_window_size", "Window size (frames)", W, "int", _flag("sliding_window", "sliding_window_size"),
      _window("window_min", 5), _window("window_max", 257), _window("window_step", 4),
      help="A shot longer than this is generated in overlapping windows."),
    F("sliding_window_overlap", "Overlap between windows (frames)", W, "int",
      _flag("sliding_window", "sliding_window_overlap"), _window("overlap_min", 1), _window("overlap_max", 97),
      _window("overlap_step", 4), help="More overlap keeps windows consistent and needs more of them."),
    F("sliding_window_color_correction_strength", "Colour match between windows", W, "number",
      lambda d, m: bool(m.get("sliding_window") and m.get("color_correction")), 0, 1, 0.01, help="0 is off."),
    F("sliding_window_overlap_noise", "Noise on overlapped frames", W, "int",
      lambda d, m: bool(m.get("sliding_window") and m.get("vace_class")), 0, 150, 1, help="Reduces blur at the joins."),
    F("sliding_window_discard_last_frames", "Drop a window's last frames", W, "int",
      _flag("sliding_window", "sliding_window_discard_last_frames"), 0, 20, 1),
    F("sliding_window_trim_first_frames", "Trim the first frames", W, "int",
      _flag("sliding_window", "sliding_window_trim_first_frames"), 0, 10, 1),
    F("RIFLEx_setting", "RIFLEx (videos over 5 s)", W, "choice", _flag("riflex"),
      choices=[("Auto (on over 5 s)", 0), ("Always on", 1), ("Always off", 2)]),

    F("activated_loras", "Active LoRAs", L, "loras", _has("activated_loras"), default=[],
      help="LoRAs from WanGP's folder for this model. Mention a LoRA's trigger word in the prompt if it has one."),
    F("loras_multipliers", "LoRA multipliers", L, "text", _has("activated_loras"), default="",
      help="One number per active LoRA, in order, separated by spaces (e.g. 1 0.8). Empty: 1 for each."),
]
KEYS = {f["key"] for f in CATALOGUE}


def _num(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


class Tuning:
    def __init__(self, db, wangp, renders):
        self.db, self.wangp, self.renders = db, wangp, renders
        self._models: dict[str, tuple[float, dict, dict, list[str]]] = {}

    async def _model(self, model_type: str) -> tuple[dict, dict, list[str]]:
        """(defaults, definition, LoRA names) of a model, as WanGP reports them; kept for a few minutes."""
        hit = self._models.get(model_type)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1], hit[2], hit[3]
        defaults = await self.wangp.call("wangp_model", {"model_type": model_type, "action": "defaults"})
        definition = await self.wangp.call("wangp_model", {"model_type": model_type, "action": "definition"})
        loras: list[str] = []
        if "activated_loras" in defaults:
            cursor = None
            for _ in range(10):
                page = await self.wangp.call("wangp_model", {"model_type": model_type, "action": "loras",
                                                             "arguments": {"cursor": cursor} if cursor else {}})
                loras += [str(x) for x in page.get("loras") or []]
                cursor = page.get("next_cursor")
                if not page.get("has_more") or not cursor:
                    break
        self._models[model_type] = (time.monotonic(), defaults, definition, loras)
        return defaults, definition, loras

    async def _template(self, name: str) -> tuple[dict, dict]:
        tpl = await self.renders.resolve_template(name)
        full = await self.wangp.call("wangp_templates", {"task": tpl.get("task", "gen_video"), "template": tpl["template"]})
        return tpl, dict(full.get("settings") or {})

    async def fields(self, template: str) -> tuple[dict, dict, list[dict]]:
        """(template row, the template's settings, the settings this model offers)."""
        tpl, base = await self._template(template)
        defaults, definition, loras = await self._model(tpl["model_type"])
        known = defaults | base  # the template's own values are what a render starts from
        out = []
        for f in CATALOGUE:
            if not f["when"](known, definition):
                continue
            def r(v):  # a bound or choice list may depend on the model
                return v(known, definition) if callable(v) else v
            field = {"key": f["key"], "label": f["label"], "group": f["group"], "kind": f["kind"], "help": f["help"],
                     "default": known.get(f["key"], f["default"]),
                     "locked": f["locked"](known, definition) if f["locked"] else None}
            if f["kind"] in ("int", "number"):
                field |= {"min": _num(r(f["lo"])), "max": _num(r(f["hi"])), "step": _num(r(f["step"])) or 1}
            if f["kind"] == "choice":
                field["choices"] = [{"label": str(c[0]), "value": c[1]} for c in r(f["choices"])]
            if f["kind"] == "loras":
                # the template's own LoRAs first (an accelerator LoRA a distilled template needs), then WanGP's list
                own = [str(x) for x in field["default"] or []] if isinstance(field["default"], list) else []
                names = list(dict.fromkeys(own + loras))
                if not names:
                    continue
                field["choices"] = [{"label": x, "value": x} for x in names]
                if own:
                    field["help"] += " The ones already ticked come with the template: the model may need them."
            out.append(field)
        if not any(f["key"] == "activated_loras" for f in out):
            out = [f for f in out if f["key"] != "loras_multipliers"]
        return tpl, base, out

    @staticmethod
    def clean(values: dict, fields: list[dict]) -> dict:
        """The owner's values, checked against what the model offers; a value equal to the default isn't kept (the
        setting then follows the template). Raises TuningError naming every problem."""
        by_key = {f["key"]: f for f in fields}
        out, errors = {}, []
        for key, value in (values or {}).items():
            f = by_key.get(key)
            if not f:
                errors.append(f"{key} isn't a setting of this model")
                continue
            if value is None or (isinstance(value, str) and not value.strip() and f["kind"] in ("int", "number")):
                continue  # cleared: back to the template's value
            if f["locked"]:
                if value != f["default"]:
                    errors.append(f"{f['label']}: {f['locked']}")
                continue
            if f["kind"] in ("int", "number"):
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    errors.append(f"{f['label']} must be a number")
                    continue
                if isinstance(value, bool) or n != n or (f["kind"] == "int" and not n.is_integer()):
                    errors.append(f"{f['label']} must be a {'whole ' if f['kind'] == 'int' else ''}number")
                    continue
                if (f["min"] is not None and n < f["min"]) or (f["max"] is not None and n > f["max"]):
                    errors.append(f"{f['label']} must be {f['min']}-{f['max']}")
                    continue
                value = int(n) if f["kind"] == "int" else _num(round(n, 4))
            elif f["kind"] == "choice":
                match = next((c["value"] for c in f["choices"] if c["value"] == value or str(c["value"]) == str(value)), None)
                if match is None and not any(c["value"] == value for c in f["choices"]):
                    errors.append(f"{f['label']} must be one of: {', '.join(c['label'] for c in f['choices'])}")
                    continue
                value = match
            elif f["kind"] == "loras":
                names = {c["value"] for c in f["choices"]}
                if not isinstance(value, list) or any(v not in names for v in value):
                    errors.append(f"{f['label']}: pick from the LoRAs WanGP lists for this model")
                    continue
                value = list(dict.fromkeys(value))
            else:
                value = str(value).strip()
                if len(value) > MAX_TEXT:
                    errors.append(f"{f['label']} must be at most {MAX_TEXT} characters")
                    continue
            if value != f["default"]:
                out[key] = value
        if errors:
            raise TuningError("; ".join(errors))
        return out

    async def stored(self, project_id: str) -> dict:
        row = await self.db.fetchone("SELECT gen_settings FROM projects WHERE id = ?", (project_id,))
        try:
            data = json.loads((row or {}).get("gen_settings") or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    async def overrides(self, project_id: str, template: str) -> dict:
        """What a render of this project lays over the template's settings (only settings FilmLab offers)."""
        values = (await self.stored(project_id)).get(template) or {}
        return {k: v for k, v in values.items() if k in KEYS} if isinstance(values, dict) else {}

    async def view(self, project_id: str, template: str) -> dict:
        tpl, _, fields = await self.fields(template)
        values = await self.overrides(project_id, tpl["template"])
        offered = {f["key"] for f in fields}
        return {"template": tpl["template"], "model_type": tpl["model_type"], "fields": fields,
                "values": {k: v for k, v in values.items() if k in offered}}

    async def save(self, project_id: str, template: str, values: dict, actor: str) -> dict:
        tpl, base, fields = await self.fields(template)
        clean = self.clean(values, fields)
        if clean:
            await self._wangp_accepts(base, clean)
        data = await self.stored(project_id)
        if clean:
            data[tpl["template"]] = clean
        else:
            data.pop(tpl["template"], None)
        await self.db.execute("UPDATE projects SET gen_settings = ?, updated_at = updated_at WHERE id = ?",
                              (json.dumps(data) if data else None, project_id))
        await self.db.audit(actor, "project.gen_settings", project_id, {"template": tpl["template"], "values": clean})
        return await self.view(project_id, tpl["template"])

    async def _wangp_accepts(self, base: dict, clean: dict):
        """WanGP's own check of the settings with the owner's values: whatever it reports that it didn't report for
        the template alone (a template checked without references always misses them) is the values' doing."""
        probe = dict(base, prompt="FilmLab settings check")

        async def errors(settings) -> set[str]:
            try:
                res = await self.wangp.call("wangp_validate", {"settings": settings})
            except WanGPError as exc:
                return {str(exc)[:300]}
            return {d.get("message", "") for d in res.get("diagnostics", [])
                    if d.get("severity") == "error" and "multiples of" not in d.get("message", "")}
        new = await errors(probe | clean) - await errors(probe)
        if new:
            raise TuningError("WanGP doesn't accept these settings: " + "; ".join(sorted(new)))
