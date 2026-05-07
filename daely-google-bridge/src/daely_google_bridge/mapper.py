"""Pure functions: Daely event → Google Calendar event dict.

No I/O, no logging, no global state. Easy to unit-test against fixture JSON.

Mapping decisions (see findings/05_EVENT_MODEL.md, 06_BRIDGE_ARCHITECTURE.md,
07_LIVE_READ_RESULTS.md, 08_PROFILES.md):
- Filter: events from calendars with calendarType != 0 are skipped (those are
  externally-synced calendars; we don't push them to Google to avoid loops).
- Recurring: master-only — `deduplicate_recurring()` keeps the earliest instance
  of each series. Subsequent instances are dropped. Google expands the RRULE.
- Daely-only fields (id, recurringId, calendarId, profileId, additionalParticipants,
  customColorCode, privateEvent, hasError) are mirrored into
  `extendedProperties.private` for diagnostics. The bridge never reads them back.
- `customColorCode` (#RRGGBB) is NOT mapped to Google `colorId` (1–11 enum) in
  the MVP — colour conversion would be lossy. We just preserve the hex in extProp.
- `privateEvent: true` → Google `visibility: "private"`; otherwise omitted.
- `description=None` AND empty string → field not set in Google body (cleaner UI).
- Server-managed Daely fields (editable, hasError, deleted, created, updated) are
  NOT mapped onto Google fields — Google manages its own equivalents. `hasError`
  is mirrored to extProp so it shows up under operator inspection.
- Profile-footer (Phase 3e): when `profiles_map` is supplied, any profile UUIDs
  in `additionalParticipants` are resolved to display names and appended to
  the event's description as a footer (`👥 Beteiligt: …`). Unknown UUIDs are
  silently dropped. Names are sorted case-insensitively for deterministic output.
"""
from __future__ import annotations

import json
from typing import Any

from .models import (
    Calendar,
    CalendarEvent,
    is_all_day,
    master_uuid,
)


# ─────────────────── filter / dedup ───────────────────

def should_skip_calendar(calendar: Calendar) -> bool:
    """Skip non-internal calendars (calendarType != 0).

    External-account calendars (Google/Apple/MS-synced) are already two-way-synced
    via Daely's own integration; pushing them to Google again would create loops
    or duplicates.
    """
    return calendar.calendarType != 0


def is_recurring_instance_skip(event: CalendarEvent) -> bool:
    """A recurring instance — drop it in favour of the master."""
    return event.recurringId is not None


def _instance_sort_key(event: CalendarEvent):
    """Sort recurring instances by their concrete start. Earlier first."""
    if event.start.dateTime is not None:
        return event.start.dateTime
    if event.start.date is not None:
        return event.start.date
    # Should not happen, but stay deterministic.
    return event.created


