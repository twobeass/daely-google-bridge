"""CLI tests — bootstrap dry-run with full mocks; status; argparse routing."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from daely_google_bridge.cli import (
    cmd_bootstrap,
    cmd_resync,
    cmd_run,
    cmd_status,
    main,
)
from daely_google_bridge.config import BridgeConfig, save_config
from daely_google_bridge.store import Store


FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures_anonymized"


def _fix(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


# ────────── argparse routing ──────────

def test_main_dispatches_status(tmp_path, capsys):
    """`bridge status` should not crash when no config exists; should print a hint."""
    bogus_cfg = tmp_path / "nope.yaml"
    rc = main(["-c", str(bogus_cfg), "status"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "bootstrap" in err.lower()


def test_run_without_config_returns_1(tmp_path, capsys):
    args = MagicMock()
    args.config = str(tmp_path / "missing.yaml")
    args.once = True
    rc = cmd_run(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "bootstrap" in err.lower()


def test_run_once_dispatches_full_sync(tmp_path, capsys):
    """`bridge run --once` performs one full_sync and exits."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = True

    from daely_google_bridge.sync import SyncReport
    fake_report = SyncReport(inserts=2, patches=1, no_ops=3)

    full_sync_fn = MagicMock(return_value=fake_report)
    incremental_sync_fn = MagicMock()

    rc = cmd_run(
        args,
        daely_factory=lambda s, c: MagicMock(close=MagicMock()),
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=full_sync_fn,
        incremental_sync_fn=incremental_sync_fn,
    )
    assert rc == 0
    full_sync_fn.assert_called_once()
    incremental_sync_fn.assert_not_called()
    out = capsys.readouterr().out
    assert "inserts:        2" in out
    assert "patches:        1" in out


def test_run_default_starts_scheduler_with_incremental(tmp_path):
    """Without --once, a scheduler is started and configured for incremental_sync."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        poll_interval_minutes=7,
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = False

    fake_scheduler = MagicMock()
    # start() must return immediately so the test doesn't block.
    fake_scheduler.start.return_value = None

    from daely_google_bridge.sync import SyncReport

    rc = cmd_run(
        args,
        daely_factory=lambda s, c: MagicMock(close=MagicMock()),
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: fake_scheduler,
    )
    assert rc == 0
    # add_job called with interval=7m
    fake_scheduler.add_job.assert_called_once()
    call = fake_scheduler.add_job.call_args
    assert call.args[1] == "interval"
    assert call.kwargs["minutes"] == 7
    fake_scheduler.start.assert_called_once()


def test_run_no_daely_token_returns_1(tmp_path, capsys):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = True

    def daely_factory_raising(_s, _c):
        raise RuntimeError("no Daely refresh-token in store. Run `bridge bootstrap` first.")

    rc = cmd_run(
        args,
        daely_factory=daely_factory_raising,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(),
        incremental_sync_fn=MagicMock(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "bootstrap" in err.lower()


def _resync_fixture(tmp_path):
    """Build a config + Store with sample mappings; return (config_path, db_path)."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)

    from datetime import datetime, timezone
    s = Store(cfg.db_path)
    seen = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    s.put_event_mapping(
        daely_id="d1", daely_calendar_id="cal-A",
        google_event_id="g1", google_calendar_id="gcal-A",
        last_seen_updated=seen,
    )
    s.put_event_mapping(
        daely_id="d2", daely_calendar_id="cal-A",
        google_event_id="g2", google_calendar_id="gcal-A",
        last_seen_updated=seen,
    )
    s.put_event_mapping(
        daely_id="d3", daely_calendar_id="cal-B",
        google_event_id="g3", google_calendar_id="gcal-B",
        last_seen_updated=seen,
    )
    s.close()
    return config_path, cfg.db_path


