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
- `customColorCode` is NOT mapped to Google `colorId` — by design. Per-event
  user overrides in Daely don't propagate; we preserve the hex in extProp only.
- `privateEvent: true` → Google `visibility: "private"`; otherwise omitted.
- `description=None` AND empty string → field not set in Google body (cleaner UI).
- Server-managed Daely fields (editable, hasError, deleted, created, updated) are
  NOT mapped onto Google fields — Google manages its own equivalents. `hasError`
  is mirrored to extProp so it shows up under operator inspection.
- Profile-footer (Phase 3e): when `profiles_map` is supplied, any profile UUIDs
  in `additionalParticipants` are resolved to display names and appended to
  the event's description as a footer (`👥 Beteiligt: …`). Unknown UUIDs are
  silently dropped. Names are sorted case-insensitively for deterministic output.
- Profile-color (Phase 3f): when `apply_colors=True` and full Profile objects
  are passed in `profiles_map`, the event gets a Google `colorId` derived from
  the main participant's `colorCode` (nearest-RGB match, overridable via
  `color_overrides`). With ≥2 participants, a sequence of color-emoji is
  prepended to the title (one per participant, in the same alphabetical order
  the footer uses).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from .colors import emoji_for_color_id, nearest_color_id
from .models import (
    Calendar,
    CalendarEvent,
    Profile,
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


# ─────────────────── recurring-instance deletions (§3.1) ───────────────────

_UNTIL_RE = re.compile(r";UNTIL=[^;]*", re.IGNORECASE)
# Captures the UNTIL value itself (RFC 5545 forms: `…Z` UTC, or floating local).
_UNTIL_VALUE_RE = re.compile(r"UNTIL=(\d{8}T\d{6})(Z?)", re.IGNORECASE)


def _until_to_wall(rrule_line: str, tz_name: str | None) -> datetime | None:
    """Parse the RRULE's UNTIL into a naive wall-clock datetime, or None.

    UNTIL is normally UTC (`…Z`); we convert it into the series' local
    timezone (the same TZID the EXDATEs carry) and drop tzinfo so it lives in
    the same naive wall-clock space as the rest of this function. A single
    point-in-time conversion is DST-correct (zoneinfo picks the right offset
    for that date); we never iterate across a transition in aware time.
    """
    m = _UNTIL_VALUE_RE.search(rrule_line)
    if not m:
        return None
    value, zulu = m.group(1), m.group(2)
    try:
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if zulu and tz_name:
        # UTC instant → local wall-clock.
        return (
            naive.replace(tzinfo=timezone.utc)
            .astimezone(ZoneInfo(tz_name))
            .replace(tzinfo=None)
        )
    # Floating UNTIL, or no source tz to convert into: treat as wall-clock.
    return naive


def compute_series_exdates(
    instances: list[CalendarEvent],
    *,
    window_end: date | None = None,
) -> list[str]:
    """Detect instances deleted from a recurring series, return EXDATE lines.

    Daely expands recurring events server-side, and when the user deletes a
    single occurrence Daely simply *omits* it from the expansion — the master
    RRULE stays unchanged, no `deleted=true` tombstone, no EXDATE
    (confirmed via live read, see findings/06). Google therefore re-expands
    the full RRULE and the deleted occurrence stays visible.

    This function takes ALL fetched instances of one series, expands the RRULE,
    diffs against the dates Daely actually returned, and emits `EXDATE;TZID=…`
    lines for the gaps.

    Expansion upper bound:
      - For an **open-ended** series we can only diff up to the last returned
        instance — past that we can't tell a deleted trailing occurrence from
        the simple edge of the fetch window.
      - For a series with an explicit **UNTIL** we know the real end, so we
        expand all the way to it (capped by `window_end` so we never EXDATE
        occurrences Daely was never asked about). This catches a deleted
        *last* occurrence, which otherwise leaves the observed range ending
        early and stays visible in Google.

    Works in naive wall-clock time so it's DST-safe — Google applies the
    TZID. Returns `[]` when there's no gap, fewer than 2 timed instances,
    or no RRULE.

    Remaining limitation: a deleted *first* occurrence is still undetectable —
    there's no surviving earlier neighbour, so the observed range (and our
    dtstart proxy) simply starts later.
    """
    # Only timed recurring instances — all-day recurring series would need
    # DATE-valued EXDATEs; not observed in live data, handled defensively
    # by the `dateTime is not None` filter (all-day instances are skipped).
    timed = [e for e in instances if e.start.dateTime is not None]
    if len(timed) < 2:
        return []

    def _wall(ev: CalendarEvent):
        # Strip tzinfo → naive wall-clock. DST-safe: the EXDATE carries TZID.
        return ev.start.dateTime.replace(tzinfo=None)

    timed.sort(key=_wall)
    earliest = timed[0]
    tz = earliest.start.timeZone

    rrule_lines = [
        r for r in (earliest.recurrence or [])
        if r.upper().startswith("RRULE")
    ]
    if not rrule_lines:
        return []

    # Strip UNTIL from the rule we feed dateutil — UNTIL is usually UTC ("…Z")
    # and would clash with our naive dtstart. We re-derive the bound below.
    rrule_clean = _UNTIL_RE.sub("", rrule_lines[0])

    dtstart = _wall(earliest)
    latest = _wall(timed[-1])

    # Default: only diff within the observed range. We can extend past the
    # last returned instance ONLY when we know two things: the series' real
    # end (an explicit UNTIL) AND the fetch window (so we never expand past
    # what Daely was queried for — within the window, "expected but absent"
    # means deleted). With both, a deleted *last* occurrence becomes visible
    # as a gap; without them we'd be guessing, so we stay conservative.
    upper = latest
    if window_end is not None:
        until_wall = _until_to_wall(rrule_lines[0], tz)
        if until_wall is not None:
            upper = min(until_wall, datetime.combine(window_end, time.max))
            upper = max(upper, latest)  # never shrink below what we saw

    try:
        rule = rrulestr(rrule_clean, dtstart=dtstart)
        expected = set(rule.between(dtstart, upper, inc=True))
    except (ValueError, TypeError):
        # Malformed RRULE — don't guess, just emit nothing.
        return []

    actual = {_wall(e) for e in timed}
    missing = sorted(expected - actual)
    if not missing:
        return []

    exdates: list[str] = []
    for dt in missing:
        stamp = dt.strftime("%Y%m%dT%H%M%S")
        if tz:
            exdates.append(f"EXDATE;TZID={tz}:{stamp}")
        else:
            # No timezone on the source event — emit a floating EXDATE.
            exdates.append(f"EXDATE:{stamp}")
    return exdates


def exdates_by_recurring_id(
    events: list[CalendarEvent],
    *,
    window_end: date | None = None,
) -> dict[str, list[str]]:
    """Group `events` by `recurringId` and compute EXDATEs for each series.

    Returns `{recurringId: [exdate_line, …]}` — only series with at least
    one detected gap appear in the dict. Non-recurring events are ignored.

    `window_end` is the sync's fetch end date; it caps how far a finite
    (UNTIL-bounded) series is expanded so we never EXDATE occurrences beyond
    what Daely was queried for. See `compute_series_exdates`.
    """
    by_series: dict[str, list[CalendarEvent]] = {}
    for ev in events:
        if ev.recurringId is None:
            continue
        by_series.setdefault(ev.recurringId, []).append(ev)

    out: dict[str, list[str]] = {}
    for rid, instances in by_series.items():
        exdates = compute_series_exdates(instances, window_end=window_end)
        if exdates:
            out[rid] = exdates
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


def _normalize_profiles_map(
    profiles_map: dict[str, str] | dict[str, Profile] | None,
) -> dict[str, Profile]:
    """Accept either {uuid: name} (legacy) or {uuid: Profile} (full).

    Returns a uniform {uuid: Profile} dict so downstream helpers can reach
    `colorCode`/`sortOrder` when present and degrade gracefully when not.
    """
    if not profiles_map:
        return {}
    out: dict[str, Profile] = {}
    for uuid, val in profiles_map.items():
        if isinstance(val, Profile):
            out[uuid] = val
        elif isinstance(val, str):
            out[uuid] = Profile(id=uuid, name=val)
        # silently ignore unexpected value types — defensive for legacy callers
    return out


def _build_profile_footer(
    additional_participants: list[str],
    profiles_norm: dict[str, Profile],
) -> str | None:
    """Resolve UUIDs to names; return formatted footer or None.

    Unknown UUIDs and empty/None names are silently dropped. Names are sorted
    case-insensitively for deterministic output. Returns None if no name could
    be resolved.
    """
    if not profiles_norm or not additional_participants:
        return None
    names: list[str] = []
    for uuid in additional_participants:
        prof = profiles_norm.get(uuid)
        if prof and prof.name:
            names.append(prof.name)
    if not names:
        return None
    names.sort(key=str.casefold)
    return PROFILE_FOOTER_PREFIX + ", ".join(names)


def _pick_main_profile(
    additional_participants: list[str],
    profiles_norm: dict[str, Profile],
) -> Profile | None:
    """Pick the dominant participant for single-color decisions.

    Strategy: lowest sortOrder wins (account-owner is sortOrder=0 in Daely);
    ties resolve by participant-list order. Profiles unknown to `profiles_norm`
    are ignored entirely.
    """
    candidates: list[tuple[int, int, Profile]] = []
    for idx, uuid in enumerate(additional_participants):
        prof = profiles_norm.get(uuid)
        if prof is None:
            continue
        sort_order = prof.sortOrder if prof.sortOrder is not None else 1_000_000
        candidates.append((sort_order, idx, prof))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][2]


