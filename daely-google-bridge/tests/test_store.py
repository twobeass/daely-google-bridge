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


def test_existing_pre_framework_db_detected_as_v1(tmp_path):
    """A db with the pre-migration baseline schema (no schema_version table)
    must be picked up as v1 without re-running migration_001 (which would
    fail because the tables already exist)."""
    db_file = tmp_path / "bridge.db"
    # Hand-craft a pre-framework db: just the original baseline schema, no
    # schema_version table.
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

    # Open with Store — should detect v1 and not re-run migration_001
    s = Store(db_file)
    try:
        assert s.schema_version == 1
        assert s.migrated_from_version == 1  # detection only, no actual migration
        assert s.last_backup_path is None  # no backup needed when nothing changed
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


def test_synthetic_v2_migration_applied_to_v1_db(tmp_path, monkeypatch):
    """Simulate a future v2 migration to verify the framework picks it up
    on re-open of an existing v1 db."""
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()  # creates v1

    applied = {"called": False}

    def _migration_002_test(conn):
        applied["called"] = True
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(2, _migration_002_test)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", 2)

    s = Store(db_file)
    try:
        assert applied["called"] is True
        assert s.schema_version == 2
        assert s.migrated_from_version == 1
        # New table really exists
        with s._cursor() as cur:
            row = cur.execute(
                "SELECT name FROM sqlite_master WHERE name='synthetic_test_table'",
            ).fetchone()
            assert row is not None
    finally:
        s.close()


def test_synthetic_migration_writes_backup(tmp_path, monkeypatch):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()  # creates v1

    def _migration_002_test(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(2, _migration_002_test)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", 2)

    s = Store(db_file)
    try:
        assert s.last_backup_path is not None
        assert s.last_backup_path.exists()
        assert s.last_backup_path.name.startswith("bridge.db.bak.v1-")
    finally:
        s.close()


def test_backup_skipped_when_disabled(tmp_path, monkeypatch):
    db_file = tmp_path / "bridge.db"
    Store(db_file).close()  # creates v1

    def _migration_002_test(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(2, _migration_002_test)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", 2)

    s = Store(db_file, backup_on_migrate=False)
    try:
        assert s.last_backup_path is None
        # But the migration still ran
        assert s.schema_version == 2
    finally:
        s.close()


def test_backup_best_effort_swallows_oserror(tmp_path, monkeypatch):
    """If the backup write fails (read-only parent, etc.), migrations still run."""
    from pathlib import Path as _Path

    db_file = tmp_path / "bridge.db"
    Store(db_file).close()  # creates v1

    def _migration_002_test(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(2, _migration_002_test)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", 2)

    real_write = _Path.write_bytes

    def _selective(self, data):
        if ".bak." in self.name:
            raise OSError("simulated read-only parent")
        return real_write(self, data)

    monkeypatch.setattr(_Path, "write_bytes", _selective)

    s = Store(db_file)  # must NOT raise
    try:
        assert s.schema_version == 2
        assert s.last_backup_path is None
    finally:
        s.close()


def test_existing_data_survives_migration(tmp_path, monkeypatch):
    """Critical: writing a v2 migration doesn't lose v1 data."""
    db_file = tmp_path / "bridge.db"
    s = Store(db_file)
    s.put_token(provider="daely", refresh_token="must-survive")
    _put(s, daely_id="d-survives")
    s.close()

    def _migration_002_test(conn):
        conn.execute("CREATE TABLE synthetic_test_table (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        store_module, "_MIGRATIONS",
        store_module._MIGRATIONS + [(2, _migration_002_test)],
    )
    monkeypatch.setattr(store_module, "LATEST_SCHEMA_VERSION", 2)

    s2 = Store(db_file)
    try:
        assert s2.schema_version == 2
        assert s2.get_token("daely").refresh_token == "must-survive"
        assert s2.get_event_mapping("d-survives") is not None
    finally:
        s2.close()


def test_migration_list_versions_are_strictly_increasing():
    """Sanity check on the canonical migration list."""
    versions = [v for v, _ in store_module._MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[0] >= 1  # 0 reserved for "fresh db"
