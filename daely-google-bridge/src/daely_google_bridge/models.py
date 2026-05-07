"""Pydantic models for Daely API responses.

All models use `extra="ignore"` so unexpected new fields from Daely don't break us.
Field names match the wire-format exactly — including the typo `writeable` (sic).

Wire-enum integer values were derived empirically from live data, NOT from the
blutter Dart-index disassembly (which mismatched). Treat them as authoritative
only for the values observed; new values may surface and need extending.
"""
from datetime import date as Date  # noqa: N812 — avoid collision with field name `date`
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ────────── enums (as int wire-values, with friendly literal aliases) ──────────

# Observed wire values; expand if more surface during operation.
AccountTypeWire = Literal[1]  # 1 = google (only one observed in live data)
CalendarTypeWire = Literal[0, 1]  # 0 = internal, 1 = google
ShareTypeWire = Literal[0, 1, 2, 3]  # confirmed only `2` (twoWay); others by analogy
PresentationTypeWire = Literal[0, 1]  # 0 = allEvents, 1 = timeWindow


# ─────────────────── shared base ───────────────────

class _DaelyModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",          # tolerate unknown fields silently
        populate_by_name=True,
        str_strip_whitespace=False,
    )


# ─────────────────── account / user ───────────────────

class UserMe(_DaelyModel):
    id: str
    email: str
    firstName: str
    lastName: str
    locale: str | None = None
    imageUrl: str | None = None


class Group(_DaelyModel):
    """One entry from `GET /api/groups/me`."""
    id: str
    name: str
    setupComplete: bool
    noDeviceSince: datetime | None = None


class Profile(_DaelyModel):
    """One entry from `GET /api/groups/<gid>/profiles`.

    Fields beyond `id` and `name` are optional in our usage and tolerated
    via `extra="ignore"` — we only need id+name for the description footer.
    """
    id: str
    name: str
    colorCode: str | None = None
    groupId: str | None = None
    userId: str | None = None
    imageUrl: str | None = None
    sortOrder: int | None = None


# Backwards-compatible alias (older docs called this GroupSummary).
GroupSummary = Group


class SyncTokenPair(_DaelyModel):
    """Sync-Token-Tupel auf Calendar.{internal,external}SyncToken.

    Pure container — used when persisting/restoring sync state. The wire format
    actually splits these into two top-level Calendar fields, so this struct
    is just a convenience for the sync layer.
    """
    internal: str | None = None
    external: str | None = None


class ExternalAccount(_DaelyModel):
    id: str
    userId: str
    accountId: str
    accountName: str
    accountType: int  # 1 = google observed; not yet a strict Literal
    hasError: bool
    connectedCalendars: list[str] | None = None


class UrlCalendar(_DaelyModel):
    """Read-side model for `GET /api/url-calendars`. Live data was empty list."""
    internalId: str
    url: str
    title: str
    timezone: str | None = None
    calendarType: int | None = None
    writeable: bool = False
    groupId: str | None = None
    sharedWith: list[dict] = Field(default_factory=list)


# ─────────────────── calendar core ───────────────────

class StartEnd(_DaelyModel):
    """The `start`/`end` shape on every CalendarEvent.

    Either `dateTime` is set (timed event) or `date` is set (all-day).
    `timeZone` is always populated (IANA zone name).
    """
    dateTime: datetime | None = None
    timeZone: str | None = None
    date: Date | None = None


class CalendarEvent(_DaelyModel):
    """Daely calendar event — 17 wire fields confirmed via live read.

    Recurring events arrive expanded into one entry per occurrence.
    The composite ID format `<masterUuid>_<startUTCcompact>` provides a unique
    primary key per occurrence.
    """
    id: str
    recurringId: str | None = None
    deleted: bool
    title: str
    description: str | None = None
    location: str | None = None
    start: StartEnd
    end: StartEnd
    created: datetime
    updated: datetime
    recurrence: list[str] = Field(default_factory=list)  # RFC 5545 RRULE strings
    reminders: list[int] = Field(default_factory=list)   # minutes before event
    customColorCode: str | None = None                    # "#RRGGBB" hex
    additionalParticipants: list[str] = Field(default_factory=list)  # profile UUIDs
    editable: bool
    hasError: bool
    privateEvent: bool


class Calendar(_DaelyModel):
    """Calendar from `/api/groups/<gid>/calendars` (lite, no events)."""
    id: str
    externalId: str | None = None
    title: str
    description: str | None = None
    url: str | None = None
    timeZone: str | None = None
    colorCode: str | None = None
    ownerId: str | None = None
    calendarType: int                  # 0 = internal, 1 = google (empirical)
    shareType: int | None = None       # 2 = twoWay observed
    profileId: str | None = None
    isClassSchedule: bool = False
    writeable: bool = False             # sic: typo from server
    internalSyncToken: str | None = None
    externalSyncToken: str | None = None


class CalendarWithEvents(Calendar):
    """Same as Calendar plus events + window metadata, from `/calendars/with-events`."""
    events: list[CalendarEvent] = Field(default_factory=list)
    hasError: bool = False
    eventsIncluded: bool = True
    presentationType: int | None = None
    startDate: datetime | None = None
    endDate: datetime | None = None


# ─────────────────── handy helpers ───────────────────

def is_recurring_master_or_unique(event: CalendarEvent) -> bool:
    """Return True if this event entry should produce a Google event.

    Master-only strategy: pick the FIRST occurrence of each series and let
    Google expand the RRULE itself. Non-recurring events are always picked.
    """
    return event.recurringId is None


def master_uuid(event: CalendarEvent) -> str:
    """Return the master UUID for a recurring instance, or the event id otherwise."""
    return event.recurringId or event.id


def is_all_day(event: CalendarEvent) -> bool:
    return event.start.dateTime is None and event.start.date is not None


__all__ = [
    "AccountTypeWire",
    "Calendar",
    "CalendarEvent",
    "CalendarTypeWire",
    "CalendarWithEvents",
    "ExternalAccount",
    "Group",
    "GroupSummary",
    "Profile",
    "PresentationTypeWire",
    "ShareTypeWire",
    "StartEnd",
    "SyncTokenPair",
    "UrlCalendar",
    "UserMe",
    "is_all_day",
    "is_recurring_master_or_unique",
    "master_uuid",
]
