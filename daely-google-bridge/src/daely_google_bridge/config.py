"""Bridge configuration — YAML-backed, pydantic-validated.

Layout matches `config.example.yaml` at the repo root. The bootstrap CLI
populates `profile_calendar_mapping` after creating Google sub-calendars.
"""
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .colors import VALID_COLOR_IDS


class ColorMappingConfig(BaseModel):
    """Per-profile event-color settings.

    Auto-match: the bridge picks the nearest of Google's 11 event colorIds for
    each profile based on `Profile.colorCode` (#RRGGBB). Set `profile_overrides`
    to pin a profile to a specific Google colorId — useful when two profiles
    auto-match into the same color and you want them visually distinct, or when
    you simply prefer a different palette assignment than the auto-pick.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    profile_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("profile_overrides")
    @classmethod
    def _validate_color_ids(cls, v: dict[str, str]) -> dict[str, str]:
        bad = {p: c for p, c in v.items() if c not in VALID_COLOR_IDS}
        if bad:
            raise ValueError(
                f"profile_overrides has invalid Google colorId(s): {bad}. "
                f"Must be one of {sorted(VALID_COLOR_IDS, key=int)}."
            )
        return v


class HealthServerConfig(BaseModel):
    """Tiny HTTP server exposing /healthz, /readyz, /status.

    Disabled by default for backwards compatibility — set `enabled: true`
    to opt in. Bind defaults to localhost; widen to "0.0.0.0" only behind
    a reverse proxy or firewall.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8090, ge=1024, le=65535)


class BridgeConfig(BaseModel):
    """Static configuration for one bridge instance.

    Secrets (refresh tokens, access tokens) are NOT in this file — they live
    in the SQLite store under the `tokens` table.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Daely
    daely_email: str
    daely_min_pause_seconds: float = 1.0

    # Google
    google_oauth_client_secrets_file: Path
    google_oauth_scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/calendar"],
    )
    oauth_local_port: int = Field(
        default=8080,
        ge=1024,
        le=65535,
        description=(
            "Port the InstalledAppFlow listens on for the OAuth-callback during "
            "bootstrap. Default 8080. Override if that port is already taken on "
            "the host (e.g. by another service). The same port must be used in "
            "the SSH tunnel (`ssh -L <port>:localhost:<port> ...`) and, when "
            "running in Docker, in the bootstrap service's port mapping."
        ),
    )

    # Profile → Google sub-calendar mapping
    # key = Daely profile UUID; value = Google calendarId
    profile_calendar_mapping: dict[str, str] = Field(default_factory=dict)
    fallback_google_calendar_id: str | None = None

    # Profile-color → Google colorId mapping (auto-match + per-profile overrides).
    color_mapping: ColorMappingConfig = Field(default_factory=ColorMappingConfig)

    # Health-check HTTP server (off by default).
    health_server: HealthServerConfig = Field(default_factory=HealthServerConfig)

    # Sync
    poll_interval_minutes: int = 15
    lookback_days: int = 30
    lookahead_days: int = 365

    # Storage
    db_path: Path = Path("./bridge.db")

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"
    log_file: Path | None = None


def load_config(path: Path | str) -> BridgeConfig:
    """Read YAML and validate.

    Raises ValueError if file is missing or malformed; pydantic ValidationError
    propagates as-is on schema mismatch.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    return BridgeConfig.model_validate(raw)


def save_config(cfg: BridgeConfig, path: Path | str, *, backup: bool = True) -> None:
    """Persist config to YAML.

    Writes in place (truncate-and-overwrite). Critically, this does NOT
    rename the file — Docker bind-mounted files can't be renamed (the
    kernel returns `EBUSY: Device or resource busy` because the inode is
    the mount target). Pathlib.write_text() opens for writing and
    truncates, which works through bind-mounts.

    If `backup=True` and the file already exists, the previous version is
    copied to `<path>.bak` (best-effort: a backup that fails to write
    doesn't block the main update — for example, if a parent dir isn't
    writable inside a container, we just skip the backup and log nothing).
    """
    p = Path(path)
    if backup and p.exists():
        backup_path = p.with_suffix(p.suffix + ".bak")
        try:
            backup_path.write_bytes(p.read_bytes())
        except OSError:
            # best-effort; the main config update is what matters
            pass
    data = cfg.model_dump(mode="json", exclude_none=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


__all__ = [
    "BridgeConfig", "ColorMappingConfig", "HealthServerConfig",
    "load_config", "save_config",
]
