"""Turn WanGP failures into a short code, a plain message and a next step.

The raw error text is always kept on the job; this only adds what the owner reads.
"""

import re

# (code, pattern on the raw error, message, next step) - first match wins
RULES = [
    ("cancelled", r"\bcancel", "The render was cancelled.", "Render again when you're ready."),
    ("lost_on_restart", r"restarted and no output|unknown_after_restart",
     "WanGP restarted and this take was lost.", "Render again; it gets a fresh request."),
    ("gpu_oom", r"out of memory|\bOOM\b|CUDA error: out|allocat\w+ .*memory",
     "The GPU ran out of memory.", "Try a shorter clip or a lower resolution, or close other GPU work."),
    ("model_missing", r"not installed|missing (model|file|weights)|no such file|download(ing)? failed|"
                      r"model .*not (found|available)|unknown model",
     "The model files aren't available in WanGP.", "Open WanGP and make sure this model is installed."),
    ("invalid_settings", r"invalid|not supported|must be|out of range|validation|unsupported",
     "WanGP rejected the settings.", "Run Validate on the shot to see what WanGP expects."),
    ("download_corrupt", r"SHA-256", "The finished file didn't arrive intact.", "Render again."),
    ("wangp_unreachable", r"ConnectError|ConnectTimeout|not reachable|ReadTimeout|RemoteProtocolError",
     "FilmLab couldn't reach WanGP.", "Check WanGP is running with MCP enabled."),
]
FALLBACK = ("wangp_error", "WanGP reported an error.", "See the details below; render again to retry.")
_COMPILED = [(code, re.compile(pat, re.I), msg, hint) for code, pat, msg, hint in RULES]
_BY_CODE = {code: (msg, hint) for code, _, msg, hint in _COMPILED} | {FALLBACK[0]: FALLBACK[1:]}


def classify(raw: str | None) -> str | None:
    if not raw:
        return None
    for code, pat, _, _ in _COMPILED:
        if pat.search(raw):
            return code
    return FALLBACK[0]


def describe(code: str | None) -> dict | None:
    if not code:
        return None
    msg, hint = _BY_CODE.get(code, FALLBACK[1:])
    return {"code": code, "message": msg, "hint": hint}