def test_resync_without_config_returns_1(tmp_path, capsys):
    args = MagicMock()
    args.config = str(tmp_path / "missing.yaml")
    args.calendar = None
    args.dry_run = False
    rc = cmd_resync(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "bootstrap" in err.lower()


def test_resync_all_calendars_resets_every_mapping(tmp_path, capsys):
    config_path, db_path = _resync_fixture(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.calendar = None
    args.dry_run = False

    rc = cmd_resync(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "reset 3 mapping" in out

    s = Store(db_path)
    try:
        for daely_id in ("d1", "d2", "d3"):
            assert s.get_event_mapping(daely_id).last_seen_updated is None
    finally:
        s.close()


def test_resync_filtered_by_calendar_only_resets_that_one(tmp_path, capsys):
    config_path, db_path = _resync_fixture(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.calendar = "cal-A"
    args.dry_run = False

    rc = cmd_resync(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "reset 2 mapping" in out
    assert "cal-A" in out

    s = Store(db_path)
    try:
        # cal-A rows reset
        assert s.get_event_mapping("d1").last_seen_updated is None
        assert s.get_event_mapping("d2").last_seen_updated is None
        # cal-B row untouched
        assert s.get_event_mapping("d3").last_seen_updated is not None
    finally:
        s.close()


def test_resync_dry_run_does_not_modify_db(tmp_path, capsys):
    config_path, db_path = _resync_fixture(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.calendar = None
    args.dry_run = True

    rc = cmd_resync(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "would reset 3" in out

    s = Store(db_path)
    try:
        # All last_seen_updated still set
        for daely_id in ("d1", "d2", "d3"):
            assert s.get_event_mapping(daely_id).last_seen_updated is not None
    finally:
        s.close()


def test_recolor_command_resets_all_mappings(tmp_path, capsys):
    """re-color is a thin alias for resync over all calendars."""
    from daely_google_bridge.cli import cmd_recolor

    config_path, db_path = _resync_fixture(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.dry_run = False
    # Note: cmd_recolor sets args.calendar = None internally

    rc = cmd_recolor(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "reset 3 mapping" in out

    s = Store(db_path)
    try:
        for daely_id in ("d1", "d2", "d3"):
            assert s.get_event_mapping(daely_id).last_seen_updated is None
    finally:
        s.close()


def test_main_dispatches_resync(tmp_path, capsys):
    """`bridge resync --dry-run` via main() — verify argparse wiring."""
    config_path, _ = _resync_fixture(tmp_path)
    rc = main(["-c", str(config_path), "resync", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_dispatches_recolor(tmp_path, capsys):
    """`bridge re-color --dry-run` via main()."""
    config_path, _ = _resync_fixture(tmp_path)
    rc = main(["-c", str(config_path), "re-color", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out


# ────────── bootstrap dry-run ──────────

@pytest.fixture()
def bootstrap_setup(tmp_path):
    """Builds a config, secrets file, and the kwargs needed by cmd_bootstrap."""
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {
        "client_id": "cid", "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}))
    cfg = BridgeConfig(
        daely_email="user1@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    return args, config_path, secrets


def test_bootstrap_full_dry_run(bootstrap_setup, monkeypatch):
    """End-to-end bootstrap with all collaborators mocked.

    Exercises:
    - Daely login (via injected factory)
    - Daely group + calendars fetch
    - Google authorize (via injected callable)
    - Sub-calendar reconciliation (one create, one fallback create)
    - Config write-back with backup
    """
    args, config_path, secrets_path = bootstrap_setup

    # Mock Daely client
    daely = MagicMock()
    daely.access_token = "AT"
    daely.refresh_token = "RT"
    daely.min_pause_seconds = 1.0
    # get_my_groups → 1 group
    group = MagicMock(id="grp-1", name="Test Family", setupComplete=True)
    daely.get_my_groups.return_value = [group]
    # get_calendars → 3 cals (1 internal with profile, 1 internal w/o profile (fallback),
    # 1 external Google calendar — must be ignored)
    cal_internal_with_profile = MagicMock(
        id="cal-int-1", title="Profile A", calendarType=0,
        profileId="prof-A", timeZone="Europe/Berlin",
    )
    cal_internal_no_profile = MagicMock(
        id="cal-int-2", title="Family Shared", calendarType=0,
        profileId=None, timeZone="Europe/Berlin",
    )
    cal_external = MagicMock(
        id="cal-ext", title="External Google", calendarType=1,
        profileId=None, timeZone="UTC",
    )
    daely.get_calendars.return_value = [
        cal_internal_with_profile, cal_internal_no_profile, cal_external,
    ]

    daely_factory = MagicMock(return_value=daely)

    # Mock google authorize
    google_creds = MagicMock()
    google_creds.refresh_token = "google-rt"
    google_creds.token = "google-at"
    google_creds.expiry = None
    google_authorize = MagicMock(return_value=google_creds)

    # Mock GoogleClient instance + factory
    google_client = MagicMock()
    google_client.list_calendars.return_value = []  # nothing pre-existing
    google_client.create_calendar.side_effect = lambda summary, **kw: {
        "id": f"new-{summary.lower().replace(' ', '-')}@google",
        "summary": summary,
    }
    google_factory = MagicMock(return_value=google_client)

    rc = cmd_bootstrap(
        args,
        daely_factory=daely_factory,
        google_authorize=google_authorize,
        google_factory=google_factory,
        input_fn=lambda _: "user1@example.com",
        getpass_fn=lambda _: "password-from-test",
    )
    assert rc == 0

    # Daely login was called with config email (not stubbed input, since email is set)
    daely.login_password.assert_called_once_with("user1@example.com", "password-from-test")

    # Google authorize invoked with secrets path AND the port from config (default 8080)
    google_authorize.assert_called_once()
    auth_kwargs = google_authorize.call_args.kwargs
    assert auth_kwargs.get("port") == 8080

    # Two calendars created: one for prof-A, one fallback (Daely – Family)
    create_calls = google_client.create_calendar.call_args_list
    summaries = [c.kwargs.get("summary") or c.args[0] for c in create_calls]
    assert "Daely – Profile A" in summaries
    assert "Daely – Family" in summaries

    # Config was rewritten with the new mapping
    written = yaml.safe_load(config_path.read_text())
    assert "prof-A" in written["profile_calendar_mapping"]
    assert written["fallback_google_calendar_id"] is not None

    # Backup created
    assert config_path.with_suffix(".yaml.bak").exists()


def test_bootstrap_login_failure_returns_2(bootstrap_setup):
    args, _, _ = bootstrap_setup
    daely = MagicMock()
    from daely_google_bridge.daely_client import DaelyAuthError
    daely.login_password.side_effect = DaelyAuthError("bad password")
    daely_factory = MagicMock(return_value=daely)
    rc = cmd_bootstrap(
        args,
        daely_factory=daely_factory,
        google_authorize=MagicMock(),
        google_factory=MagicMock(),
        input_fn=lambda _: "x@example.com",
        getpass_fn=lambda _: "pw",
    )
    assert rc == 2


def test_bootstrap_no_groups_returns_3(bootstrap_setup):
    args, _, _ = bootstrap_setup
    daely = MagicMock()
    daely.access_token = "AT"
    daely.refresh_token = "RT"
    daely.get_my_groups.return_value = []
    daely_factory = MagicMock(return_value=daely)
    rc = cmd_bootstrap(
        args,
        daely_factory=daely_factory,
        google_authorize=MagicMock(),
        google_factory=MagicMock(),
        input_fn=lambda _: "x",
        getpass_fn=lambda _: "y",
    )
    assert rc == 3


def test_bootstrap_missing_secrets_returns_4(tmp_path, bootstrap_setup):
    args, _, secrets_path = bootstrap_setup
    secrets_path.unlink()
    daely = MagicMock()
    daely.access_token = "AT"
    daely.refresh_token = "RT"
    daely.get_my_groups.return_value = [MagicMock(id="g", name="G")]
    daely.get_calendars.return_value = []
    daely_factory = MagicMock(return_value=daely)
    rc = cmd_bootstrap(
        args,
        daely_factory=daely_factory,
        google_authorize=MagicMock(),
        google_factory=MagicMock(),
        input_fn=lambda _: "x",
        getpass_fn=lambda _: "y",
    )
    assert rc == 4


def test_bootstrap_reuses_existing_google_calendars(bootstrap_setup):
    """If Google already has 'Daely – Profile A', use it instead of creating."""
    args, _, _ = bootstrap_setup

    daely = MagicMock()
    daely.access_token = "AT"
    daely.refresh_token = "RT"
    daely.get_my_groups.return_value = [MagicMock(id="g", name="G", setupComplete=True)]
    daely.get_calendars.return_value = [
        MagicMock(id="c", title="Profile A", calendarType=0,
                  profileId="prof-A", timeZone="Europe/Berlin"),
    ]

    google_client = MagicMock()
    google_client.list_calendars.return_value = [
        {"id": "existing-prof-a@google", "summary": "Daely – Profile A"},
    ]
    google_client.create_calendar.side_effect = lambda summary, **kw: {
        "id": f"new-{summary.lower().replace(' ', '-')}@google",
        "summary": summary,
    }
    rc = cmd_bootstrap(
        args,
        daely_factory=MagicMock(return_value=daely),
        google_authorize=MagicMock(return_value=MagicMock(refresh_token="rt", token="at", expiry=None)),
        google_factory=MagicMock(return_value=google_client),
        input_fn=lambda _: "x",
        getpass_fn=lambda _: "y",
    )
    assert rc == 0
    # Should NOT have created prof-A — it already exists
    create_summaries = [
        c.kwargs.get("summary") or c.args[0]
        for c in google_client.create_calendar.call_args_list
    ]
    assert "Daely – Profile A" not in create_summaries
    # Fallback may still be created since none was pre-existing
    assert "Daely – Family" in create_summaries


# ────────── status command ──────────

def test_status_with_partial_state(tmp_path, capsys):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="x@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)

    # Pre-populate the store
    store = Store(cfg.db_path)
    store.put_token(provider="daely", refresh_token="rt-d")
    store.put_event_mapping(
        daely_id="d1",
        daely_calendar_id="daely-cal-1",
        google_event_id="g1",
        google_calendar_id="cal-x",
    )
    store.close()

    args = MagicMock()
    args.config = str(config_path)
    rc = cmd_status(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "daely refresh-token:   set" in out
    assert "google refresh-token:  MISSING" in out
    assert "event mappings:        1" in out
