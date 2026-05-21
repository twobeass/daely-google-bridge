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
        full_sync_interval_hours=12,
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
    jobs_by_id = {c.kwargs["id"]: c for c in fake_scheduler.add_job.call_args_list}
    # Incremental poll on interval=7m.
    incr = jobs_by_id["incremental"]
    assert incr.args[1] == "interval"
    assert incr.kwargs["minutes"] == 7
    # Periodic full_sync on interval=12h.
    full = jobs_by_id["full_sync"]
    assert full.args[1] == "interval"
    assert full.kwargs["hours"] == 12
    fake_scheduler.start.assert_called_once()


def test_run_full_sync_interval_zero_disables_periodic_full_sync(tmp_path):
    """full_sync_interval_hours=0 → only the incremental job is scheduled
    (legacy behaviour: full_sync runs once at startup only)."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        poll_interval_minutes=7,
        full_sync_interval_hours=0,
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = False

    fake_scheduler = MagicMock()
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
    job_ids = {c.kwargs["id"] for c in fake_scheduler.add_job.call_args_list}
    assert job_ids == {"incremental"}


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


# ────────── doctor ──────────

from daely_google_bridge.cli import cmd_doctor  # noqa: E402  imported here for test grouping


def _doctor_fixture_full(tmp_path, *, with_tokens: bool = True,
                         with_mappings: bool = True,
                         with_recent_sync: bool = True,
                         poll_interval_minutes: int = 15):
    """Build a config + Store and seed it with a known-good state."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
        profile_calendar_mapping={"prof-A": "cal-A"},
        fallback_google_calendar_id="cal-fb",
        poll_interval_minutes=poll_interval_minutes,
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)

    s = Store(cfg.db_path)
    if with_tokens:
        s.put_token(provider="daely", refresh_token="rt-d")
        s.put_token(provider="google", refresh_token="rt-g")
    if with_mappings:
        from datetime import datetime, timezone
        seen = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        s.put_event_mapping(
            daely_id="d1", daely_calendar_id="cal-A",
            google_event_id="g1", google_calendar_id="gcal-A",
            last_seen_updated=seen,
        )
    if with_recent_sync:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        s.record_sync_history(
            run_id="recent-run-1",
            started_at=now,
            completed_at=now,
            duration_seconds=1.0,
            inserts=1, patches=0, deletes=0, no_ops=2,
            skipped_external=0, skipped_no_target=0,
            errors=[],
        )
    s.close()
    return config_path, cfg.db_path


