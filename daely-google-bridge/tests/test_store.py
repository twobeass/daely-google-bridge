"""Tests for store.py against in-memory SQLite."""
from datetime import datetime, timedelta, timezone

import pytest

from daely_google_bridge.store import Store


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
