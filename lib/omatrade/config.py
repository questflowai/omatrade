"""Configuration: where the Questflow API is, and which OAuth client we are.

Defaults are deliberately unusable placeholders (`.invalid` never resolves),
so a build can never silently talk to a host nobody configured. Real values
come from `~/.config/omatrade/config.toml` or the environment:

    api_base   = "https://api.questflow.example"   # OMATRADE_API_BASE
    web_base   = "https://app.questflow.example"   # OMATRADE_WEB_BASE
    client_id  = "..."                             # OMATRADE_CLIENT_ID

`OMATRADE_DEV_ALLOW_HTTP_LOCALHOST=1` (or `dev_allow_http_localhost = true`)
permits plain http to a loopback host for local development only.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import urls

APP = "omatrade"
PLUGIN_ID = "questflow.omatrade"

PLACEHOLDER_API_BASE = "https://api.example.invalid"
PLACEHOLDER_WEB_BASE = "https://app.example.invalid"
PLACEHOLDER_CLIENT_ID = "omatrade-desktop-placeholder"

# The only scopes this client ever asks for. None of them can move funds,
# place orders or read credentials.
SCOPES = ("read:profile", "read:signals", "read:positions")


def _truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / APP


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    return (Path(base) / APP) if base else state_dir()


@dataclass
class Config:
    api_base: str = PLACEHOLDER_API_BASE
    web_base: str = PLACEHOLDER_WEB_BASE
    client_id: str = PLACEHOLDER_CLIENT_ID
    scopes: tuple = SCOPES
    allow_http_loopback: bool = False
    state_dir: Path = field(default_factory=state_dir)
    config_dir: Path = field(default_factory=config_dir)
    runtime_dir: Path = field(default_factory=runtime_dir)

    @property
    def configured(self):
        return (self.api_base != PLACEHOLDER_API_BASE
                and self.client_id != PLACEHOLDER_CLIENT_ID)

    def public(self):
        """What may be shown or written to the snapshot: no secrets live
        here, but keep the shape explicit anyway."""
        return {
            "apiBase": self.api_base,
            "webBase": self.web_base,
            "clientId": self.client_id,
            "scopes": list(self.scopes),
            "configured": self.configured,
            "devHttpLoopback": self.allow_http_loopback,
        }


class ConfigError(ValueError):
    pass


def load(env=None, path=None):
    env = os.environ if env is None else env
    cfg_dir = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP
    path = Path(path) if path else cfg_dir / "config.toml"
    raw = {}
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None

    allow = _truthy(env.get("OMATRADE_DEV_ALLOW_HTTP_LOCALHOST", raw.get("dev_allow_http_localhost", False)))
    api_base = env.get("OMATRADE_API_BASE") or raw.get("api_base") or PLACEHOLDER_API_BASE
    web_base = env.get("OMATRADE_WEB_BASE") or raw.get("web_base") or PLACEHOLDER_WEB_BASE
    client_id = env.get("OMATRADE_CLIENT_ID") or raw.get("client_id") or PLACEHOLDER_CLIENT_ID
    try:
        api_base = urls.validate_base(api_base, allow_http_loopback=allow)
        web_base = urls.validate_base(web_base, allow_http_loopback=allow, what="web base URL")
    except urls.UnsafeURL as e:
        raise ConfigError(str(e)) from None
    if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 200:
        raise ConfigError("client_id must be a non-empty string")

    state = Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / APP
    runtime = Path(env["XDG_RUNTIME_DIR"]) / APP if env.get("XDG_RUNTIME_DIR") else state
    return Config(api_base=api_base, web_base=web_base, client_id=client_id.strip(),
                  allow_http_loopback=allow, state_dir=state, config_dir=cfg_dir,
                  runtime_dir=runtime)
