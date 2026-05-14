"""Tests for mapper.py against anonymized live fixtures.

Covers all 8 event categories from the Phase-3b spec:
1. Single timed event
2. Single all-day event
3. Recurring master event
4. Recurring instance (must be dropped by dedup)
5. Private event (visibility="private")
6. Event with additionalParticipants
7. Event with customColorCode
8. Event from calendar with calendarType=1 (must be filtered → None)
"""
import json

import pytest

from daely_google_bridge.mapper import (
    compute_series_exdates,
    daely_event_to_google,
    deduplicate_recurring,
    exdates_by_recurring_id,
    is_recurring_instance_skip,
    select_target_calendar_id,
    should_skip_calendar,
)
from daely_google_bridge.models import (
    Calendar,
    CalendarEvent,
    CalendarWithEvents,
    Profile,
    is_all_day,
    is_recurring_master_or_unique,
    master_uuid,
)


# ─────────────────── helpers ───────────────────

def _make_internal_calendar() -> Calendar:
    """Construct a minimal Calendar with calendarType=0 for use as an event context."""
    return Calendar(
        id="00000000-0000-0000-0002-000000000099",
        title="Test Family",
        timeZone="Europe/Berlin",
        calendarType=0,
        shareType=None,
        profileId="00000000-0000-0000-0004-000000000099",
        writeable=True,
    )


def _profile_map() -> dict[str, str]:
    return {
        "00000000-0000-0000-0004-000000000001": "cal-prof1@google",
        "00000000-0000-0000-0004-000000000002": "cal-prof2@google",
        "00000000-0000-0000-0004-000000000099": "cal-fallback@google",
    }


# ─────────────────── parsing ───────────────────

def test_models_parse_full_with_events_payload(with_events_payload):
    parsed = [CalendarWithEvents.model_validate(c) for c in with_events_payload]
    assert len(parsed) == 3
    total_events = sum(len(c.events) for c in parsed)
    assert total_events == 80


def test_event_field_count(with_events_payload):
    """All 17 fields appear on every event in the live response."""
    expected = {
        "id", "recurringId", "deleted", "title", "description", "location",
        "start", "end", "created", "updated", "recurrence", "reminders",
        "customColorCode", "additionalParticipants", "editable", "hasError",
        "privateEvent",
    }
    for cal in with_events_payload:
        for ev in cal["events"]:
            assert set(ev.keys()) == expected


# ─────────────────── (1) single timed event ───────────────────

def test_single_timed_event_mapping(with_events_payload):
    cal = CalendarWithEvents.model_validate(with_events_payload[0])
    cal_internal = _make_internal_calendar()
    candidates = [e for e in cal.events if e.recurringId is None and e.start.dateTime is not None]
    assert candidates
    body = daely_event_to_google(candidates[0], cal_internal, _profile_map())
    assert body is not None
    assert body["summary"] == candidates[0].title
    assert "dateTime" in body["start"]
    assert body["start"]["timeZone"] == candidates[0].start.timeZone
    assert body["iCalUID"].startswith(candidates[0].id)
    assert "recurrence" not in body
    assert body["extendedProperties"]["private"]["daely_id"] == candidates[0].id
    assert body["extendedProperties"]["private"]["daely_calendar_id"] == cal_internal.id
    assert body["extendedProperties"]["private"]["daely_profile_id"] == cal_internal.profileId


# ─────────────────── (2) all-day event ───────────────────

def test_all_day_event_mapping(with_events_payload):
    flat = [e for cal in with_events_payload for e in cal["events"]]
    raw_allday = [e for e in flat if e["start"]["dateTime"] is None]
    assert raw_allday
    ev = CalendarEvent.model_validate(raw_allday[0])
    assert is_all_day(ev)
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map())
    assert body is not None
    assert "date" in body["start"]
    assert "dateTime" not in body["start"]
    # All-day per Google spec: no timeZone on start/end
    assert "timeZone" not in body["start"]
    assert "timeZone" not in body["end"]
    assert "date" in body["end"]
    assert len(body["start"]["date"]) == 10  # YYYY-MM-DD


# ─────────────────── (3) recurring master ───────────────────

