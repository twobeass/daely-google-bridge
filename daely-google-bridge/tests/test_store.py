"""Tests for store.py against in-memory SQLite."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from daely_google_bridge import store as store_module
from daely_google_bridge.store import LATEST_SCHEMA_VERSION, Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


# helpers for the new event_mapping signature

def _put(store, **overrides) -> None:
    """Wrapper with sensible defaults — saves test boilerplate."""
    kwargs = {
        "daely_id": "d-1",
        "daely_calendar_id": "daely-cal-1",
        "google_event_id": "g-1",
        "google_calendar_id": "cal-x@google",
    }
    kwargs.update(overrides)
    store.put_event_mapping(**kwargs)


# ────────── event_mapping ──────────

def test_event_mapping_put_and_get(store):
    _put(store)
    m = store.get_event_mapping("d-1")
    assert m is not None
    assert m.daely_id == "d-1"
    assert m.daely_calendar_id == "daely-cal-1"
    assert m.google_event_id == "g-1"
    assert m.google_calendar_id == "cal-x@google"
    assert m.last_seen_updated is None
    assert m.failed is False
    assert m.last_synced_at is not None


def test_event_mapping_records_last_seen_updated(store):
    seen = datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc)
    _put(store, last_seen_updated=seen)
    m = store.get_event_mapping("d-1")
    assert m.last_seen_updated == seen


def test_event_mapping_get_missing_returns_none(store):
    assert store.get_event_mapping("nope") is None


def test_event_mapping_idempotent_upsert(store):
    _put(store, google_event_id="g-1", google_calendar_id="cal-x")
    _put(store, google_event_id="g-2", google_calendar_id="cal-y",
         daely_calendar_id="daely-cal-2")
    m = store.get_event_mapping("d-1")
    assert m.google_event_id == "g-2"
    assert m.google_calendar_id == "cal-y"
    assert m.daely_calendar_id == "daely-cal-2"


def test_event_mapping_delete(store):
    _put(store)
    assert store.get_event_mapping("d-1") is not None
    store.delete_event_mapping("d-1")
    assert store.get_event_mapping("d-1") is None


def test_event_mapping_delete_missing_is_noop(store):
    store.delete_event_mapping("never-existed")


def test_event_mapping_all_and_by_daely_calendar(store):
    _put(store, daely_id="d1", daely_calendar_id="A", google_event_id="g1")
    _put(store, daely_id="d2", daely_calendar_id="B", google_event_id="g2")
    _put(store, daely_id="d3", daely_calendar_id="A", google_event_id="g3")

    all_rows = store.all_event_mappings()
    assert len(all_rows) == 3

    a_rows = store.event_mappings_for_daely_calendar("A")
    assert {r.daely_id for r in a_rows} == {"d1", "d3"}

    b_rows = store.event_mappings_for_daely_calendar("B")
    assert {r.daely_id for r in b_rows} == {"d2"}


def test_event_mapping_failed_flag_lifecycle(store):
    _put(store, daely_id="d1")
    assert store.get_event_mapping("d1").failed is False

    store.mark_event_failed("d1")
    assert store.get_event_mapping("d1").failed is True

    store.mark_event_failed("d1", failed=False)
    assert store.get_event_mapping("d1").failed is False


def test_event_mapping_explicit_last_synced_at_persists(store):
    ts = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    _put(store, daely_id="d1", last_synced_at=ts)
    m = store.get_event_mapping("d1")
    assert m.last_synced_at == ts


# ────────── sync_state ──────────

def test_sync_state_put_and_get(store):
    store.put_sync_state(
        calendar_id="A",
        internal_token="639xxx",
        external_token=None,
        recommended_interval_min=15,
    )
    s = store.get_sync_state("A")
    assert s.calendar_id == "A"
    assert s.internal_token == "639xxx"
    assert s.external_token is None
    assert s.recommended_interval_min == 15
    assert s.last_polled_at is None


def test_sync_state_idempotent(store):
    store.put_sync_state(calendar_id="A", internal_token="t1")
    store.put_sync_state(calendar_id="A", internal_token="t2", recommended_interval_min=30)
    s = store.get_sync_state("A")
    assert s.internal_token == "t2"
    assert s.recommended_interval_min == 30


def test_sync_state_get_missing_returns_none(store):
    assert store.get_sync_state("never") is None


def test_sync_state_last_polled_at_persists(store):
    ts = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    store.put_sync_state(calendar_id="A", last_polled_at=ts)
    s = store.get_sync_state("A")
    assert s.last_polled_at == ts


# ────────── tokens ──────────

def test_tokens_put_and_get(store):
    expires = datetime.now(timezone.utc) + timedelta(seconds=1800)
    store.put_token(
        provider="daely",
        refresh_token="rt-secret",
        access_token="at-secret",
        expires_at=expires,
    )
    t = store.get_token("daely")
    assert t.provider == "daely"
    assert t.refresh_token == "rt-secret"
    assert t.access_token == "at-secret"
    assert t.expires_at == expires


def test_tokens_idempotent_upsert(store):
    store.put_token(provider="daely", refresh_token="rt1")
    store.put_token(provider="daely", refresh_token="rt2", access_token="at2")
    t = store.get_token("daely")
    assert t.refresh_token == "rt2"
    assert t.access_token == "at2"


def test_tokens_two_providers_isolated(store):
    store.put_token(provider="daely", refresh_token="rt-d")
    store.put_token(provider="google", refresh_token="rt-g")
    assert store.get_token("daely").refresh_token == "rt-d"
    assert store.get_token("google").refresh_token == "rt-g"


def test_tokens_get_missing_returns_none(store):
    assert store.get_token("never") is None


def test_tokens_delete(store):
    store.put_token(provider="daely", refresh_token="rt")
    assert store.get_token("daely") is not None
    store.delete_token("daely")
    assert store.get_token("daely") is None


# ────────── lifecycle ──────────

def test_store_context_manager_closes(tmp_path):
    db_file = tmp_path / "bridge.db"
    with Store(db_file) as s:
        _put(s, daely_id="d1")
    assert db_file.exists()
    with Store(db_file) as s2:
        assert s2.get_event_mapping("d1") is not None


def test_store_persists_to_file(tmp_path):
    db_file = tmp_path / "bridge.db"
    s1 = Store(db_file)
    s1.put_token(provider="daely", refresh_token="rt-x")
    s1.close()
    s2 = Store(db_file)
    assert s2.get_token("daely").refresh_token == "rt-x"
    s2.close()


# ────────── schema migrations (§1.3) ──────────


def test_fresh_db_lands_at_latest_schema_version(tmp_path):
    s = Store(tmp_path / "bridge.db")
    try:
        assert s.schema_version == LATEST_SCHEMA_VERSION
        assert s.migrated_from_version == 0  # was empty before
        assert s.last_backup_path is None  # nothing to back up
    finally:
        s.close()


def test_in_memory_db_also_runs_migrations():
    s = Store(":memory:")
    try:
        assert s.schema_version == LATEST_SCHEMA_VERSION
    finally:
        s.close()


def test_schema_version_row_persisted(tmp_path):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()
    # Read the row back via raw connection
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        assert row is not None
        assert row[0] == LATEST_SCHEMA_VERSION
    finally:
        conn.close()


def test_existing_pre_framework_db_detected_as_v1_then_upgraded(tmp_path):
    """A db with the pre-framework baseline schema (no schema_version table)
    must be picked up as v1 — migration_001 is skipped (tables already exist),
    but later migrations (v2+) ARE applied to bring it up to LATEST."""
    db_file = tmp_path / "bridge.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE event_mapping (
            daely_id TEXT PRIMARY KEY,
            daely_calendar_id TEXT NOT NULL,
            google_event_id TEXT NOT NULL,
            google_calendar_id TEXT NOT NULL,
            last_seen_updated TEXT,
            last_synced_at TEXT NOT NULL,
            failed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE sync_state (
            calendar_id TEXT PRIMARY KEY,
            internal_token TEXT,
            external_token TEXT,
            recommended_interval_min INTEGER,
            last_polled_at TEXT
        );
        CREATE TABLE tokens (
            provider TEXT PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            access_token TEXT,
            expires_at TEXT
        );
        INSERT INTO tokens (provider, refresh_token) VALUES ('daely', 'pre-existing');
    """)
    conn.close()

    s = Store(db_file)
    try:
        assert s.migrated_from_version == 1  # detection only for v1
        assert s.schema_version == LATEST_SCHEMA_VERSION  # later migrations applied
        # Pre-existing data preserved
        assert s.get_token("daely").refresh_token == "pre-existing"
    finally:
        s.close()


