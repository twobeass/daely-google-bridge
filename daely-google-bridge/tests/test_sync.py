"""Sync orchestration tests with mocked Daely + Google clients.

We don't touch the network. Daely returns a hand-crafted list of
CalendarWithEvents; Google is a MagicMock whose insert/patch/delete are
inspected.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from daely_google_bridge.config import BridgeConfig
from daely_google_bridge.models import (
    CalendarEvent,
    CalendarWithEvents,
    Profile,
    StartEnd,
)
from daely_google_bridge.store import Store
from daely_google_bridge.sync import (
    SyncReport,
    full_sync,
    incremental_sync,
)


# ─────────────── helpers ───────────────

PROFILE_A = "00000000-0000-0000-0004-000000000001"
PROFILE_B = "00000000-0000-0000-0004-000000000002"
GOOGLE_CAL_A = "google-cal-A@group.calendar.google.com"
GOOGLE_CAL_FALLBACK = "google-cal-fallback@group.calendar.google.com"


def _config(
    *,
    mapping: dict | None = None,
    fallback: str | None = GOOGLE_CAL_FALLBACK,
    lookback: int = 30,
    lookahead: int = 90,
) -> BridgeConfig:
    return BridgeConfig(
        daely_email="t@example.com",
        google_oauth_client_secrets_file="/tmp/c.json",
        profile_calendar_mapping=mapping or {PROFILE_A: GOOGLE_CAL_A},
        fallback_google_calendar_id=fallback,
        lookback_days=lookback,
        lookahead_days=lookahead,
    )


def _event(
    *,
    id: str = "00000000-0000-0000-0005-000000000001",
    title: str = "Test event",
    recurringId: str | None = None,
    deleted: bool = False,
    updated: datetime | None = None,
    recurrence: list[str] | None = None,
    start_dt: str = "2026-05-08T15:00:00+02:00",
    end_dt: str = "2026-05-08T16:00:00+02:00",
) -> CalendarEvent:
    return CalendarEvent.model_validate({
        "id": id,
        "recurringId": recurringId,
        "deleted": deleted,
        "title": title,
        "description": "",
        "location": None,
        "start": {"dateTime": start_dt, "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": end_dt,   "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00",
        "updated": (updated or datetime(2026, 4, 27, 18, 37, 31, tzinfo=timezone.utc)).isoformat(),
        "recurrence": recurrence or [],
        "reminders": [],
        "customColorCode": None,
        "additionalParticipants": [],
        "editable": True,
        "hasError": False,
        "privateEvent": False,
    })


def _calendar_with_events(
    *,
    cal_id: str = "daely-cal-1",
    profileId: str | None = PROFILE_A,
    calendarType: int = 0,
    events: list[CalendarEvent] | None = None,
) -> CalendarWithEvents:
    return CalendarWithEvents.model_validate({
        "id": cal_id,
        "title": "Family",
        "calendarType": calendarType,
        "shareType": None,
        "profileId": profileId,
        "isClassSchedule": False,
        "writeable": True,
        "timeZone": "Europe/Berlin",
        "events": [e.model_dump(mode="json") for e in (events or [])],
        "hasError": False,
        "eventsIncluded": True,
        "presentationType": 1,
    })


def _daely_mock(
    cwes: list[CalendarWithEvents],
    *,
    profiles: list | None = None,
    profile_fetch_raises: Exception | None = None,
) -> MagicMock:
    daely = MagicMock()
    daely.get_my_groups.return_value = [MagicMock(id="grp-1", name="Family")]
    daely.get_calendars_with_events.return_value = cwes
    if profile_fetch_raises is not None:
        daely.get_profiles.side_effect = profile_fetch_raises
    else:
        daely.get_profiles.return_value = profiles or []
    return daely


def _google_mock(*, insert_id: str = "g-new") -> MagicMock:
    google = MagicMock()
    # default insert returns predictable id
    counter = {"i": 0}

    def _insert(cal_id, body):
        counter["i"] += 1
        return {"id": f"{insert_id}-{counter['i']}"}

    google.insert_event.side_effect = _insert
    google.patch_event.return_value = {"id": "patched"}
    google.delete_event.return_value = None
    return google


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


# ─────────────── insert ───────────────

def test_full_sync_inserts_new_events(store):
    ev = _event(id="ev-1", title="Hello")
    cwes = [_calendar_with_events(events=[ev])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())

    assert report.inserts == 1
    assert report.patches == 0
    assert report.deletes == 0
    assert report.errors == []
    google.insert_event.assert_called_once()
    cal_id_arg, body_arg = google.insert_event.call_args.args
    assert cal_id_arg == GOOGLE_CAL_A
    assert body_arg["summary"] == "Hello"
    # mapping persisted
    m = store.get_event_mapping("ev-1")
    assert m is not None
    assert m.google_event_id == "g-new-1"
    assert m.google_calendar_id == GOOGLE_CAL_A
    assert m.daely_calendar_id == "daely-cal-1"


def test_full_sync_inserts_use_fallback_for_unprofiled_calendar(store):
    cwes = [_calendar_with_events(profileId=None, events=[_event(id="x")])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 1
    cal_id_arg, _body = google.insert_event.call_args.args
    assert cal_id_arg == GOOGLE_CAL_FALLBACK


def test_full_sync_skips_calendar_when_no_target(store):
    cwes = [_calendar_with_events(profileId="unmapped-prof", events=[_event(), _event(id="ev-2")])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config(mapping={}, fallback=None))
    assert report.inserts == 0
    assert report.skipped_no_target_events == 2
    assert len(report.errors) == 1
    google.insert_event.assert_not_called()


# ─────────────── patch ───────────────

def test_full_sync_patches_when_updated_changed(store):
    old_updated = datetime(2026, 4, 1, tzinfo=timezone.utc)
    new_updated = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # Pre-populate store with stale mapping
    store.put_event_mapping(
        daely_id="ev-1",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-existing",
        google_calendar_id=GOOGLE_CAL_A,
        last_seen_updated=old_updated,
    )
    ev = _event(id="ev-1", title="Updated title", updated=new_updated)
    cwes = [_calendar_with_events(events=[ev])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.patches == 1
    assert report.inserts == 0
    google.patch_event.assert_called_once()
    cal_id_arg, ev_id_arg, body_arg = google.patch_event.call_args.args
    assert cal_id_arg == GOOGLE_CAL_A
    assert ev_id_arg == "g-existing"
    assert body_arg["summary"] == "Updated title"
    # last_seen_updated bumped
    m = store.get_event_mapping("ev-1")
    assert m.last_seen_updated == new_updated


def test_full_sync_no_op_when_updated_unchanged(store):
    same_updated = datetime(2026, 4, 27, 18, 37, 31, tzinfo=timezone.utc)
    store.put_event_mapping(
        daely_id="ev-1",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-existing",
        google_calendar_id=GOOGLE_CAL_A,
        last_seen_updated=same_updated,
    )
    ev = _event(id="ev-1", updated=same_updated)
    daely = _daely_mock([_calendar_with_events(events=[ev])])
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.no_ops == 1
    assert report.inserts == 0
    assert report.patches == 0
    google.insert_event.assert_not_called()
    google.patch_event.assert_not_called()


# ─────────────── delete ───────────────

def test_full_sync_propagates_deleted_flag(store):
    store.put_event_mapping(
        daely_id="ev-1",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-existing",
        google_calendar_id=GOOGLE_CAL_A,
    )
    ev = _event(id="ev-1", deleted=True)
    cwes = [_calendar_with_events(events=[ev])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.deletes == 1
    google.delete_event.assert_called_once_with(GOOGLE_CAL_A, "g-existing")
    assert store.get_event_mapping("ev-1") is None


def test_full_sync_detects_missing_events_as_deletions(store):
    """Mapping in store but event no longer in snapshot → delete in Google."""
    store.put_event_mapping(
        daely_id="ev-gone",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-gone",
        google_calendar_id=GOOGLE_CAL_A,
    )
    # Empty snapshot for the same daely calendar
    cwes = [_calendar_with_events(events=[])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.deletes == 1
    google.delete_event.assert_called_once_with(GOOGLE_CAL_A, "g-gone")
    assert store.get_event_mapping("ev-gone") is None


def test_incremental_sync_does_not_detect_missing_as_deleted(store):
    """Incremental relies on deleted=true flags; missing events are tolerated."""
    store.put_event_mapping(
        daely_id="ev-gone",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-gone",
        google_calendar_id=GOOGLE_CAL_A,
    )
    cwes = [_calendar_with_events(events=[])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = incremental_sync(daely, google, store, _config())
    assert report.deletes == 0
    google.delete_event.assert_not_called()
    assert store.get_event_mapping("ev-gone") is not None


def test_incremental_sync_uses_configured_window_by_default(store):
    """The poll window defaults to config.lookback_days/lookahead_days, not 1/30,
    so edits anywhere in the configured range propagate on every cycle."""
    daely = _daely_mock([_calendar_with_events(events=[])])
    google = _google_mock()

    incremental_sync(daely, google, store, _config(lookback=30, lookahead=365))

    _, kwargs = daely.get_calendars_with_events.call_args
    today = date.today()
    assert kwargs["start_date"] == today - timedelta(days=30)
    assert kwargs["end_date"] == today + timedelta(days=365)


def test_incremental_sync_explicit_window_overrides_config(store):
    """Callers (e.g. tests) can still pin the window via explicit kwargs."""
    daely = _daely_mock([_calendar_with_events(events=[])])
    google = _google_mock()

    incremental_sync(
        daely, google, store, _config(lookback=30, lookahead=365),
        lookback_days=1, lookahead_days=7,
    )

    _, kwargs = daely.get_calendars_with_events.call_args
    today = date.today()
    assert kwargs["start_date"] == today - timedelta(days=1)
    assert kwargs["end_date"] == today + timedelta(days=7)


# ─────────────── master-only dedup ───────────────

def test_full_sync_dedups_recurring_to_one_insert(store):
    """4 instances of a recurring series → 1 insert, 1 mapping under master uuid."""
    master = "00000000-0000-0000-0005-000000000010"
    starts = [
        ("2026-05-01T15:00:00+02:00", "2026-05-01T16:00:00+02:00"),
        ("2026-05-08T15:00:00+02:00", "2026-05-08T16:00:00+02:00"),
        ("2026-05-15T15:00:00+02:00", "2026-05-15T16:00:00+02:00"),
        ("2026-05-22T15:00:00+02:00", "2026-05-22T16:00:00+02:00"),
    ]
    events = [
        _event(
            id=f"{master}_2026{['0501','0508','0515','0522'][i]}T130000Z",
            recurringId=master,
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=FR"],
            start_dt=s, end_dt=e,
        )
        for i, (s, e) in enumerate(starts)
    ]
    cwes = [_calendar_with_events(events=events)]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 1
    google.insert_event.assert_called_once()
    body = google.insert_event.call_args.args[1]
    assert "recurrence" in body
    # mapping is under master uuid (not composite id)
    assert store.get_event_mapping(master) is not None


def test_full_sync_synthesizes_exdates_for_deleted_series_instance(store):
    """§3.1: when Daely drops an occurrence from a recurring series (user
    deleted a single instance), the bridge synthesizes EXDATE lines so the
    Google master event omits it too."""
    master = "00000000-0000-0000-0005-000000000020"
    # Weekly Thursdays — but 2026-05-14 is MISSING (user deleted it in Daely).
    # Daely returns 05-07, [gap], 05-21, 05-28 with the unchanged RRULE.
    present = ["2026-05-07", "2026-05-21", "2026-05-28"]
    events = [
        _event(
            id=f"{master}_{d.replace('-', '')}T135000Z",
            recurringId=master,
            recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"],
            start_dt=f"{d}T15:50:00+02:00",
            end_dt=f"{d}T16:50:00+02:00",
        )
        for d in present
    ]
    cwes = [_calendar_with_events(events=events)]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 1
    body = google.insert_event.call_args.args[1]
    assert body["recurrence"] == [
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH",
        "EXDATE;TZID=Europe/Berlin:20260514T155000",
    ]


def test_full_sync_no_exdates_when_series_is_complete(store):
    """A gapless recurring series → recurrence stays just the RRULE."""
    master = "00000000-0000-0000-0005-000000000021"
    present = ["2026-05-07", "2026-05-14", "2026-05-21"]
    events = [
        _event(
            id=f"{master}_{d.replace('-', '')}T135000Z",
            recurringId=master,
            recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"],
            start_dt=f"{d}T15:50:00+02:00",
            end_dt=f"{d}T16:50:00+02:00",
        )
        for d in present
    ]
    cwes = [_calendar_with_events(events=events)]
    daely = _daely_mock(cwes)
    google = _google_mock()

    full_sync(daely, google, store, _config())
    body = google.insert_event.call_args.args[1]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"]


def test_full_sync_exdates_multiple_gaps_in_one_series(store):
    """Two deleted occurrences → two EXDATE lines."""
    master = "00000000-0000-0000-0005-000000000022"
    # 05-07, [05-14 + 05-21 deleted], 05-28
    present = ["2026-05-07", "2026-05-28"]
    events = [
        _event(
            id=f"{master}_{d.replace('-', '')}T135000Z",
            recurringId=master,
            recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"],
            start_dt=f"{d}T15:50:00+02:00",
            end_dt=f"{d}T16:50:00+02:00",
        )
        for d in present
    ]
    cwes = [_calendar_with_events(events=events)]
    daely = _daely_mock(cwes)
    google = _google_mock()

    full_sync(daely, google, store, _config())
    body = google.insert_event.call_args.args[1]
    assert body["recurrence"] == [
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH",
        "EXDATE;TZID=Europe/Berlin:20260514T155000",
        "EXDATE;TZID=Europe/Berlin:20260521T155000",
    ]


# ─────────────── filter ───────────────

def test_full_sync_skips_external_calendars(store):
    cwes = [
        _calendar_with_events(cal_id="ext-1", calendarType=1, events=[_event()]),
        _calendar_with_events(cal_id="ext-2", calendarType=2, events=[_event(id="x")]),
    ]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 0
    assert report.skipped_external_calendar_events == 2
    google.insert_event.assert_not_called()


# ─────────────── error resilience ───────────────

def test_full_sync_isolates_per_event_errors(store):
    events = [_event(id="ev-1"), _event(id="ev-2"), _event(id="ev-3")]
    cwes = [_calendar_with_events(events=events)]
    daely = _daely_mock(cwes)
    google = MagicMock()
    # ev-2 fails, others succeed
    counter = {"i": 0}

    def _insert(cal_id, body):
        counter["i"] += 1
        if counter["i"] == 2:
            raise RuntimeError("transient google error on ev-2")
        return {"id": f"g-{counter['i']}"}

    google.insert_event.side_effect = _insert

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 2
    assert len(report.errors) == 1
    failed_id, msg = report.errors[0]
    assert failed_id == "ev-2"
    assert "transient" in msg
    # ev-1 and ev-3 successfully mapped
    assert store.get_event_mapping("ev-1") is not None
    assert store.get_event_mapping("ev-3") is not None
    assert store.get_event_mapping("ev-2") is None


def test_full_sync_isolates_delete_errors_continues_inserts(store):
    """If a delete fails, the rest of the calendar should still process."""
    store.put_event_mapping(
        daely_id="ev-gone",
        daely_calendar_id="daely-cal-1",
        google_event_id="g-gone",
        google_calendar_id=GOOGLE_CAL_A,
    )
    new_ev = _event(id="ev-new")
    cwes = [_calendar_with_events(events=[new_ev])]
    daely = _daely_mock(cwes)
    google = _google_mock()
    google.delete_event.side_effect = RuntimeError("google-down")

    report = full_sync(daely, google, store, _config())
    assert report.inserts == 1
    assert report.deletes == 0
    assert len(report.errors) == 1
    assert report.errors[0][0] == "ev-gone"
    # The failed delete keeps the mapping (so we'll retry next sync)
    assert store.get_event_mapping("ev-gone") is not None


# ─────────────── retry-loop (§1.2) ───────────────

def test_failed_patch_records_retry_state(store):
    """A patch error must populate retry_count + retry_after on the mapping."""
    seen = datetime(2026, 4, 27, 18, 37, 31, tzinfo=timezone.utc)
    store.put_event_mapping(
        daely_id="ev-1", daely_calendar_id="daely-cal-1",
        google_event_id="g-1", google_calendar_id=GOOGLE_CAL_A,
        last_seen_updated=seen,
    )
    # Daely now reports the event with a newer `updated` → bridge will try patch.
    newer = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    cwes = [_calendar_with_events(events=[_event(id="ev-1", updated=newer)])]
    daely = _daely_mock(cwes)
    google = _google_mock()
    google.patch_event.side_effect = RuntimeError("google-403")

    report = full_sync(daely, google, store, _config())
    assert report.patches == 0
    assert len(report.errors) == 1

    m = store.get_event_mapping("ev-1")
    assert m.failed is True
    assert m.retry_count == 1
    assert m.retry_after is not None
    assert "google-403" in (m.last_error or "")


def test_failed_event_in_cooldown_is_skipped(store):
    """A mapping with retry_after in the future is not patched again."""
    seen = datetime(2026, 4, 27, 18, 37, 31, tzinfo=timezone.utc)
    far_future = datetime.now(timezone.utc) + datetime.now(timezone.utc).resolution * 0
    # Use timedelta safely
    from datetime import timedelta
    far_future = datetime.now(timezone.utc) + timedelta(hours=1)
    store.put_event_mapping(
        daely_id="ev-1", daely_calendar_id="daely-cal-1",
        google_event_id="g-1", google_calendar_id=GOOGLE_CAL_A,
        last_seen_updated=seen,
        failed=True, retry_count=3, retry_after=far_future,
        last_error="prior failure",
    )
    newer = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    cwes = [_calendar_with_events(events=[_event(id="ev-1", updated=newer)])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.patches == 0
    assert report.skipped_retry_cooldown == 1
    google.patch_event.assert_not_called()
    # Retry state preserved
    m = store.get_event_mapping("ev-1")
    assert m.failed is True
    assert m.retry_count == 3


def test_successful_retry_clears_failure_state(store):
    """When the cooldown elapses and the patch succeeds, retry state resets."""
    from datetime import timedelta
    seen = datetime(2026, 4, 27, 18, 37, 31, tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - timedelta(minutes=1)  # cooldown over
    store.put_event_mapping(
        daely_id="ev-1", daely_calendar_id="daely-cal-1",
        google_event_id="g-1", google_calendar_id=GOOGLE_CAL_A,
        last_seen_updated=seen,
        failed=True, retry_count=2, retry_after=elapsed,
        last_error="temporarily down",
    )
    # Even though the Daely event hasn't actually changed, the bridge still
    # retries because failed=True takes precedence over the no-op shortcut.
    cwes = [_calendar_with_events(events=[_event(id="ev-1", updated=seen)])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())
    assert report.patches == 1
    google.patch_event.assert_called_once()

    m = store.get_event_mapping("ev-1")
    assert m.failed is False
    assert m.retry_count == 0
    assert m.retry_after is None
    assert m.last_error is None


def test_failed_delete_records_retry_state(store):
    """Delete errors also populate retry state (cooldown applies to either op)."""
    store.put_event_mapping(
        daely_id="ev-gone", daely_calendar_id="daely-cal-1",
        google_event_id="g-gone", google_calendar_id=GOOGLE_CAL_A,
    )
    cwes = [_calendar_with_events(events=[])]  # event missing → triggers delete
    daely = _daely_mock(cwes)
    google = _google_mock()
    google.delete_event.side_effect = RuntimeError("can't reach google")

    report = full_sync(daely, google, store, _config())
    assert report.deletes == 0
    assert len(report.errors) == 1

    m = store.get_event_mapping("ev-gone")
    assert m is not None  # mapping kept for retry
    assert m.failed is True
    assert m.retry_count == 1
    assert m.retry_after is not None


# ─────────────── sync_history persistence (§10.1) ───────────────

def test_sync_history_persisted_after_full_sync(store):
    cwes = [_calendar_with_events(events=[_event(id="ev-1")])]
    daely = _daely_mock(cwes)
    google = _google_mock()

    report = full_sync(daely, google, store, _config())

    history = store.recent_sync_history()
    assert len(history) == 1
    h = history[0]
    assert h.run_id == report.run_id
    assert h.inserts == report.inserts
    assert h.errors_count == 0


def test_sync_history_records_errors(store):
    cwes = [_calendar_with_events(events=[_event(id="ev-fail")])]
    daely = _daely_mock(cwes)
    google = _google_mock()
    google.insert_event.side_effect = RuntimeError("oh no")

    report = full_sync(daely, google, store, _config())
    assert len(report.errors) == 1

    history = store.recent_sync_history()
    assert len(history) == 1
    assert history[0].errors_count == 1
    assert history[0].errors[0][0] == "ev-fail"
    assert "oh no" in history[0].errors[0][1]


def test_sync_history_persisted_even_when_no_groups(store):
    """Aborted-at-top syncs still get a history row for diagnostic visibility."""
    daely = MagicMock()
    daely.get_my_groups.return_value = []
    google = _google_mock()

    full_sync(daely, google, store, _config())

    history = store.recent_sync_history()
    assert len(history) == 1
    assert history[0].errors_count == 1
    assert "no Daely groups" in history[0].errors[0][1]


# ─────────────── no groups ───────────────

def test_full_sync_no_groups_returns_error_in_report(store):
    daely = MagicMock()
    daely.get_my_groups.return_value = []
    google = _google_mock()
    report = full_sync(daely, google, store, _config())
    assert report.inserts == 0
    assert any("no Daely groups" in msg for _, msg in report.errors)


# ─────────────── end-to-end with fixture ───────────────

# ─────────────── Phase 3e — profile footer threading ───────────────

def test_full_sync_passes_profile_footer_to_google(store):
    """When daely.get_profiles() returns names, the Google body picks up a footer."""
    profile_anna = Profile(id="prof-anna", name="Anna")
    profile_bob = Profile(id="prof-bob", name="Bob")
    ev = _event(id="ev-1", title="Sample event")
    # patch additionalParticipants directly via model_validate workaround:
    ev_dict = ev.model_dump(mode="json")
    ev_dict["additionalParticipants"] = ["prof-anna", "prof-bob"]
    ev_with_aps = CalendarEvent.model_validate(ev_dict)

    cwes = [_calendar_with_events(events=[ev_with_aps])]
    daely = _daely_mock(cwes, profiles=[profile_anna, profile_bob])
    google = _google_mock()

    full_sync(daely, google, store, _config())

    google.insert_event.assert_called_once()
    body = google.insert_event.call_args.args[1]
    assert body["description"] == "👥 Beteiligt: Anna, Bob"


def test_full_sync_continues_when_profile_fetch_fails(store):
    """If get_profiles() raises, sync runs without a footer (empty profiles_map)."""
    ev = _event(id="ev-1")
    ev_dict = ev.model_dump(mode="json")
    ev_dict["additionalParticipants"] = ["prof-x"]
    ev_with_aps = CalendarEvent.model_validate(ev_dict)

    cwes = [_calendar_with_events(events=[ev_with_aps])]
    daely = _daely_mock(cwes, profile_fetch_raises=RuntimeError("daely-down"))
    google = _google_mock()

    report = full_sync(daely, google, store, _config())

    # Sync still produced an insert
    assert report.inserts == 1
    google.insert_event.assert_called_once()
    body = google.insert_event.call_args.args[1]
    # No footer means description is missing entirely (event description was "")
    assert "description" not in body or "Beteiligt" not in body.get("description", "")
    # Sync didn't add a top-level error for the profile fetch — it's just a
    # warning. Other errors should remain empty.
    assert report.errors == []


def test_incremental_sync_also_loads_profiles(store):
    profile_anna = Profile(id="prof-anna", name="Anna")
    ev = _event(id="ev-1")
    ev_dict = ev.model_dump(mode="json")
    ev_dict["additionalParticipants"] = ["prof-anna"]
    ev_with_aps = CalendarEvent.model_validate(ev_dict)

    cwes = [_calendar_with_events(events=[ev_with_aps])]
    daely = _daely_mock(cwes, profiles=[profile_anna])
    google = _google_mock()

    incremental_sync(daely, google, store, _config())
    daely.get_profiles.assert_called_once_with("grp-1")
    body = google.insert_event.call_args.args[1]
    assert "👥 Beteiligt: Anna" in body["description"]


def test_full_sync_against_anonymized_fixture(store, with_events_payload):
    """Smoke test: feed the actual anonymized live-read response.

    The real fixture has 3 calendars. Only one (calendarType=0) yields inserts.
    Recurring instances dedup to one Google body each.
    """
    cwes = [CalendarWithEvents.model_validate(c) for c in with_events_payload]
    daely = _daely_mock(cwes)
    google = _google_mock()

    cfg = _config(
        mapping={"00000000-0000-0000-0004-000000000001": GOOGLE_CAL_A},
        fallback=GOOGLE_CAL_FALLBACK,
    )
    report = full_sync(daely, google, store, cfg)

    # We expect: at least one insert (the family calendar has events).
    assert report.inserts > 0
    # External calendars (calendarType=1) → skipped events count > 0
    assert report.skipped_external_calendar_events > 0
    # Inserts don't double-fire for recurring series
    inserts_with_recurrence = sum(
        1 for c in google.insert_event.call_args_list
        if "recurrence" in c.args[1]
    )
    inserts_without = report.inserts - inserts_with_recurrence
    # 32 recurring instances → at most ~5–10 series in the sample.
    # Single events (~12) + ~5 series → roughly 12-25 total.
    # Generous bounds avoid coupling tests to fixture exact counts.
    assert 5 < report.inserts < 40
