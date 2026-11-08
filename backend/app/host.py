"""The Windows helper (scripts/filmlab-host.ps1), seen from FilmLab's server.

The server runs in its WSL distro with Windows interop off, so it can't start Windows programs. The helper runs
on the Windows side and shares a folder with it (%LOCALAPPDATA%\\FilmLab, given to the server as
FILMLAB_HOST_DIR by filmlabctl start):

- host-status.json: the helper's status every few seconds (WanGP's folder, launcher, ports, whether it runs;
  the startup settings);
- requests/*.json: what the cockpit asks for (start WanGP, detect again, settings). The helper only ever starts
  the WanGP launcher it found itself, so a request can't make it run anything else.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import new_id

log = logging.getLogger("filmlab.host")
STALE_S = 30  # the helper writes every 5 s; older than this and it isn't running
ACTIONS = ("start-wangp", "detect", "settings")
SETTINGS = ("wangp_autostart", "wangp_restart", "start_at_logon", "wangp_path")


class HostError(ValueError):
    pass


def wsl_path(win: str) -> Path | None:
    """D:\\wan2gp\\app -> /mnt/d/wan2gp/app (to check a folder the owner typed)."""
    m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", win.strip())
    if not m:
        return None
    return Path("/mnt") / m.group(1).lower() / m.group(2).replace("\\", "/")


class Host:
    def __init__(self, folder: str | None = None):
        folder = folder if folder is not None else os.environ.get("FILMLAB_HOST_DIR", "")
        self.dir = Path(folder) if folder else None

    @property
    def available(self) -> bool:
        return bool(self.dir and self.dir.is_dir())

    def status(self) -> dict:
        """{"available": the shared folder is there, "helper_running": it wrote lately, and its last status}."""
        if not self.available:
            return {"available": False, "helper_running": False}
        try:
            data = json.loads((self.dir / "host-status.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {"available": True, "helper_running": False}
        try:
            age = time.time() - datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, AttributeError):
            age = STALE_S + 1
        return {"available": True, "helper_running": age <= STALE_S, **data}

    def request(self, action: str, **data) -> str:
        if action not in ACTIONS:
            raise HostError(f"action must be one of {', '.join(ACTIONS)}")
        if not self.available:
            raise HostError("FilmLab's Windows helper isn't set up: start FilmLab with filmlabctl start (or the "
                            "FilmLab Studio shortcut)")
        if action == "settings":
            data = {k: v for k, v in data.items() if k in SETTINGS}
            if "wangp_path" in data and data["wangp_path"]:
                folder = str(data["wangp_path"]).strip().rstrip("\\/")
                p = wsl_path(folder)
                if not p or not (p / "wgp.py").is_file():
                    raise HostError(f"{folder} isn't a WanGP folder (the one with wgp.py), or FilmLab can't see it")
                data["wangp_path"] = folder
            for k in ("wangp_autostart", "wangp_restart", "start_at_logon"):
                if k in data:
                    data[k] = bool(data[k])
        reqs = self.dir / "requests"
        reqs.mkdir(exist_ok=True)
        name = f"{int(time.time() * 1000)}-{new_id()[:6]}-{action}"
        tmp = reqs / f"{name}.tmp"
        tmp.write_text(json.dumps({"action": action, **data, "at": datetime.now(timezone.utc).isoformat()}),
                       encoding="utf-8")
        tmp.replace(reqs / f"{name}.json")  # the helper reads *.json only, so it never sees a half-written one
        return name