def test_reopen_does_not_re_migrate(tmp_path):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()
    # Second open: already at latest, nothing to do
    s2 = Store(db_file)
    try:
        assert s2.schema_version == LATEST_SCHEMA_VERSION
        assert s2.migrated_from_version == LATEST_SCHEMA_VERSION
        assert s2.last_backup_path is None
    finally:
        s2.close()


def _inject_synthetic_migration(monkeypatch, fn) -> tuple[int, int]:
    """Helper: append a synthetic migration one version above the current
    LATEST_SCHEMA_VERSION, return (prior_latest, new_latest)."""
    prior_latest = store_module.LATEST_SCHEMA_VERSION
    new_latest = prior_latest + 1
    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(new_latest, fn)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", new_latest)
    return prior_latest, new_latest


def test_synthetic_migration_applied_to_existing_db(tmp_path, monkeypatch):
    """Simulate a new migration above current LATEST and verify the framework
    picks it up on re-open of an existing db at prior LATEST."""
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()  # creates db at current LATEST

    applied = {"called": False}

    def _new_migration(conn):
        applied["called"] = True
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    prior_latest, new_latest = _inject_synthetic_migration(monkeypatch, _new_migration)

    s = Store(db_file)
    try:
        assert applied["called"] is True
        assert s.schema_version == new_latest
        assert s.migrated_from_version == prior_latest
        with s._cursor() as cur:
            row = cur.execute(
                "SELECT name FROM sqlite_master WHERE name='synthetic_test_table'",
            ).fetchone()
            assert row is not None
    finally:
        s.close()