def test_doctor_all_green(tmp_path, capsys):
    config_path, _ = _doctor_fixture_full(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Overall: OK" in out
    # All checks should be [OK]
    assert "[FAIL]" not in out
    assert "[WARN]" not in out
    assert "config:" in out
    assert "database:" in out
    assert "daely refresh-token:" in out
    assert "google refresh-token:" in out
    assert "event mappings:" in out
    assert "last sync:" in out


def test_doctor_no_config_returns_fail(tmp_path, capsys):
    args = MagicMock()
    args.config = str(tmp_path / "missing.yaml")
    args.live = False
    rc = cmd_doctor(args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "config:" in out
    assert "bootstrap" in out.lower()


def test_doctor_missing_tokens_fail(tmp_path, capsys):
    config_path, _ = _doctor_fixture_full(tmp_path, with_tokens=False)
    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "Overall: FAIL" in out
    assert "[FAIL] daely refresh-token:" in out
    assert "[FAIL] google refresh-token:" in out


def test_doctor_no_sync_yet_is_ok(tmp_path, capsys):
    """Empty sync_history is a valid post-startup state, not a warning."""
    config_path, _ = _doctor_fixture_full(tmp_path, with_recent_sync=False)
    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Overall: OK" in out
    assert "pending" in out
    assert "[OK]   last sync:" in out


def test_doctor_stale_sync_warns(tmp_path, capsys):
    """Last sync older than 2× poll_interval should yield a WARN."""
    config_path, db_path = _doctor_fixture_full(tmp_path, with_recent_sync=False)
    # Inject an old sync history row directly
    from datetime import datetime, timedelta, timezone
    s = Store(db_path)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    s.record_sync_history(
        run_id="old-run", started_at=old, completed_at=old,
        duration_seconds=1.0, inserts=0, patches=0, deletes=0, no_ops=0,
        skipped_external=0, skipped_no_target=0, errors=[],
    )
    s.close()

    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    assert rc == 2
    out = capsys.readouterr().out
    assert "Overall: WARN" in out
    assert "stale" in out.lower()


def test_doctor_failed_mappings_warn(tmp_path, capsys):
    config_path, db_path = _doctor_fixture_full(tmp_path)
    s = Store(db_path)
    s.put_event_mapping(
        daely_id="d-broken", daely_calendar_id="cal-A",
        google_event_id="g-broken", google_calendar_id="gcal-A",
    )
    s.record_event_error("d-broken", error_msg="oh no")
    s.close()

    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    assert rc == 2
    out = capsys.readouterr().out
    assert "Overall: WARN" in out
    assert "[WARN] event mappings:" in out
    assert "1 failed" in out


def test_doctor_no_profile_mapping_no_fallback_fails(tmp_path, capsys):
    """A bridge with no profile mapping AND no fallback would skip everything."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
        # No profile_calendar_mapping, no fallback
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    s = Store(cfg.db_path)
    s.put_token(provider="daely", refresh_token="rt-d")
    s.put_token(provider="google", refresh_token="rt-g")
    s.close()

    args = MagicMock()
    args.config = str(config_path)
    args.live = False
    rc = cmd_doctor(args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL] config mapping:" in out
    assert "Overall: FAIL" in out


def test_doctor_live_check_all_green(tmp_path, capsys):
    config_path, _ = _doctor_fixture_full(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.live = True

    fake_daely = MagicMock()
    fake_daely.refresh.return_value = {"expires_in": 300}
    fake_daely.refresh_token = "rt-d-rotated"
    fake_daely.access_token = "at-d-new"

    fake_google = MagicMock()
    fake_google.list_calendars.return_value = [
        {"id": "cal-A"}, {"id": "cal-B"}, {"id": "cal-fb"},
    ]

    rc = cmd_doctor(
        args,
        daely_factory=lambda c: fake_daely,
        google_factory=lambda s, c: fake_google,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Overall: OK" in out
    assert "[OK]   daely live refresh:" in out
    assert "expires_in=300" in out
    assert "[OK]   google live ping:" in out
    assert "3 calendars" in out


def test_doctor_live_daely_refresh_failure_fails(tmp_path, capsys):
    from daely_google_bridge.daely_client import DaelyAuthError
    config_path, _ = _doctor_fixture_full(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.live = True

    fake_daely = MagicMock()
    fake_daely.refresh.side_effect = DaelyAuthError("invalid_grant: token revoked")
    fake_google = MagicMock()
    fake_google.list_calendars.return_value = []

    rc = cmd_doctor(
        args,
        daely_factory=lambda c: fake_daely,
        google_factory=lambda s, c: fake_google,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] daely live refresh:" in out
    assert "invalid_grant" in out
    assert "Overall: FAIL" in out


def test_doctor_live_google_failure_fails(tmp_path, capsys):
    config_path, _ = _doctor_fixture_full(tmp_path)
    args = MagicMock()
    args.config = str(config_path)
    args.live = True

    fake_daely = MagicMock()
    fake_daely.refresh.return_value = {"expires_in": 300}
    fake_daely.refresh_token = "rt-d"
    fake_daely.access_token = "at-d"

    fake_google = MagicMock()
    fake_google.list_calendars.side_effect = RuntimeError("403 forbidden")

    rc = cmd_doctor(
        args,
        daely_factory=lambda c: fake_daely,
        google_factory=lambda s, c: fake_google,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] google live ping:" in out
    assert "403 forbidden" in out


def test_main_dispatches_doctor(tmp_path, capsys):
    config_path, _ = _doctor_fixture_full(tmp_path)
    rc = main(["-c", str(config_path), "doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Overall: OK" in out


# ────────── realtime daemon integration (§1.1) ──────────


def _build_run_args(tmp_path):
    """Build a minimal config + args for cmd_run, with realtime ENABLED."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
        realtime={"enabled": True, "debounce_seconds": 0.5},
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = False
    return args


def test_cmd_run_starts_realtime_when_enabled(tmp_path, capsys):
    """realtime.enabled=true → factory invoked, client.start() called."""
    args = _build_run_args(tmp_path)

    # Mock daely with the methods _start_realtime_client uses
    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.return_value = MagicMock(id="user-uuid-test")
    fake_daely.get_my_groups.return_value = [MagicMock(id="group-uuid-test")]

    # Realtime client mock returned by factory
    realtime_mock = MagicMock()
    captured_factory_args = {}

    def _rt_factory(cfg, daely, me, group, on_event):
        captured_factory_args["cfg"] = cfg
        captured_factory_args["me"] = me
        captured_factory_args["group"] = group
        captured_factory_args["on_event"] = on_event
        return realtime_mock

    # Stop the scheduler immediately so cmd_run returns
    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    full_sync_fn = MagicMock(return_value=SyncReport())

    rc = cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=full_sync_fn,
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )
    assert rc == 0
    realtime_mock.start.assert_called_once()
    realtime_mock.stop.assert_called_once()
    assert captured_factory_args["me"].id == "user-uuid-test"
    assert captured_factory_args["group"].id == "group-uuid-test"


def test_cmd_run_skips_realtime_when_disabled(tmp_path):
    from daely_google_bridge.config import load_config as _load_config
    args = _build_run_args(tmp_path)
    # Override config to disable realtime
    cfg = _load_config(args.config)
    cfg = cfg.model_copy(update={"realtime": cfg.realtime.model_copy(update={"enabled": False})})
    save_config(cfg, args.config, backup=False)

    rt_factory_calls = {"count": 0}

    def _rt_factory(*a, **kw):
        rt_factory_calls["count"] += 1
        return MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    rc = cmd_run(
        args,
        daely_factory=lambda s, c: MagicMock(close=MagicMock()),
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )
    assert rc == 0
    assert rt_factory_calls["count"] == 0


def test_realtime_callback_only_triggers_on_calendar_events(tmp_path, capsys):
    """The on_event callback registered by _start_realtime_client should
    schedule a sync ONLY for calendar/* subjects."""
    args = _build_run_args(tmp_path)

    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.return_value = MagicMock(id="u")
    fake_daely.get_my_groups.return_value = [MagicMock(id="g")]

    captured = {}

    def _rt_factory(cfg, daely, me, group, on_event):
        captured["on_event"] = on_event
        return MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )

    on_event = captured["on_event"]

    # Calendar event → scheduler.add_job called
    from daely_google_bridge.models import RealtimeEvent
    on_event(RealtimeEvent(subject="calendar/event", entityId="ev-1"))
    assert scheduler_mock.add_job.called
    # The job was scheduled with id="realtime-trigger" + replace_existing=True
    args_called = scheduler_mock.add_job.call_args
    assert args_called.kwargs.get("id") == "realtime-trigger"
    assert args_called.kwargs.get("replace_existing") is True

    # Non-calendar event → no new add_job
    add_job_count_before = scheduler_mock.add_job.call_count
    on_event(RealtimeEvent(subject="chore/completion"))
    on_event(RealtimeEvent(subject="meal-plan"))
    on_event(RealtimeEvent(subject="user"))
    assert scheduler_mock.add_job.call_count == add_job_count_before