def deduplicate_recurring(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Keep one entry per recurring series (earliest start), pass non-recurring through.

    Non-recurring events keep their original list-order. Recurring entries are
    grouped by `recurringId`, sorted by start, and only the earliest is kept.
    The earliest instance is then placed at the position of the FIRST seen
    occurrence of that series in the input order — preserving overall ordering
    intent of the caller.
    """
    # First pass: group recurring instances by master id, find earliest each.
    earliest_by_master: dict[str, CalendarEvent] = {}
    first_index: dict[str, int] = {}
    for idx, ev in enumerate(events):
        if ev.recurringId is None:
            continue
        if ev.recurringId not in first_index:
            first_index[ev.recurringId] = idx
        cur = earliest_by_master.get(ev.recurringId)
        if cur is None or _instance_sort_key(ev) < _instance_sort_key(cur):
            earliest_by_master[ev.recurringId] = ev

    # Second pass: emit non-recurring as-is; emit each master once at first-seen index.
    out: list[CalendarEvent] = []
    emitted_masters: set[str] = set()
    for idx, ev in enumerate(events):
        if ev.recurringId is None:
            out.append(ev)
        elif ev.recurringId not in emitted_masters and first_index[ev.recurringId] == idx:
            out.append(earliest_by_master[ev.recurringId])
            emitted_masters.add(ev.recurringId)
    return out


# ─────────────────── per-event mapping ───────────────────

def _start_end_to_google(se) -> dict[str, Any]:
    """Convert StartEnd → Google start/end (`date` XOR `dateTime`)."""
    if se.dateTime is not None:
        out: dict[str, Any] = {"dateTime": se.dateTime.isoformat()}
        if se.timeZone:
            out["timeZone"] = se.timeZone
        return out
    if se.date is not None:
        # All-day: only `date`, no `timeZone` for true all-day per Google.
        return {"date": se.date.isoformat()}
    raise ValueError("StartEnd has neither dateTime nor date set")


def _reminders_to_google(minutes_list: list[int]) -> dict[str, Any]:
    if not minutes_list:
        return {"useDefault": True}
    return {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": int(m)} for m in minutes_list
        ],
    }


def _extended_private(event: CalendarEvent, calendar: Calendar) -> dict[str, str]:
    private = {
        "daely_id": event.id,
        "daely_recurring_id": event.recurringId or "",
        "daely_calendar_id": calendar.id,
        "daely_profile_id": calendar.profileId or "",
        "daely_has_error": str(event.hasError).lower(),
        "daely_private_event": str(event.privateEvent).lower(),
    }
    if event.additionalParticipants:
        private["daely_additional_participants"] = json.dumps(event.additionalParticipants)
    if event.customColorCode:
        private["daely_custom_color"] = event.customColorCode
    return private


PROFILE_FOOTER_SEPARATOR = "\n\n"
PROFILE_FOOTER_PREFIX = "👥 Beteiligt: "


def _build_profile_footer(
    additional_participants: list[str],
    profiles_map: dict[str, str] | None,
) -> str | None:
    """Resolve UUIDs to names; return formatted footer or None.

    Unknown UUIDs and empty/None names are silently dropped. Names are sorted
    case-insensitively for deterministic output. Returns None if no name could
    be resolved.
    """
    if not profiles_map or not additional_participants:
        return None
    names: list[str] = []
    for uuid in additional_participants:
        name = profiles_map.get(uuid)
        if name:  # also drops empty strings
            names.append(name)
    if not names:
        return None
    names.sort(key=str.casefold)
    return PROFILE_FOOTER_PREFIX + ", ".join(names)


def daely_event_to_google(
    event: CalendarEvent,
    calendar: Calendar,
    profile_calendar_map: dict[str, str],  # noqa: ARG001  unused but part of API for future
    profiles_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Convert a Daely event to a Google Calendar event body dict.

    Contract: the caller MUST have run `deduplicate_recurring()` over the input
    list before calling this — otherwise N entries of the same recurring series
    would each produce a Google body with the same iCalUID, leading to either
    upstream errors (events.import) or duplicate creation (events.insert).

    Returns:
      - `None` if the calendar's `calendarType != 0` (filter rule).
      - Otherwise: a dict suitable as `body=` in `events.insert()`.

    The caller is responsible for resolving the target Google calendar via
    `select_target_calendar_id(calendar.profileId, profile_calendar_map)` and
    passing it as `calendarId=` in the Google API call.

    `profile_calendar_map` is currently only used for callers' validation; kept
    in the signature so future enhancements (per-event target override) don't
    require an API change.
    """
    if should_skip_calendar(calendar):
        return None

    body: dict[str, Any] = {
        "summary": event.title,
        "start": _start_end_to_google(event.start),
        "end": _start_end_to_google(event.end),
        "iCalUID": f"{master_uuid(event)}@daely-google-bridge",
        "extendedProperties": {
            "private": _extended_private(event, calendar),
        },
        "reminders": _reminders_to_google(event.reminders),
    }
    footer = _build_profile_footer(event.additionalParticipants, profiles_map)
    if footer is not None:
        if event.description:
            body["description"] = event.description + PROFILE_FOOTER_SEPARATOR + footer
        else:
            body["description"] = footer
    elif event.description:
        body["description"] = event.description
    if event.location:
        body["location"] = event.location
    if event.recurrence:
        body["recurrence"] = list(event.recurrence)
    if event.privateEvent:
        body["visibility"] = "private"
    return body


def select_target_calendar_id(
    daely_calendar_profile_id: str | None,
    profile_to_calendar: dict[str, str],
    *,
    fallback_calendar_id: str | None = None,
) -> str:
    """Pick the Google calendar for an event based on its Daely calendar's profileId."""
    if daely_calendar_profile_id and daely_calendar_profile_id in profile_to_calendar:
        return profile_to_calendar[daely_calendar_profile_id]
    if fallback_calendar_id:
        return fallback_calendar_id
    raise KeyError(
        f"no Google calendar configured for Daely profile {daely_calendar_profile_id!r} "
        f"and no fallback calendar set"
    )


__all__ = [
    "daely_event_to_google",
    "deduplicate_recurring",
    "is_recurring_instance_skip",
    "select_target_calendar_id",
    "should_skip_calendar",
]