def test_synthetic_migration_writes_backup(tmp_path, monkeypatch):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()

    def _new_migration(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    prior_latest, _ = _inject_synthetic_migration(monkeypatch, _new_migration)

    s = Store(db_file)
    try:
        assert s.last_backup_path is not None
        assert s.last_backup_path.exists()
        assert s.last_backup_path.name.startswith(f"bridge.db.bak.v{prior_latest}-")
    finally:
        s.close()


def test_backup_skipped_when_disabled(tmp_path, monkeypatch):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()

    def _new_migration(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    _, new_latest = _inject_synthetic_migration(monkeypatch, _new_migration)

    s = Store(db_file, backup_on_migrate=False)
    try:
        assert s.last_backup_path is None
        assert s.schema_version == new_latest
    finally:
        s.close()


def test_backup_best_effort_swallows_oserror(tmp_path, monkeypatch):
    """If the backup write fails (read-only parent, etc.), migrations still run."""
    from pathlib import Path as _Path

    db_file = tmp_path / "bridge.db"
    Store(db_file).close()

    def _new_migration(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    _, new_latest = _inject_synthetic_migration(monkeypatch, _new_migration)

    real_write = _Path.write_bytes

    def _selective(self, data):
        if ".bak." in self.name:
            raise OSError("simulated read-only parent")
        return real_write(self, data)

    monkeypatch.setattr(_Path, "write_bytes", _selective)

    s = Store(db_file)  # must NOT raise
    try:
        assert s.schema_version == new_latest
        assert s.last_backup_path is None
    finally:
        s.close()


def test_existing_data_survives_migration(tmp_path, monkeypatch):
    """Critical: applying a new migration doesn't drop pre-existing rows."""
    db_file = tmp_path / "bridge.db"
    s = Store(db_file)
    s.put_token(provider="daely", refresh_token="must-survive")
    _put(s, daely_id="d-survives")
    s.close()

    def _new_migration(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    _, new_latest = _inject_synthetic_migration(monkeypatch, _new_migration)

    s2 = Store(db_file)
    try:
        assert s2.schema_version == new_latest
        assert s2.get_token("daely").refresh_token == "must-survive"
        assert s2.get_event_mapping("d-survives") is not None
    finally:
        s2.close()


def test_migration_list_versions_are_strictly_increasing():
    """Sanity check on the canonical migration list."""
    versions = [v for v, _ in store_module._MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[0] >= 1  # 0 reserved for "fresh db"


# ────────── retry-loop (§1.2) ──────────


def test_record_event_error_first_failure_uses_base_backoff(store):
    _put(store, daely_id="d-fail")
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    updated = store.record_event_error(
        "d-fail", error_msg="boom", base_seconds=60, max_seconds=3600, now=now,
    )
    assert updated is not None
    assert updated.failed is True
    assert updated.retry_count == 1
    assert updated.last_error == "boom"
    # First failure → base backoff (60s)
    assert updated.retry_after == now + timedelta(seconds=60)


def test_record_event_error_exponential_backoff_grows(store):
    _put(store, daely_id="d-fail")
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    # Three consecutive failures
    store.record_event_error("d-fail", error_msg="e1", base_seconds=60, now=now)
    store.record_event_error("d-fail", error_msg="e2", base_seconds=60, now=now)
    third = store.record_event_error("d-fail", error_msg="e3", base_seconds=60, now=now)
    assert third.retry_count == 3
    # 3rd failure: 60 * 2^2 = 240s
    assert third.retry_after == now + timedelta(seconds=240)
    assert third.last_error == "e3"  # latest error wins


def test_record_event_error_caps_at_max_seconds(store):
    _put(store, daely_id="d-fail")
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    # 10 failures with base=60, max=300 should cap quickly
    last = None
    for i in range(10):
        last = store.record_event_error(
            "d-fail", error_msg=f"e{i}",
            base_seconds=60, max_seconds=300, now=now,
        )
    assert last.retry_count == 10
    assert last.retry_after == now + timedelta(seconds=300)


def test_record_event_error_nonexistent_mapping_returns_none(store):
    assert store.record_event_error("nope", error_msg="x") is None


def test_record_event_error_truncates_long_messages(store):
    _put(store, daely_id="d-fail")
    long_msg = "x" * 5000
    updated = store.record_event_error("d-fail", error_msg=long_msg)
    assert updated.last_error is not None
    assert len(updated.last_error) == 1000


def test_clear_event_error_resets_all_retry_state(store):
    _put(store, daely_id="d-fail")
    store.record_event_error("d-fail", error_msg="boom")
    pre = store.get_event_mapping("d-fail")
    assert pre.failed is True

    store.clear_event_error("d-fail")
    post = store.get_event_mapping("d-fail")
    assert post.failed is False
    assert post.retry_count == 0
    assert post.retry_after is None
    assert post.last_error is None


def test_put_event_mapping_clears_failure_state_on_success(store):
    """A successful patch (default put_event_mapping kwargs) must implicitly
    clear retry state — that's how the sync engine resets after recovery."""
    _put(store, daely_id="d-fail")
    store.record_event_error("d-fail", error_msg="boom")
    assert store.get_event_mapping("d-fail").failed is True

    # Simulate a successful re-patch — caller passes only the success fields.
    _put(store, daely_id="d-fail",
         last_seen_updated=datetime(2026, 5, 8, tzinfo=timezone.utc))
    after = store.get_event_mapping("d-fail")
    assert after.failed is False
    assert after.retry_count == 0
    assert after.retry_after is None
    assert after.last_error is None


def test_events_due_for_retry_returns_only_due_failed(store):
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    # Three mappings: not failed, failed-cooldown-pending, failed-due
    _put(store, daely_id="d-ok")
    _put(store, daely_id="d-pending")
    store.record_event_error("d-pending", error_msg="x", now=now,
                             base_seconds=600)  # retry in 10min
    _put(store, daely_id="d-due")
    store.record_event_error("d-due", error_msg="y",
                             now=now - timedelta(hours=2),
                             base_seconds=60)  # was 1min ago + 1min ⇒ already due

    due = store.events_due_for_retry(now=now)
    ids = {m.daely_id for m in due}
    assert ids == {"d-due"}


def test_reset_event_sync_markers_all(store):
    seen = datetime(2026, 5, 8, tzinfo=timezone.utc)
    _put(store, daely_id="d1", last_seen_updated=seen)
    _put(store, daely_id="d2", last_seen_updated=seen)
    affected = store.reset_event_sync_markers()
    assert affected == 2
    assert store.get_event_mapping("d1").last_seen_updated is None
    assert store.get_event_mapping("d2").last_seen_updated is None


def test_reset_event_sync_markers_filtered_by_calendar(store):
    seen = datetime(2026, 5, 8, tzinfo=timezone.utc)
    _put(store, daely_id="d1", daely_calendar_id="A", last_seen_updated=seen)
    _put(store, daely_id="d2", daely_calendar_id="B", last_seen_updated=seen)
    affected = store.reset_event_sync_markers(daely_calendar_id="A")
    assert affected == 1
    assert store.get_event_mapping("d1").last_seen_updated is None
    assert store.get_event_mapping("d2").last_seen_updated is not None


# ────────── sync_history (§10.1) ──────────


def _hist_args(run_id: str = "r-1", **overrides):
    base = {
        "run_id": run_id,
        "started_at": datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 5, 8, 12, 0, 5, tzinfo=timezone.utc),
        "duration_seconds": 5.0,
        "inserts": 1, "patches": 2, "deletes": 0, "no_ops": 3,
        "skipped_external": 0, "skipped_no_target": 0,
        "errors": [],
    }
    base.update(overrides)
    return base


def test_record_and_recent_sync_history(store):
    store.record_sync_history(**_hist_args(run_id="r-1"))
    store.record_sync_history(**_hist_args(
        run_id="r-2",
        started_at=datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 5, 8, 13, 0, 2, tzinfo=timezone.utc),
        inserts=10,
    ))
    recent = store.recent_sync_history()
    # Newest first
    assert [h.run_id for h in recent] == ["r-2", "r-1"]
    assert recent[0].inserts == 10


def test_record_sync_history_persists_errors_as_json(store):
    errors = [("d-x", "boom-x"), ("d-y", "boom-y")]
    store.record_sync_history(**_hist_args(run_id="r-err", errors=errors))
    recent = store.recent_sync_history()
    assert len(recent) == 1
    assert recent[0].errors_count == 2
    assert recent[0].errors == errors


def test_record_sync_history_caps_errors_at_100(store):
    big = [(f"d-{i}", f"err-{i}") for i in range(250)]
    store.record_sync_history(**_hist_args(run_id="r-big", errors=big))
    recent = store.recent_sync_history()
    assert recent[0].errors_count == 250  # full count preserved
    assert len(recent[0].errors) == 100   # but only first 100 details kept


def test_record_sync_history_idempotent_on_run_id(store):
    store.record_sync_history(**_hist_args(run_id="r-dup", inserts=1))
    store.record_sync_history(**_hist_args(run_id="r-dup", inserts=99))
    recent = store.recent_sync_history()
    assert len(recent) == 1
    assert recent[0].inserts == 99


def test_prune_sync_history_keeps_last_n(store):
    for i in range(10):
        store.record_sync_history(**_hist_args(
            run_id=f"r-{i}",
            started_at=datetime(2026, 5, 8, 10, i, tzinfo=timezone.utc),
            completed_at=datetime(2026, 5, 8, 10, i, 1, tzinfo=timezone.utc),
        ))
    deleted = store.prune_sync_history(keep_last=3)
    assert deleted == 7
    remaining = store.recent_sync_history(limit=20)
    assert len(remaining) == 3
    # The three newest survive
    assert [h.run_id for h in remaining] == ["r-9", "r-8", "r-7"]


def test_prune_sync_history_no_op_when_under_limit(store):
    for i in range(3):
        store.record_sync_history(**_hist_args(run_id=f"r-{i}"))
    assert store.prune_sync_history(keep_last=10) == 0


def test_recent_sync_history_limit_zero_returns_empty(store):
    store.record_sync_history(**_hist_args())
    assert store.recent_sync_history(limit=0) == []