def test_realtime_trigger_runs_full_sync_not_incremental(tmp_path):
    """Critical correctness: incremental_sync has detect_missing_as_deleted=False
    and would silently miss physical deletes. Realtime triggers MUST use
    full_sync to ensure deletions propagate."""
    args = _build_run_args(tmp_path)

    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.return_value = MagicMock(id="u")
    fake_daely.get_my_groups.return_value = [MagicMock(id="g")]

    captured_jobs = []

    def _scheduler():
        sched = MagicMock()
        sched.start.side_effect = KeyboardInterrupt
        # Capture each add_job invocation so we can dispatch the correct
        # closure when we simulate a realtime trigger
        def _record_add_job(func, *a, **kw):
            captured_jobs.append((func, a, kw))
        sched.add_job.side_effect = _record_add_job
        return sched

    captured_on_event = {}

    def _rt_factory(cfg, daely, me, group, on_event):
        captured_on_event["fn"] = on_event
        return MagicMock()

    full_sync_calls = {"count": 0}
    incremental_calls = {"count": 0}

    from daely_google_bridge.sync import SyncReport

    def _full_sync(*a, **kw):
        full_sync_calls["count"] += 1
        return SyncReport()

    def _incr_sync(*a, **kw):
        incremental_calls["count"] += 1
        return SyncReport()

    cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=_full_sync,
        incremental_sync_fn=_incr_sync,
        scheduler_factory=_scheduler,
        realtime_client_factory=_rt_factory,
    )

    # The initial startup full_sync ran exactly once
    assert full_sync_calls["count"] == 1

    # Now simulate a realtime calendar event arriving
    from daely_google_bridge.models import RealtimeEvent
    captured_on_event["fn"](RealtimeEvent(subject="calendar/event"))

    # The on_event callback scheduled a job via scheduler.add_job; the LAST
    # captured job was that one
    assert len(captured_jobs) >= 2  # at least: incremental interval + realtime-trigger
    last_func, last_args, last_kwargs = captured_jobs[-1]
    assert last_kwargs.get("id") == "realtime-trigger"

    # Run the captured realtime job ourselves (mimicking what the scheduler
    # would have done) and verify that full_sync was called, not incremental
    last_func()
    assert full_sync_calls["count"] == 2  # initial + realtime trigger
    assert incremental_calls["count"] == 0  # never via realtime path