def _resolve_color_id(
    profile: Profile | None,
    color_overrides: dict[str, str] | None,
) -> str | None:
    """Override beats auto. Returns None if neither a valid override nor a
    parseable colorCode is available."""
    if profile is None:
        return None
    if color_overrides:
        override = color_overrides.get(profile.id)
        if override is not None:
            return override
    return nearest_color_id(profile.colorCode)


def _build_emoji_prefix(
    additional_participants: list[str],
    profiles_norm: dict[str, Profile],
    color_overrides: dict[str, str] | None,
) -> str | None:
    """Multi-participant title-prefix. Order mirrors the footer
    (alphabetical, case-insensitive). Returns None unless ≥2 participants
    can be resolved to a colorId.
    """
    known: list[Profile] = []
    seen: set[str] = set()
    for uuid in additional_participants:
        prof = profiles_norm.get(uuid)
        if prof is None or not prof.name or prof.id in seen:
            continue
        known.append(prof)
        seen.add(prof.id)
    if len(known) < 2:
        return None
    known.sort(key=lambda p: p.name.casefold())
    emojis: list[str] = []
    for p in known:
        cid = _resolve_color_id(p, color_overrides)
        em = emoji_for_color_id(cid)
        if em:
            emojis.append(em)
    if len(emojis) < 2:
        return None
    return "".join(emojis) + " "