def test_recurring_master_carries_rrule(with_events_payload):
    flat = [e for cal in with_events_payload for e in cal["events"]]
    raw_rec = [e for e in flat if e.get("recurringId")]
    assert raw_rec
    # Mapper itself drops instances (defensive). Pass through dedup first.
    parsed = [CalendarEvent.model_validate(e) for e in raw_rec]
    deduped = deduplicate_recurring(parsed)
    masters_in_dedup = [e for e in deduped if e.recurringId is not None]
    assert masters_in_dedup
    body = daely_event_to_google(masters_in_dedup[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert "recurrence" in body
    assert body["recurrence"][0].startswith("RRULE:")


# ─────────────────── (4) recurring instance is dropped ───────────────────

def test_recurring_instance_is_recognized_by_helper():
    """`is_recurring_instance_skip()` flags entries with a recurringId.

    Note: the mapper itself relies on the caller having called
    `deduplicate_recurring()` first. The helper is for the caller's use, not an
    internal mapper guard.
    """
    instance = CalendarEvent.model_validate({
        "id": "00000000-0000-0000-0005-000000000001_20260515T130000Z",
        "recurringId": "00000000-0000-0000-0005-000000000001",
        "deleted": False, "title": "x", "description": "", "location": None,
        "start": {"dateTime": "2026-05-15T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-15T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31.066922+00:00",
        "updated": "2026-04-27T18:37:31.066922+00:00",
        "recurrence": ["RRULE:FREQ=WEEKLY"],
        "reminders": [], "customColorCode": None, "additionalParticipants": [],
        "editable": True, "hasError": False, "privateEvent": False,
    })
    assert is_recurring_instance_skip(instance)


def test_dedup_then_map_emits_one_per_series(with_events_payload):
    """End-to-end: dedup → map → exactly one Google body per recurring master."""
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    cal = _make_internal_calendar()
    bodies = [daely_event_to_google(e, cal, _profile_map()) for e in deduped]
    bodies = [b for b in bodies if b is not None]
    masters_in = {e.recurringId for e in flat if e.recurringId}
    expected_recurring = len(masters_in)
    bodies_with_recurrence = [b for b in bodies if "recurrence" in b]
    assert len(bodies_with_recurrence) == expected_recurring


def test_deduplicate_picks_earliest_per_series(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    seen = {}
    for e in deduped:
        if e.recurringId is None:
            continue
        assert e.recurringId not in seen
        seen[e.recurringId] = e

    # Verify each survivor really is the earliest in its series.
    by_master = {}
    for e in flat:
        if e.recurringId:
            by_master.setdefault(e.recurringId, []).append(e)
    for master_id, instances in by_master.items():
        earliest = min(instances, key=lambda x: x.start.dateTime or x.start.date)
        assert seen[master_id].id == earliest.id


def test_deduplicate_preserves_non_recurring_count(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    n_in = sum(1 for e in flat if e.recurringId is None)
    n_out = sum(1 for e in deduped if e.recurringId is None)
    assert n_in == n_out


# ─────────────────── (5) private event ───────────────────

def test_private_event_flag_maps_to_visibility():
    ev = CalendarEvent.model_validate({
        "id": "x", "recurringId": None, "deleted": False, "title": "secret",
        "description": "", "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00", "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [], "customColorCode": None,
        "additionalParticipants": [], "editable": True, "hasError": False,
        "privateEvent": True,
    })
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["visibility"] == "private"
    assert body["extendedProperties"]["private"]["daely_private_event"] == "true"


def test_non_private_event_omits_visibility():
    ev = CalendarEvent.model_validate({
        "id": "x", "recurringId": None, "deleted": False, "title": "public",
        "description": "", "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00", "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [], "customColorCode": None,
        "additionalParticipants": [], "editable": True, "hasError": False,
        "privateEvent": False,
    })
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map())
    assert body is not None
    assert "visibility" not in body


# ─────────────────── (6) additionalParticipants ───────────────────

def test_additional_participants_to_extended_properties(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    flat_dedup = deduplicate_recurring(flat)
    with_ap = [e for e in flat_dedup if e.additionalParticipants]
    assert with_ap
    body = daely_event_to_google(with_ap[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    raw = body["extendedProperties"]["private"]["daely_additional_participants"]
    assert json.loads(raw) == with_ap[0].additionalParticipants


def test_no_additional_participants_omits_extProp_key():
    ev = CalendarEvent.model_validate({
        "id": "x", "recurringId": None, "deleted": False, "title": "alone",
        "description": "", "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00", "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [], "customColorCode": None,
        "additionalParticipants": [], "editable": True, "hasError": False,
        "privateEvent": False,
    })
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map())
    assert body is not None
    assert "daely_additional_participants" not in body["extendedProperties"]["private"]


# ─────────────────── (7) customColorCode ───────────────────

def test_custom_color_to_extended_properties(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    with_cc = [e for e in flat if e.customColorCode]
    assert with_cc, "fixture should contain at least one event with customColorCode"
    body = daely_event_to_google(with_cc[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["extendedProperties"]["private"]["daely_custom_color"] == with_cc[0].customColorCode
    assert "colorId" not in body  # we deliberately don't touch Google's colorId


# ─────────────────── (8) calendarType filter ───────────────────

def test_event_from_external_calendar_returns_none():
    """Calendar with calendarType=1 (Google-synced) → mapper returns None."""
    google_cal = Calendar(
        id="some-google-cal",
        title="Synced from Google",
        timeZone="Europe/Berlin",
        calendarType=1,    # not internal
        shareType=2,
        profileId=None,
        writeable=True,
    )
    ev = CalendarEvent.model_validate({
        "id": "x", "recurringId": None, "deleted": False, "title": "external",
        "description": "", "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00", "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [], "customColorCode": None,
        "additionalParticipants": [], "editable": True, "hasError": False,
        "privateEvent": False,
    })
    assert daely_event_to_google(ev, google_cal, _profile_map()) is None
    assert should_skip_calendar(google_cal)


def test_should_skip_calendar_only_skips_non_internal():
    cal0 = Calendar(id="a", title="t", calendarType=0, writeable=True)
    cal1 = Calendar(id="b", title="t", calendarType=1, writeable=True)
    cal2 = Calendar(id="c", title="t", calendarType=2, writeable=True)
    assert not should_skip_calendar(cal0)
    assert should_skip_calendar(cal1)
    assert should_skip_calendar(cal2)


# ─────────────────── reminders ───────────────────

def test_reminders_use_default_when_empty(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    no_rem = [e for e in deduped if not e.reminders and e.recurringId is None]
    assert no_rem
    body = daely_event_to_google(no_rem[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["reminders"] == {"useDefault": True}


def test_reminders_overrides_when_set(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    with_rem = [e for e in deduped if e.reminders]
    assert with_rem
    body = daely_event_to_google(with_rem[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["reminders"]["useDefault"] is False
    assert all(o["method"] == "popup" for o in body["reminders"]["overrides"])
    assert [o["minutes"] for o in body["reminders"]["overrides"]] == with_rem[0].reminders


# ─────────────────── description handling ───────────────────

def test_empty_description_not_emitted(with_events_payload):
    """description="" or None should not produce a description field in Google body."""
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    empty = [e for e in deduped if not e.description]
    assert empty
    body = daely_event_to_google(empty[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert "description" not in body


def test_non_empty_description_passes_through(with_events_payload):
    flat = [CalendarEvent.model_validate(e)
            for cal in with_events_payload for e in cal["events"]]
    deduped = deduplicate_recurring(flat)
    with_desc = [e for e in deduped if e.description]
    assert with_desc, "fixture should contain at least one event with description"
    body = daely_event_to_google(with_desc[0], _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["description"] == with_desc[0].description


# ─────────────────── helpers ───────────────────

def test_master_uuid_helper():
    instance = CalendarEvent.model_validate({
        "id": "00000000-0000-0000-0005-000000000001_20260508T130000Z",
        "recurringId": "00000000-0000-0000-0005-000000000001",
        "deleted": False, "title": "x", "description": "", "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31.066922+00:00",
        "updated": "2026-04-27T18:37:31.066922+00:00",
        "recurrence": ["RRULE:FREQ=WEEKLY"],
        "reminders": [], "customColorCode": None, "additionalParticipants": [],
        "editable": True, "hasError": False, "privateEvent": False,
    })
    assert master_uuid(instance) == "00000000-0000-0000-0005-000000000001"
    assert not is_recurring_master_or_unique(instance)


# ─────────────────── target-calendar selection ───────────────────

def test_select_target_calendar_with_profile_match():
    profile_map = {"prof-A": "cal-A@google", "prof-B": "cal-B@google"}
    assert select_target_calendar_id("prof-A", profile_map) == "cal-A@google"


def test_select_target_calendar_falls_back_when_no_profile():
    profile_map = {"prof-A": "cal-A@google"}
    target = select_target_calendar_id(None, profile_map, fallback_calendar_id="fb@google")
    assert target == "fb@google"


def test_select_target_calendar_raises_without_match_or_fallback():
    with pytest.raises(KeyError):
        select_target_calendar_id("unknown", {}, fallback_calendar_id=None)


# ─────────────── Phase 3e — profile footer ───────────────

def _ev_with_participants(participants: list[str], description: str | None = "") -> CalendarEvent:
    """Helper: minimal event with arbitrary additionalParticipants."""
    return CalendarEvent.model_validate({
        "id": "evt-x",
        "recurringId": None,
        "deleted": False,
        "title": "Sample event",
        "description": description,
        "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00",
        "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [], "customColorCode": None,
        "additionalParticipants": participants,
        "editable": True, "hasError": False, "privateEvent": False,
    })


def test_footer_appends_to_existing_description():
    profiles = {"p-anna": "Anna", "p-bob": "Bob"}
    ev = _ev_with_participants(["p-anna", "p-bob"], description="Treffpunkt am Eingang")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "Treffpunkt am Eingang\n\n👥 Beteiligt: Anna, Bob"


def test_footer_drops_unknown_uuids():
    profiles = {"p-anna": "Anna"}
    ev = _ev_with_participants(["p-anna", "p-unknown-uuid"], description="x")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "x\n\n👥 Beteiligt: Anna"


def test_empty_profiles_map_falls_back_to_existing_behaviour():
    profiles = {}
    ev = _ev_with_participants(["p-someone"], description="kept as-is")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "kept as-is"


def test_profiles_map_none_is_backward_compatible():
    """profiles_map=None must behave exactly like the pre-3e mapper."""
    ev = _ev_with_participants(["p-anyone"], description="kept")
    # Default — caller didn't pass profiles_map at all.
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map())
    assert body is not None
    assert body["description"] == "kept"


def test_footer_alone_when_description_is_none():
    profiles = {"p-anna": "Anna", "p-bob": "Bob"}
    ev = _ev_with_participants(["p-anna", "p-bob"], description=None)
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "👥 Beteiligt: Anna, Bob"


def test_footer_alone_when_description_is_empty_string():
    profiles = {"p-anna": "Anna", "p-bob": "Bob"}
    ev = _ev_with_participants(["p-anna", "p-bob"], description="")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "👥 Beteiligt: Anna, Bob"


def test_footer_names_are_sorted_case_insensitively():
    """Anna < bob < Carla — capitalisation must not influence ordering."""
    profiles = {"p1": "Carla", "p2": "bob", "p3": "Anna"}
    ev = _ev_with_participants(["p1", "p2", "p3"], description=None)
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "👥 Beteiligt: Anna, bob, Carla"


def test_footer_omitted_when_all_uuids_unknown():
    profiles = {"p-someone-else": "Someone"}
    ev = _ev_with_participants(["p-x", "p-y"], description="lonely")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "lonely"
    assert "Beteiligt" not in body["description"]


def test_footer_separator_is_double_newline():
    """Locked-down format: two \\n between description and footer."""
    profiles = {"p-anna": "Anna"}
    ev = _ev_with_participants(["p-anna"], description="abc")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    # Exact byte sequence
    assert "abc\n\n👥 Beteiligt: Anna" == body["description"]


def test_footer_does_not_change_extended_properties():
    """The original UUIDs MUST still go into extendedProperties for diagnostics."""
    profiles = {"p-anna": "Anna", "p-bob": "Bob"}
    ev = _ev_with_participants(["p-anna", "p-bob"], description="x")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    raw = body["extendedProperties"]["private"]["daely_additional_participants"]
    assert json.loads(raw) == ["p-anna", "p-bob"]


def test_footer_omitted_when_event_has_no_additional_participants():
    profiles = {"p-anna": "Anna"}
    ev = _ev_with_participants([], description="alone")
    body = daely_event_to_google(ev, _make_internal_calendar(), _profile_map(),
                                  profiles_map=profiles)
    assert body is not None
    assert body["description"] == "alone"


# ─────────────── Phase 3f — colorId + emoji-prefix (Hybrid C) ───────────────

# Distinct hex codes that land in distinct Google colorIds:
#   #d50000 → 11 (Tomato, 🔴)
#   #039be5 →  7 (Peacock, 🔵)
#   #f6bf26 →  5 (Banana, 🟡)
#   #0b8043 → 10 (Basil, 🟢)


def _profiles_full(*pairs: tuple[str, str, str | None, int | None]) -> dict[str, Profile]:
    """Helper: build a {uuid: Profile} dict from tuples (uuid, name, hex, sortOrder)."""
    return {
        uuid: Profile(id=uuid, name=name, colorCode=hex_, sortOrder=so)
        for uuid, name, hex_, so in pairs
    }


def test_colors_disabled_by_default_no_colorId_no_prefix():
    """apply_colors=False (the default) leaves summary + body untouched."""
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))
    ev = _ev_with_participants(["p-anna"], description="x")
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(), profiles_map=profiles,
    )
    assert body is not None
    assert "colorId" not in body
    assert body["summary"] == ev.title  # no emoji prefix


def test_single_participant_sets_colorId_no_emoji_prefix():
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))
    ev = _ev_with_participants(["p-anna"], description="x")
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["colorId"] == "11"  # Tomato (red)
    assert body["summary"] == ev.title  # 1 participant → no prefix


def test_two_participants_sets_emoji_prefix_in_alphabetical_order():
    profiles = _profiles_full(
        ("p-bob", "Bob", "#039be5", 1),    # Peacock blue → 🔵
        ("p-anna", "Anna", "#d50000", 0),  # Tomato red → 🔴
    )
    # Listed bob-first to confirm prefix ordering follows alpha-sorted names,
    # not participants-list order.
    ev = _ev_with_participants(["p-bob", "p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    # Anna (red) comes before Bob (blue) alphabetically.
    assert body["summary"] == "🔴🔵 " + ev.title
    # Main profile = sortOrder=0 (Anna) → red colorId.
    assert body["colorId"] == "11"
    # Footer order matches prefix order.
    assert body["description"] == "👥 Beteiligt: Anna, Bob"


def test_three_participants_emoji_count_matches():
    profiles = _profiles_full(
        ("p-anna", "Anna", "#d50000", 0),  # 🔴
        ("p-bob", "Bob", "#039be5", 1),    # 🔵
        ("p-carla", "Carla", "#f6bf26", 2),  # 🟡
    )
    ev = _ev_with_participants(["p-anna", "p-bob", "p-carla"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["summary"] == "🔴🔵🟡 " + ev.title


def test_override_beats_auto_for_main_profile_color():
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))  # auto = 11
    ev = _ev_with_participants(["p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
        color_overrides={"p-anna": "5"},  # override → Banana
    )
    assert body is not None
    assert body["colorId"] == "5"


def test_override_changes_emoji_prefix_too():
    profiles = _profiles_full(
        ("p-anna", "Anna", "#d50000", 0),  # auto → 11 (🔴)
        ("p-bob", "Bob", "#039be5", 1),    # auto → 7 (🔵)
    )
    ev = _ev_with_participants(["p-anna", "p-bob"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
        color_overrides={"p-anna": "5"},  # Anna → Banana 🟡
    )
    assert body is not None
    assert body["summary"] == "🟡🔵 " + ev.title
    assert body["colorId"] == "5"  # Anna (sortOrder=0) is main, override wins


def test_main_profile_picked_by_lowest_sortOrder():
    """sortOrder=0 (account owner) wins regardless of participant-list position."""
    profiles = _profiles_full(
        ("p-bob", "Bob", "#039be5", 5),     # higher sortOrder
        ("p-anna", "Anna", "#d50000", 0),   # account owner
    )
    # bob listed first
    ev = _ev_with_participants(["p-bob", "p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["colorId"] == "11"  # Anna's red, not Bob's blue


def test_main_profile_tie_broken_by_participant_order():
    """Equal sortOrder → first in additionalParticipants wins."""
    profiles = _profiles_full(
        ("p-bob", "Bob", "#039be5", 0),
        ("p-anna", "Anna", "#d50000", 0),
    )
    ev_bob_first = _ev_with_participants(["p-bob", "p-anna"], description=None)
    ev_anna_first = _ev_with_participants(["p-anna", "p-bob"], description=None)
    body_bob = daely_event_to_google(
        ev_bob_first, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    body_anna = daely_event_to_google(
        ev_anna_first, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body_bob["colorId"] == "7"   # Bob (blue) first
    assert body_anna["colorId"] == "11"  # Anna (red) first


def test_no_colorId_when_main_profile_has_no_colorCode_and_no_override():
    profiles = _profiles_full(("p-anna", "Anna", None, 0))
    ev = _ev_with_participants(["p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert "colorId" not in body


def test_override_works_even_without_colorCode():
    profiles = _profiles_full(("p-anna", "Anna", None, 0))
    ev = _ev_with_participants(["p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
        color_overrides={"p-anna": "3"},
    )
    assert body is not None
    assert body["colorId"] == "3"


def test_unknown_participant_uuids_dont_crash_color_path():
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))
    ev = _ev_with_participants(["p-unknown", "p-anna"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["colorId"] == "11"  # only Anna is known
    assert body["summary"] == ev.title  # only 1 known participant → no prefix


def test_emoji_prefix_only_when_two_or_more_resolvable():
    """One known + several unknown → no prefix (since prefix needs ≥2 emojis)."""
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))
    ev = _ev_with_participants(["p-anna", "p-x", "p-y", "p-z"], description=None)
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["summary"] == ev.title  # no prefix


def test_legacy_string_profiles_map_still_supported_no_color_data():
    """Backward compat: passing {uuid: name} works for footer but yields no color."""
    profiles = {"p-anna": "Anna", "p-bob": "Bob"}
    ev = _ev_with_participants(["p-anna", "p-bob"], description="x")
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    # Footer still works
    assert body["description"] == "x\n\n👥 Beteiligt: Anna, Bob"
    # No colorCode info → no colorId, no emoji prefix
    assert "colorId" not in body
    assert body["summary"] == ev.title


def test_apply_colors_false_with_full_profiles_still_no_color():
    profiles = _profiles_full(
        ("p-anna", "Anna", "#d50000", 0),
        ("p-bob", "Bob", "#039be5", 1),
    )
    ev = _ev_with_participants(["p-anna", "p-bob"], description="x")
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles,  # apply_colors omitted → False
    )
    assert body is not None
    assert "colorId" not in body
    assert body["summary"] == ev.title
    # Footer remains unaffected by the toggle
    assert body["description"] == "x\n\n👥 Beteiligt: Anna, Bob"


def test_event_customColorCode_does_NOT_affect_google_colorId():
    """Per-event colorCode in Daely stays in extProp; profile-color drives Google."""
    profiles = _profiles_full(("p-anna", "Anna", "#d50000", 0))  # → 11 Tomato
    ev = CalendarEvent.model_validate({
        "id": "evt-cc", "recurringId": None, "deleted": False,
        "title": "x", "description": None, "location": None,
        "start": {"dateTime": "2026-05-08T15:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "end":   {"dateTime": "2026-05-08T16:00:00+02:00", "timeZone": "Europe/Berlin", "date": None},
        "created": "2026-04-27T18:37:31+00:00", "updated": "2026-04-27T18:37:31+00:00",
        "recurrence": [], "reminders": [],
        "customColorCode": "#0b8043",  # would map to Basil (10) if we honoured it
        "additionalParticipants": ["p-anna"],
        "editable": True, "hasError": False, "privateEvent": False,
    })
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        profiles_map=profiles, apply_colors=True,
    )
    assert body is not None
    assert body["colorId"] == "11"  # profile, not event-customColorCode
    # extProp still mirrors the raw event override for diagnostics.
    assert body["extendedProperties"]["private"]["daely_custom_color"] == "#0b8043"


# ─────────────── §3.1 — recurring-instance deletions (EXDATE synthesis) ───────────────

def _recurring_instance(
    *,
    recurring_id: str,
    start_iso: str,
    rrule: str = "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH",
    tz: str = "Europe/Berlin",
    instance_id: str | None = None,
) -> CalendarEvent:
    """Build one instance of a recurring series.

    Mirrors the live-data shape: every instance carries the same recurringId
    and the same `recurrence` RRULE; the composite id encodes the start.
    """
    return CalendarEvent.model_validate({
        "id": instance_id or f"{recurring_id}_{start_iso.replace(':', '').replace('-', '')}",
        "recurringId": recurring_id,
        "deleted": False,
        "title": "Recurring sample",
        "description": "",
        "location": None,
        "start": {"dateTime": start_iso, "timeZone": tz, "date": None},
        "end": {"dateTime": start_iso, "timeZone": tz, "date": None},
        "created": "2026-04-27T15:03:07+00:00",
        "updated": "2026-04-27T15:03:07+00:00",
        "recurrence": [rrule],
        "reminders": [], "customColorCode": None, "additionalParticipants": [],
        "editable": True, "hasError": False, "privateEvent": False,
    })


def test_compute_exdates_clean_series_returns_empty():
    """A series with no gaps yields no EXDATEs."""
    rid = "00000000-0000-0000-0005-0000000000a1"
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00")
        for d in (7, 14, 21, 28)  # four consecutive Thursdays
    ]
    assert compute_series_exdates(instances) == []


def test_compute_exdates_single_gap():
    """One missing Thursday in the middle → one EXDATE with matching time."""
    rid = "00000000-0000-0000-0005-0000000000a2"
    # 05-07, [05-14 missing], 05-21, 05-28
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00")
        for d in (7, 21, 28)
    ]
    exdates = compute_series_exdates(instances)
    assert exdates == ["EXDATE;TZID=Europe/Berlin:20260514T155000"]


def test_compute_exdates_multiple_gaps():
    """Two missing Thursdays → two EXDATEs, sorted."""
    rid = "00000000-0000-0000-0005-0000000000a3"
    # 05-07, [05-14, 05-21 missing], 05-28
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00")
        for d in (7, 28)
    ]
    exdates = compute_series_exdates(instances)
    assert exdates == [
        "EXDATE;TZID=Europe/Berlin:20260514T155000",
        "EXDATE;TZID=Europe/Berlin:20260521T155000",
    ]


def test_compute_exdates_preserves_instance_time_of_day():
    """EXDATE time component must match the series' wall-clock time exactly,
    or Google won't match the occurrence."""
    rid = "00000000-0000-0000-0005-0000000000a4"
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T13:45:00+02:00")
        for d in (7, 21)  # 05-14 missing
    ]
    exdates = compute_series_exdates(instances)
    assert exdates == ["EXDATE;TZID=Europe/Berlin:20260514T134500"]


def test_compute_exdates_dst_boundary_is_wall_clock_safe():
    """Across the spring DST change (last Sunday of March in Europe/Berlin),
    a weekly series stays at the same wall-clock time. EXDATE must use wall
    time, not a shifted UTC instant."""
    rid = "00000000-0000-0000-0005-0000000000a5"
    # Weekly Sundays around the 2026-03-29 DST switch (+01:00 → +02:00).
    # 03-22 is +01:00, 04-05 is +02:00; 03-29 (the switch day) is "missing".
    instances = [
        _recurring_instance(
            recurring_id=rid,
            start_iso="2026-03-22T10:00:00+01:00",
            rrule="RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU",
        ),
        _recurring_instance(
            recurring_id=rid,
            start_iso="2026-04-05T10:00:00+02:00",
            rrule="RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU",
        ),
    ]
    exdates = compute_series_exdates(instances)
    # 03-29 missing, at wall-clock 10:00 regardless of the offset shift
    assert exdates == ["EXDATE;TZID=Europe/Berlin:20260329T100000"]


def test_compute_exdates_too_few_instances_returns_empty():
    rid = "00000000-0000-0000-0005-0000000000a6"
    single = [_recurring_instance(recurring_id=rid, start_iso="2026-05-07T15:50:00+02:00")]
    assert compute_series_exdates(single) == []
    assert compute_series_exdates([]) == []


def test_compute_exdates_no_rrule_returns_empty():
    """An instance group with no RRULE on the earliest entry → no EXDATEs."""
    rid = "00000000-0000-0000-0005-0000000000a7"
    instances = [
        _recurring_instance(recurring_id=rid, start_iso="2026-05-07T15:50:00+02:00", rrule=""),
        _recurring_instance(recurring_id=rid, start_iso="2026-05-28T15:50:00+02:00", rrule=""),
    ]
    # rrule="" → recurrence list has one empty string, filtered out
    assert compute_series_exdates(instances) == []


def test_compute_exdates_until_clause_is_stripped_safely():
    """RRULEs with a UTC UNTIL (…Z) must not crash the naive expansion."""
    rid = "00000000-0000-0000-0005-0000000000a8"
    rrule = "RRULE:FREQ=WEEKLY;UNTIL=20260625T235900Z;INTERVAL=1;BYDAY=TH"
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00", rrule=rrule)
        for d in (7, 28)  # 05-14, 05-21 missing
    ]
    exdates = compute_series_exdates(instances)
    assert exdates == [
        "EXDATE;TZID=Europe/Berlin:20260514T155000",
        "EXDATE;TZID=Europe/Berlin:20260521T155000",
    ]


def test_compute_exdates_malformed_rrule_returns_empty():
    rid = "00000000-0000-0000-0005-0000000000a9"
    instances = [
        _recurring_instance(recurring_id=rid, start_iso="2026-05-07T15:50:00+02:00",
                            rrule="RRULE:THIS-IS-NOT-VALID"),
        _recurring_instance(recurring_id=rid, start_iso="2026-05-28T15:50:00+02:00",
                            rrule="RRULE:THIS-IS-NOT-VALID"),
    ]
    # Defensive: don't guess on a malformed rule
    assert compute_series_exdates(instances) == []


def test_compute_exdates_leading_deletion_undetectable():
    """Documented limitation: a deleted FIRST occurrence has no surviving
    neighbour before it — the observed range just starts later, so it's
    indistinguishable from a series that legitimately starts then."""
    rid = "00000000-0000-0000-0005-0000000000aa"
    # True series would be 05-07, 05-14, 05-21 — but 05-07 was deleted.
    # We only see 05-14, 05-21 → no gap detected.
    instances = [
        _recurring_instance(recurring_id=rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00")
        for d in (14, 21)
    ]
    assert compute_series_exdates(instances) == []


def test_exdates_by_recurring_id_groups_and_filters():
    """exdates_by_recurring_id only returns series that actually have gaps."""
    clean_rid = "00000000-0000-0000-0005-0000000000b1"
    gap_rid = "00000000-0000-0000-0005-0000000000b2"
    events = []
    # clean series — 3 consecutive Thursdays
    events += [
        _recurring_instance(recurring_id=clean_rid, start_iso=f"2026-05-{d:02d}T15:50:00+02:00")
        for d in (7, 14, 21)
    ]
    # gapped series — 05-14 missing
    events += [
        _recurring_instance(recurring_id=gap_rid, start_iso=f"2026-05-{d:02d}T09:00:00+02:00")
        for d in (7, 21)
    ]
    # a non-recurring event in the mix — must be ignored
    events.append(_ev_with_participants([], description="single event"))

    result = exdates_by_recurring_id(events)
    assert set(result.keys()) == {gap_rid}  # clean series not present
    assert result[gap_rid] == ["EXDATE;TZID=Europe/Berlin:20260514T090000"]


def test_mapper_appends_exdates_to_recurrence():
    """daely_event_to_google appends recurrence_exdates to body['recurrence']."""
    master = _recurring_instance(
        recurring_id="00000000-0000-0000-0005-0000000000c1",
        start_iso="2026-05-07T15:50:00+02:00",
    )
    exdates = ["EXDATE;TZID=Europe/Berlin:20260514T155000"]
    body = daely_event_to_google(
        master, _make_internal_calendar(), _profile_map(),
        recurrence_exdates=exdates,
    )
    assert body is not None
    assert body["recurrence"] == [
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH",
        "EXDATE;TZID=Europe/Berlin:20260514T155000",
    ]


def test_mapper_no_exdates_leaves_recurrence_untouched():
    master = _recurring_instance(
        recurring_id="00000000-0000-0000-0005-0000000000c2",
        start_iso="2026-05-07T15:50:00+02:00",
    )
    body = daely_event_to_google(
        master, _make_internal_calendar(), _profile_map(),
        recurrence_exdates=None,
    )
    assert body is not None
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"]


def test_mapper_exdates_ignored_for_non_recurring_event():
    """recurrence_exdates on a non-recurring event is a no-op (no recurrence key)."""
    ev = _ev_with_participants([], description="not recurring")
    body = daely_event_to_google(
        ev, _make_internal_calendar(), _profile_map(),
        recurrence_exdates=["EXDATE;TZID=Europe/Berlin:20260514T155000"],
    )
    assert body is not None
    assert "recurrence" not in body