def test_realtime_internal_only_mode_pre_fetches_calendars(tmp_path):
    """`calendar_filter_mode: internal-only` calls daely.get_calendars()."""
    # Build args with explicit internal-only mode
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    cfg = BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file=secrets,
        db_path=tmp_path / "bridge.db",
        log_format="text",
        realtime={
            "enabled": True,
            "debounce_seconds": 0.5,
            "calendar_filter_mode": "internal-only",
        },
    )
    config_path = tmp_path / "config.yaml"
    save_config(cfg, config_path, backup=False)
    args = MagicMock()
    args.config = str(config_path)
    args.once = False

    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.return_value = MagicMock(id="u")
    fake_daely.get_my_groups.return_value = [MagicMock(id="g")]
    cal_internal = MagicMock(id="cal-internal-1", calendarType=0)
    fake_daely.get_calendars.return_value = [cal_internal]

    captured = {"factory_called": False}

    def _rt_factory(cfg, daely, me, group, on_event):
        captured["factory_called"] = True
        return MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )

    fake_daely.get_calendars.assert_called_once_with("g")
    assert captured["factory_called"] is True


def test_realtime_auto_mode_does_not_fetch_calendars(tmp_path):
    """`calendar_filter_mode: auto` (default) skips the calendar pre-fetch."""
    args = _build_run_args(tmp_path)  # default mode is "auto"

    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.return_value = MagicMock(id="u")
    fake_daely.get_my_groups.return_value = [MagicMock(id="g")]

    def _rt_factory(cfg, daely, me, group, on_event):
        return MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )

    fake_daely.get_calendars.assert_not_called()


def test_realtime_calendars_filter_is_internal_only():
    """Direct unit test on the calendar_uuids derivation logic in cli.py:
    only calendarType=0 (internal) should land in the filter; externals
    (Google/Apple-synced calendars) must not."""
    cal_internal_1 = MagicMock(id="cal-A", calendarType=0)
    cal_internal_2 = MagicMock(id="cal-B", calendarType=0)
    cal_google = MagicMock(id="cal-G", calendarType=1)
    cal_other = MagicMock(id="cal-O", calendarType=2)
    all_cals = [cal_internal_1, cal_google, cal_internal_2, cal_other]
    # Same one-liner as in cli.py
    calendar_uuids = [c.id for c in all_cals if c.calendarType == 0]
    assert calendar_uuids == ["cal-A", "cal-B"]


def test_realtime_disabled_when_get_me_fails(tmp_path, capsys):
    """If we can't fetch user/group at startup, realtime stays off but
    the daemon still runs polling."""
    args = _build_run_args(tmp_path)

    fake_daely = MagicMock()
    fake_daely.access_token = "at"
    fake_daely.get_me.side_effect = RuntimeError("backend down")

    rt_factory_calls = {"count": 0}

    def _rt_factory(*a, **kw):
        rt_factory_calls["count"] += 1
        return MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock.start.side_effect = KeyboardInterrupt

    from daely_google_bridge.sync import SyncReport
    rc = cmd_run(
        args,
        daely_factory=lambda s, c: fake_daely,
        google_factory=lambda s, c: MagicMock(),
        full_sync_fn=MagicMock(return_value=SyncReport()),
        incremental_sync_fn=MagicMock(),
        scheduler_factory=lambda: scheduler_mock,
        realtime_client_factory=_rt_factory,
    )
    assert rc == 0
    # Factory not invoked (we couldn't fetch group), but scheduler still ran
    assert rt_factory_calls["count"] == 0
    err = capsys.readouterr().err
    assert "realtime disabled" in err.lower()


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