def daely_event_to_google(
    event: CalendarEvent,
    calendar: Calendar,
    profile_calendar_map: dict[str, str],  # noqa: ARG001  unused but part of API for future
    profiles_map: dict[str, str] | dict[str, Profile] | None = None,
    *,
    apply_colors: bool = False,
    color_overrides: dict[str, str] | None = None,
    recurrence_exdates: list[str] | None = None,
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

    `recurrence_exdates` (§3.1): EXDATE lines computed by
    `compute_series_exdates()` for this event's series — appended to
    `body["recurrence"]` so Google omits the occurrences the user deleted in
    Daely. Only applied when the event is itself recurring (`event.recurrence`
    non-empty). Ignored otherwise.
    """
    if should_skip_calendar(calendar):
        return None

    profiles_norm = _normalize_profiles_map(profiles_map)

    summary = event.title
    if apply_colors:
        prefix = _build_emoji_prefix(
            event.additionalParticipants, profiles_norm, color_overrides,
        )
        if prefix:
            summary = prefix + event.title

    body: dict[str, Any] = {
        "summary": summary,
        "start": _start_end_to_google(event.start),
        "end": _start_end_to_google(event.end),
        "iCalUID": f"{master_uuid(event)}@daely-google-bridge",
        "extendedProperties": {
            "private": _extended_private(event, calendar),
        },
        "reminders": _reminders_to_google(event.reminders),
    }

    if apply_colors:
        main_profile = _pick_main_profile(event.additionalParticipants, profiles_norm)
        color_id = _resolve_color_id(main_profile, color_overrides)
        if color_id is not None:
            body["colorId"] = color_id

    footer = _build_profile_footer(event.additionalParticipants, profiles_norm)
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
        recurrence = list(event.recurrence)
        # §3.1: append synthesized EXDATE lines for occurrences the user
        # deleted in Daely (Daely drops them from the expansion but never
        # touches the master RRULE — so without this, Google re-shows them).
        if recurrence_exdates:
            recurrence.extend(recurrence_exdates)
        body["recurrence"] = recurrence
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
    "compute_series_exdates",
    "daely_event_to_google",
    "deduplicate_recurring",
    "exdates_by_recurring_id",
    "is_recurring_instance_skip",
    "select_target_calendar_id",
    "should_skip_calendar",
]
