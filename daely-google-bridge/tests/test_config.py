"""Config load/save/validation."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from daely_google_bridge.config import BridgeConfig, load_config, save_config


# ────────── defaults ──────────

def test_minimal_config_has_sensible_defaults():
    cfg = BridgeConfig(
        daely_email="x@example.com",
        google_oauth_client_secrets_file=Path("/tmp/client.json"),
    )
    assert cfg.poll_interval_minutes == 15
    assert cfg.lookback_days == 30
    assert cfg.lookahead_days == 365
    assert cfg.daely_min_pause_seconds == 1.0
    assert cfg.profile_calendar_mapping == {}
    assert cfg.fallback_google_calendar_id is None
    assert cfg.google_oauth_scopes == ["https://www.googleapis.com/auth/calendar"]
    assert cfg.log_level == "INFO"
    # Color mapping ships on by default with no overrides.
    assert cfg.color_mapping.enabled is True
    assert cfg.color_mapping.profile_overrides == {}


# ────────── color_mapping section ──────────

def test_color_mapping_accepts_valid_color_id_overrides():
    cfg = BridgeConfig(
        daely_email="x@example.com",
        google_oauth_client_secrets_file=Path("/tmp/client.json"),
        color_mapping={"profile_overrides": {
            "00000000-0000-0000-0004-000000000001": "11",
            "00000000-0000-0000-0004-000000000002": "7",
        }},
    )
    assert cfg.color_mapping.profile_overrides["00000000-0000-0000-0004-000000000001"] == "11"


def test_color_mapping_rejects_invalid_color_id():
    with pytest.raises(ValidationError) as excinfo:
        BridgeConfig(
            daely_email="x@example.com",
            google_oauth_client_secrets_file=Path("/tmp/client.json"),
            color_mapping={"profile_overrides": {"some-uuid": "12"}},
        )
    assert "invalid Google colorId" in str(excinfo.value)


def test_color_mapping_rejects_zero_and_unknown_strings():
    for bad in ["0", "abc", ""]:
        with pytest.raises(ValidationError):
            BridgeConfig(
                daely_email="x@example.com",
                google_oauth_client_secrets_file=Path("/tmp/client.json"),
                color_mapping={"profile_overrides": {"some-uuid": bad}},
            )


def test_color_mapping_can_be_disabled():
    cfg = BridgeConfig(
        daely_email="x@example.com",
        google_oauth_client_secrets_file=Path("/tmp/client.json"),
        color_mapping={"enabled": False},
    )
    assert cfg.color_mapping.enabled is False
    assert cfg.color_mapping.profile_overrides == {}


def test_color_mapping_extra_field_rejected():
    with pytest.raises(ValidationError):
        BridgeConfig(
            daely_email="x@example.com",
            google_oauth_client_secrets_file=Path("/tmp/client.json"),
            color_mapping={"unknown_key": True},
        )


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        BridgeConfig(
            daely_email="x@example.com",
            google_oauth_client_secrets_file=Path("/tmp/x"),
            unknown_field="oops",
        )


# ────────── YAML round-trip ──────────

def test_save_and_load_roundtrip(tmp_path):
    cfg = BridgeConfig(
        daely_email="user@example.com",
        google_oauth_client_secrets_file=Path("/secrets/client.json"),
        profile_calendar_mapping={
            "00000000-0000-0000-0004-000000000001": "cal-A@google",
            "00000000-0000-0000-0004-000000000002": "cal-B@google",
        },
        poll_interval_minutes=30,
        lookback_days=60,
    )
    p = tmp_path / "config.yaml"
    save_config(cfg, p, backup=False)
    loaded = load_config(p)
    assert loaded.daely_email == cfg.daely_email
    assert loaded.profile_calendar_mapping == cfg.profile_calendar_mapping
    assert loaded.poll_interval_minutes == 30
    assert loaded.lookback_days == 60


def test_save_creates_backup_when_file_exists(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("daely_email: old@example.com\n"
                 "google_oauth_client_secrets_file: /tmp/x\n")
    cfg = BridgeConfig(
        daely_email="new@example.com",
        google_oauth_client_secrets_file=Path("/tmp/y"),
    )
    save_config(cfg, p, backup=True)
    bak = p.with_suffix(".yaml.bak")
    assert bak.exists()
    assert "old@example.com" in bak.read_text()
    assert "new@example.com" in p.read_text()


def test_save_no_backup_when_disabled(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("daely_email: old@example.com\ngoogle_oauth_client_secrets_file: /tmp/x\n")
    cfg = BridgeConfig(daely_email="x@example.com",
                       google_oauth_client_secrets_file=Path("/tmp/x"))
    save_config(cfg, p, backup=False)
    assert not p.with_suffix(".yaml.bak").exists()


def test_save_does_not_rename_main_file(tmp_path, monkeypatch):
    """Critical for Docker: bind-mounted files cannot be renamed (EBUSY).
    save_config must overwrite in place, not call Path.rename on the target.
    """
    p = tmp_path / "config.yaml"
    p.write_text(
        "daely_email: old@example.com\n"
        "google_oauth_client_secrets_file: /tmp/x\n"
    )
    original_inode = p.stat().st_ino

    # Force any rename attempt to fail loudly so we'd notice if save_config
    # accidentally re-introduced one in the future.
    def _no_rename(self, target):
        raise OSError(16, "Device or resource busy", str(self))
    monkeypatch.setattr(Path, "rename", _no_rename)

    cfg = BridgeConfig(
        daely_email="new@example.com",
        google_oauth_client_secrets_file=Path("/tmp/y"),
    )
    save_config(cfg, p, backup=True)  # must succeed despite rename being broken

    # main file: same inode (in-place overwrite), new content
    assert p.stat().st_ino == original_inode
    loaded = load_config(p)
    assert loaded.daely_email == "new@example.com"
    # backup written separately (copy, not rename)
    assert p.with_suffix(".yaml.bak").exists()
    assert "old@example.com" in p.with_suffix(".yaml.bak").read_text()


def test_save_tolerates_unwritable_backup_path(tmp_path, monkeypatch):
    """If the backup write fails (e.g. read-only parent in container), the
    main config still gets updated — backup is best-effort, not blocking."""
    p = tmp_path / "config.yaml"
    p.write_text("daely_email: old@example.com\ngoogle_oauth_client_secrets_file: /tmp/x\n")

    real_write_bytes = Path.write_bytes

    def _selective_write_bytes(self, data):
        if self.suffix == ".bak":
            raise OSError("simulated read-only backup target")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _selective_write_bytes)

    cfg = BridgeConfig(
        daely_email="new@example.com",
        google_oauth_client_secrets_file=Path("/tmp/y"),
    )
    save_config(cfg, p, backup=True)  # must not raise

    loaded = load_config(p)
    assert loaded.daely_email == "new@example.com"
    # backup wasn't written (simulated failure)
    assert not p.with_suffix(".yaml.bak").exists()


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_load_invalid_yaml_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("this is not :: valid: yaml::\n  - {{")
    with pytest.raises(yaml.YAMLError):
        load_config(p)


def test_load_validation_error_propagates(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("# missing required fields\n")
    with pytest.raises(ValidationError):
        load_config(p)


# ────────── example file is itself valid ──────────

def test_example_yaml_loads_and_validates():
    """The shipped config.example.yaml must parse and validate cleanly."""
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    assert example.exists(), "config.example.yaml missing from repo root"
    cfg = load_config(example)
    # Sanity checks on the example
    assert cfg.daely_email == "you@example.com"
    assert cfg.google_oauth_scopes == ["https://www.googleapis.com/auth/calendar"]
    assert cfg.profile_calendar_mapping == {}
