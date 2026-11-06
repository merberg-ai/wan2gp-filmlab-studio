"""Runtime settings, read from the environment.

filmlabctl loads ~/.config/filmlab/secrets.env (and filmlab.env if present)
before starting the backend, so secrets never live in the repo.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name, default):
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("FILMLAB_DATA_DIR", str(Path.home() / "filmlab" / "data"))))
    media_dir: Path = field(default_factory=lambda: Path(_env("FILMLAB_MEDIA_DIR", str(Path.home() / "filmlab" / "media"))))
    static_dir: Path | None = field(default_factory=lambda: Path(p) if (p := os.environ.get("FILMLAB_STATIC_DIR")) else None)
    host: str = field(default_factory=lambda: _env("FILMLAB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("FILMLAB_PORT", "19300")))
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        _env("FILMLAB_ALLOWED_ORIGINS", "http://127.0.0.1:19300,http://localhost:19300").split(",")))
    secure_cookies: bool = field(default_factory=lambda: _env("FILMLAB_SECURE_COOKIES", "0") == "1")
    session_hours: int = field(default_factory=lambda: int(_env("FILMLAB_SESSION_HOURS", "168")))
    # Clients outside these networks get 403 before authentication (HTTP and /ws).
    allowed_nets: tuple[str, ...] = field(default_factory=lambda: tuple(
        _env("FILMLAB_ALLOWED_NETS", "127.0.0.1/32,::1/128").split(",")))  # LAN set by the installer
    ssl_certfile: str | None = field(default_factory=lambda: os.environ.get("FILMLAB_SSL_CERTFILE") or None)
    ssl_keyfile: str | None = field(default_factory=lambda: os.environ.get("FILMLAB_SSL_KEYFILE") or None)
    login_max_failures: int = field(default_factory=lambda: int(_env("FILMLAB_LOGIN_MAX_FAILURES", "5")))
    login_window_s: int = field(default_factory=lambda: int(_env("FILMLAB_LOGIN_WINDOW_S", "600")))

    backup_dir: Path = field(default_factory=lambda: Path(_env("FILMLAB_BACKUP_DIR", str(Path.home() / "filmlab" / "backups"))))

    wangp_url: str = field(default_factory=lambda: _env("FILMLAB_WANGP_URL", "http://127.0.0.1:8000/mcp"))
    wangp_workspace: str = field(default_factory=lambda: _env("FILMLAB_WANGP_WORKSPACE", "FilmLab"))

    gateway_url: str = field(default_factory=lambda: _env("FILMLAB_GATEWAY_URL", "http://127.0.0.1:19189"))
    gateway_token: str = field(default_factory=lambda: _env("OPENCLAW_GATEWAY_TOKEN", ""), repr=False)
    director_agent: str = field(default_factory=lambda: _env("FILMLAB_DIRECTOR_AGENT", "director"))
    director_max_tool_rounds: int = field(default_factory=lambda: int(_env("FILMLAB_DIRECTOR_MAX_TOOL_ROUNDS", "6")))
    # Read only (Settings → Director shows the model and the installed instructions).
    openclaw_config: Path = field(default_factory=lambda: Path(_env("FILMLAB_OPENCLAW_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json"))))
    agent_workspace: Path = field(default_factory=lambda: Path(_env("FILMLAB_AGENT_WORKSPACE", str(Path.home() / "filmlab" / "agent-workspace"))))

    @property
    def loopback_only(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "filmlab.sqlite"
