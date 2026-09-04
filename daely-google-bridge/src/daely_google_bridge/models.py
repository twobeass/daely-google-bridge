"""Pydantic models for Daely API responses.

All models use `extra="ignore"` so unexpected new fields from Daely don't break us.
Field names match the wire-format exactly — including the typo `writeable` (sic).

Wire-enum integer values were derived empirically from live data, NOT from the
blutter Dart-index disassembly (which mismatched). Treat them as authoritative
only for the values observed; new values may surface and need extending.
"""

from datetime import date as Date  # noqa: N812 — avoid collision with field name `date`
from datetime import datetime
from enum import IntEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ────────── enums (as int wire-values, with friendly literal aliases) ──────────

# Observed wire values; expand if more surface during operation.
AccountTypeWire = Literal[1]  # 1 = google (only one observed in live data)
CalendarTypeWire = Literal[0, 1]  # 0 = internal, 1 = google
ShareTypeWire = Literal[0, 1, 2, 3]  # confirmed only `2` (twoWay); others by analogy
PresentationTypeWire = Literal[0, 1]  # 0 = allEvents, 1 = timeWindow
ChecklistSortDirection = Literal["asc", "desc"]
ChecklistSortMode = Literal["orderIndex", "alphabetical"]
MealPlanSection = Literal["morning", "noon", "evening"]


class DeleteRecurrenceType(IntEnum):
    """Wire values used by recurring-resource DELETE endpoints.

    These are not the Dart enum ordinals. The app stores a separate integer
    value on each enum member and sends that value as ``deleteType``.
    """

    DELETE_ALL = 0
    DELETE_ONE = 1
    DELETE_FUTURE = 2


# ─────────────────── shared base ───────────────────


class _DaelyModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",  # tolerate unknown fields silently
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


# ────────────────── checklists ──────────────────


class ChecklistItem(_DaelyModel):
    id: str
    title: str
    completed: bool
    sortOrder: int  # noqa: N815 - Daely wire-format field


class ChecklistProgress(_DaelyModel):
    totalItemsCount: int  # noqa: N815 - Daely wire-format field
    completedItemsCount: int  # noqa: N815 - Daely wire-format field


class Checklist(_DaelyModel):
    id: str
    groupId: str  # noqa: N815 - Daely wire-format field
    name: str
    items: list[ChecklistItem] = Field(default_factory=list)
    sortOrder: int  # noqa: N815 - Daely wire-format field
    itemSortDirection: ChecklistSortDirection  # noqa: N815 - Daely wire-format field
    itemSortMode: ChecklistSortMode  # noqa: N815 - Daely wire-format field
    changeToken: int | None = None  # noqa: N815 - Daely wire-format field
    itemsIncluded: bool | None = None  # noqa: N815 - Daely wire-format field
    hideOnDevice: bool | None = None  # noqa: N815 - Daely wire-format field
    profileIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    progress: ChecklistProgress | None = None


class ChecklistCreateRequest(_DaelyModel):
    name: str
    hideOnDevice: bool = False  # noqa: N815 - Daely wire-format field
    profileIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field


class ChecklistConfig(_DaelyModel):
    maxNumberOfChecklists: int  # noqa: N815 - Daely wire-format field
    maxNumberOfItemsPerList: int  # noqa: N815 - Daely wire-format field


class ChecklistsOverview(_DaelyModel):
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    lists: list[Checklist] = Field(default_factory=list)
    config: ChecklistConfig


class ChecklistMutationResult(_DaelyModel):
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    listChangeToken: int  # noqa: N815 - Daely wire-format field
    checklist: Checklist | None = Field(default=None, alias="list")


class ChecklistItemMutationResult(_DaelyModel):
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    listChangeToken: int  # noqa: N815 - Daely wire-format field
    item: ChecklistItem | None = None
    groupId: str  # noqa: N815 - Daely wire-format field
    checklistId: str  # noqa: N815 - Daely wire-format field
    previousCompleted: bool | None = None  # noqa: N815 - wire field


class ChecklistItemsMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    checklistId: str  # noqa: N815 - Daely wire-format field
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    listChangeToken: int  # noqa: N815 - Daely wire-format field
    items: list[ChecklistItem] = Field(default_factory=list)


class ChecklistItemReorderResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    checklistId: str  # noqa: N815 - Daely wire-format field
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    checklistChangeToken: int  # noqa: N815 - Daely wire-format field
    items: list[ChecklistItem] = Field(default_factory=list)


class ChecklistSyncListRequest(_DaelyModel):
    token: int
    includeItems: bool  # noqa: N815 - Daely wire-format field


class ChecklistSyncRequest(_DaelyModel):
    lists: list[ChecklistSyncListRequest] = Field(default_factory=list)
    includeAllItems: bool  # noqa: N815 - Daely wire-format field
    includeProgress: bool  # noqa: N815 - Daely wire-format field


class ChecklistSyncEntry(_DaelyModel):
    id: str
    unchanged: bool
    data: Checklist | None = None


class ChecklistSyncResponse(_DaelyModel):
    groupChangeToken: int  # noqa: N815 - Daely wire-format field
    lists: list[ChecklistSyncEntry] = Field(default_factory=list)


# ────────────────── meal plan / recipes ──────────────────


class MealCategory(_DaelyModel):
    """Recipe category used for both responses and create/update payloads."""

    id: str | None = None
    name: str
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class Meal(_DaelyModel):
    """A saved recipe in Daely's wire terminology."""

    id: str | None = None
    categoryIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    name: str
    description: str | None = None
    emoji: str | None = None
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class MealPlanEntry(_DaelyModel):
    """One recipe assignment in the dated meal plan."""

    id: str | None = None
    mealId: str  # noqa: N815 - Daely wire-format field
    section: MealPlanSection
    date: Date
    recurrence: list[str] = Field(default_factory=list)
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class MealPlanConfig(_DaelyModel):
    maxNumberOfCategories: int  # noqa: N815 - Daely wire-format field
    maxNumberOfMeals: int  # noqa: N815 - Daely wire-format field


class MealPlanOverview(_DaelyModel):
    meals: list[Meal] = Field(default_factory=list)
    categories: list[MealCategory] = Field(default_factory=list)
    entries: list[MealPlanEntry] = Field(default_factory=list)
    mealPlanConfig: MealPlanConfig  # noqa: N815 - Daely wire-format field


# ────────────── v2 recipes (smartphone app >= 1.5.2) ──────────────


class MealIngredient(_DaelyModel):
    """One structured recipe ingredient.

    Daely accepts either a catalog ``groceryItemId`` or a free-form
    ``ingredientName``. ``amount`` remains a string so values such as
    ``"2 EL"`` survive unchanged.
    """

    groceryItemId: str | None = None  # noqa: N815 - Daely wire-format field
    ingredientName: str | None = None  # noqa: N815 - Daely wire-format field
    amount: str
    ignoredForGroceryList: bool = False  # noqa: N815 - Daely wire-format field


class MealInstruction(_DaelyModel):
    """One ordered recipe step; the app generates UUID-v4 IDs client-side."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    position: int
    text: str


class MealCategoryV2(_DaelyModel):
    """Recipe category returned by the v2 meals API."""

    id: str | None = None
    name: str
    isDefault: bool = False  # noqa: N815 - Daely wire-format field
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class MealHeader(_DaelyModel):
    """Compact recipe representation used in paginated v2 lists."""

    id: str
    name: str
    description: str | None = None
    emoji: str | None = None
    calories: int | None = None
    time: int | None = None
    imageUrl: str | None = None  # noqa: N815 - Daely wire-format field
    categoryIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    likedByProfileIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    isDefault: bool = False  # noqa: N815 - Daely wire-format field
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class MealDetail(_DaelyModel):
    """Full v2 recipe payload used by reads and mutations."""

    id: str | None = None
    name: str
    description: str | None = None
    emoji: str | None = None
    calories: int | None = None
    time: int | None = None
    portions: int = 1
    imageUrl: str | None = None  # noqa: N815 - Daely wire-format field
    websiteLink: str | None = None  # noqa: N815 - Daely wire-format field
    ingredients: list[MealIngredient] = Field(default_factory=list)
    instructions: list[MealInstruction] = Field(default_factory=list)
    categoryIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    likedByProfileIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire field
    isDefault: bool = False  # noqa: N815 - Daely wire-format field
    createdAt: datetime | None = None  # noqa: N815 - Daely wire-format field
    updatedAt: datetime | None = None  # noqa: N815 - Daely wire-format field


class PaginatedMealHeaders(_DaelyModel):
    """Wire shape of ``PaginatedResponse<MealHeader>``."""

    items: list[MealHeader] = Field(default_factory=list)
    page: int
    pageSize: int  # noqa: N815 - Daely wire-format field
    totalCount: int  # noqa: N815 - Daely wire-format field


class PaginatedMeals(_DaelyModel):
    groupMealChangeToken: int  # noqa: N815 - Daely wire-format field
    meals: PaginatedMealHeaders
    userMealCount: int | None = None  # noqa: N815 - Daely wire-format field


class MealCategories(_DaelyModel):
    groupCategoryChangeToken: int  # noqa: N815 - Daely wire-format field
    categories: list[MealCategoryV2] = Field(default_factory=list)


class MealConfig(_DaelyModel):
    maxNumberOfCategories: int  # noqa: N815 - Daely wire-format field
    maxNumberOfMeals: int  # noqa: N815 - Daely wire-format field


class MealsOverview(_DaelyModel):
    categories: MealCategories
    meals: PaginatedMeals
    config: MealConfig


class MealMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupMealChangeToken: int  # noqa: N815 - Daely wire-format field
    meal: MealDetail


class MealCategoryMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupCategoryChangeToken: int  # noqa: N815 - Daely wire-format field
    category: MealCategoryV2


class MealPlanEntries(_DaelyModel):
    groupMealPlanChangeToken: int  # noqa: N815 - Daely wire-format field
    groupMealChangeToken: int  # noqa: N815 - Daely wire-format field
    week: Date
    entries: list[MealPlanEntry] = Field(default_factory=list)
    meals: list[MealHeader] = Field(default_factory=list)


class MealPlanEntryMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupMealPlanChangeToken: int  # noqa: N815 - Daely wire-format field
    entry: MealPlanEntry | None = None


# ────────────── v2 grocery list (smartphone app >= 1.5.2) ──────────────


class CreateGroceryListItemRequest(_DaelyModel):
    """Create from either a catalog item ID or a free-form item name."""

    groceryItemId: str | None = None  # noqa: N815 - Daely wire-format field
    newItemName: str | None = None  # noqa: N815 - Daely wire-format field
    note: str | None = None
    amount: str | None = None
    language: str | None = None


class CreateGroceryListItemsRequest(_DaelyModel):
    items: list[CreateGroceryListItemRequest] = Field(default_factory=list)
    language: str | None = None


class GroceryListItem(_DaelyModel):
    id: str | None = None
    groceryItemId: str  # noqa: N815 - Daely wire-format field
    note: str | None = None
    amount: str | None = None
    isChecked: bool = False  # noqa: N815 - Daely wire-format field
    createdAt: datetime  # noqa: N815 - Daely wire-format field
    updatedAt: datetime  # noqa: N815 - Daely wire-format field


class GroceryItem(_DaelyModel):
    id: str
    categoryId: str | None = None  # noqa: N815 - Daely wire-format field
    name: str
    iconImageKey: str | None = None  # noqa: N815 - Daely wire-format field
    isDefault: bool = False  # noqa: N815 - Daely wire-format field
    isTemporary: bool = False  # noqa: N815 - Daely wire-format field
    createdAt: datetime  # noqa: N815 - Daely wire-format field
    updatedAt: datetime  # noqa: N815 - Daely wire-format field


class GroceryCategory(_DaelyModel):
    id: str
    name: str
    sortOrder: int = 0  # noqa: N815 - Daely wire-format field
    iconImageKey: str | None = None  # noqa: N815 - Daely wire-format field
    createdAt: datetime  # noqa: N815 - Daely wire-format field
    updatedAt: datetime  # noqa: N815 - Daely wire-format field


class GroceryConfig(_DaelyModel):
    maxNumberOfGroceryLists: int = 20  # noqa: N815 - Daely wire-format field
    maxNumberOfListItems: int = 200  # noqa: N815 - Daely wire-format field
    maxNumberOfCustomItems: int = 200  # noqa: N815 - Daely wire-format field
    maxNumberOfCheckedGroceryListItems: int = 24  # noqa: N815 - wire field


class LoyaltyCard(_DaelyModel):
    id: str | None = None
    groupId: str | None = None  # noqa: N815 - Daely wire-format field
    name: str
    data: str
    barcodeType: str  # noqa: N815 - Daely wire-format field
    color: str | None = None
    sortOrder: int = 0  # noqa: N815 - Daely wire-format field


class LoyaltyCardOverview(_DaelyModel):
    groupLoyaltyCardChangeToken: int  # noqa: N815 - Daely wire-format field
    cards: list[LoyaltyCard] = Field(default_factory=list)


class LoyaltyCardMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupLoyaltyCardChangeToken: int  # noqa: N815 - Daely wire-format field
    card: LoyaltyCard | None = None


class LoyaltyCardReorderResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupLoyaltyCardChangeToken: int  # noqa: N815 - Daely wire-format field
    cards: list[LoyaltyCard] = Field(default_factory=list)


class GroceryItemOverview(_DaelyModel):
    groupGroceryItemChangeToken: int  # noqa: N815 - Daely wire-format field
    items: list[GroceryItem] = Field(default_factory=list)


class GroceryListOverview(_DaelyModel):
    groupGroceryListChangeToken: int  # noqa: N815 - Daely wire-format field
    items: list[GroceryListItem] = Field(default_factory=list)


class GroceryOverview(_DaelyModel):
    groupGroceryListChangeToken: int  # noqa: N815 - Daely wire-format field
    groupGroceryItemChangeToken: int | None = None  # noqa: N815 - wire field
    groceryItems: list[GroceryItem] = Field(default_factory=list)  # noqa: N815
    groceryCategories: list[GroceryCategory] = Field(default_factory=list)  # noqa: N815
    groceryList: list[GroceryListItem] = Field(default_factory=list)  # noqa: N815
    config: GroceryConfig
    loyaltyCards: list[LoyaltyCard] = Field(default_factory=list)  # noqa: N815
    loyaltyCardChangeToken: int | None = None  # noqa: N815 - wire field


class GroceryItemMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupGroceryItemChangeToken: int  # noqa: N815 - Daely wire-format field
    item: GroceryItem | None = None


class GroceryListItemMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupGroceryListChangeToken: int  # noqa: N815 - Daely wire-format field
    item: GroceryListItem | None = None
    updatedItem: GroceryListItem | None = None  # noqa: N815 - wire field
    newlyCreatedItem: GroceryItem | None = None  # noqa: N815 - wire field


class GroceryListItemsMutationResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupGroceryListChangeToken: int  # noqa: N815 - Daely wire-format field
    items: list[GroceryListItem] = Field(default_factory=list)
    newlyCreatedItems: list[GroceryItem] = Field(default_factory=list)  # noqa: N815
    updatedItems: list[GroceryListItem] = Field(default_factory=list)  # noqa: N815


class GroceryListItemCheckResult(_DaelyModel):
    groupId: str  # noqa: N815 - Daely wire-format field
    groupGroceryListChangeToken: int  # noqa: N815 - Daely wire-format field
    updatedItem: GroceryListItem | None = None  # noqa: N815 - wire field
    deletedItem: GroceryListItem | None = None  # noqa: N815 - wire field
    deletedTemporaryGroceryItem: GroceryItem | None = None  # noqa: N815
    groupGroceryItemChangeToken: int | None = None  # noqa: N815 - wire field


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
    reminders: list[int] = Field(default_factory=list)  # minutes before event
    customColorCode: str | None = None  # "#RRGGBB" hex
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
    calendarType: int  # 0 = internal, 1 = google (empirical)
    shareType: int | None = None  # 2 = twoWay observed
    profileId: str | None = None
    isClassSchedule: bool = False
    writeable: bool = False  # sic: typo from server
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


# ─────────────────── realtime ───────────────────


class RealtimeEvent(_DaelyModel):
    """One push notification from Daely's `/realtime` SignalR hub.

    Wire format validated against a live `ReceiveNotification` (2026-06-03,
    see findings/10_REALTIME_API.md):

        {"resourceType": "Calendar",
         "subject": "calendar.calendar.<calendarId>.event.<eventId>.<action>",
         "time": "<iso8601>"}

    `subject` is a **dot-delimited** hierarchical path (NOT slash-delimited —
    the original static-RE guess was wrong). For a calendar event it has the
    shape `calendar.calendar.<calendarId>.event.<eventId>.<action>` where
    `<action>` is created | updated | deleted. UUIDs contain hyphens, never
    dots, so splitting on "." is safe.

    `time` is kept as a raw string — we never compute on it, and Daely emits
    7-digit fractional seconds which isn't worth a datetime round-trip.
    `extra="ignore"` tolerates any additional server fields.
    """

    resourceType: str | None = None
    subject: str | None = None
    time: str | None = None

    @property
    def _segments(self) -> list[str]:
        return self.subject.split(".") if self.subject else []

    @property
    def domain(self) -> str:
        """Top-level domain of the subject: `calendar`, `chore`, `checklist`,
        `group`, `user`, `administration`, `meal-plan`. Empty if no subject."""
        segs = self._segments
        return segs[0] if segs else ""

    @property
    def is_calendar_event(self) -> bool:
        """True for any calendar-domain notification (event create/update/delete
        or a calendar-level change). Drives whether the bridge triggers a sync."""
        return self.domain == "calendar"

    @property
    def action(self) -> str | None:
        """Trailing action verb (`created` | `updated` | `deleted`), or None."""
        segs = self._segments
        if segs and segs[-1] in ("created", "updated", "deleted"):
            return segs[-1]
        return None

    @property
    def event_id(self) -> str | None:
        """The event UUID, if the subject references one (`…event.<id>…`)."""
        segs = self._segments
        if "event" in segs:
            i = segs.index("event")
            if i + 1 < len(segs):
                return segs[i + 1]
        return None

    @property
    def calendar_id(self) -> str | None:
        """The calendar UUID, if present (`calendar.calendar.<id>…`)."""
        segs = self._segments
        if len(segs) >= 3 and segs[0] == "calendar" and segs[1] == "calendar":
            return segs[2]
        return None


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
    "Checklist",
    "ChecklistConfig",
    "ChecklistCreateRequest",
    "ChecklistItem",
    "ChecklistItemMutationResult",
    "ChecklistItemReorderResult",
    "ChecklistItemsMutationResult",
    "ChecklistMutationResult",
    "ChecklistProgress",
    "ChecklistSortDirection",
    "ChecklistSortMode",
    "ChecklistSyncEntry",
    "ChecklistSyncListRequest",
    "ChecklistSyncRequest",
    "ChecklistSyncResponse",
    "ChecklistsOverview",
    "CreateGroceryListItemRequest",
    "CreateGroceryListItemsRequest",
    "DeleteRecurrenceType",
    "ExternalAccount",
    "GroceryCategory",
    "GroceryConfig",
    "GroceryItem",
    "GroceryItemMutationResult",
    "GroceryItemOverview",
    "GroceryListItem",
    "GroceryListItemCheckResult",
    "GroceryListItemMutationResult",
    "GroceryListItemsMutationResult",
    "GroceryListOverview",
    "GroceryOverview",
    "Group",
    "GroupSummary",
    "LoyaltyCard",
    "LoyaltyCardMutationResult",
    "LoyaltyCardOverview",
    "LoyaltyCardReorderResult",
    "Meal",
    "MealCategories",
    "MealCategory",
    "MealCategoryMutationResult",
    "MealCategoryV2",
    "MealConfig",
    "MealDetail",
    "MealHeader",
    "MealIngredient",
    "MealInstruction",
    "MealMutationResult",
    "MealPlanConfig",
    "MealPlanEntries",
    "MealPlanEntry",
    "MealPlanEntryMutationResult",
    "MealPlanOverview",
    "MealPlanSection",
    "MealsOverview",
    "PaginatedMealHeaders",
    "PaginatedMeals",
    "PresentationTypeWire",
    "Profile",
    "RealtimeEvent",
    "ShareTypeWire",
    "StartEnd",
    "SyncTokenPair",
    "UrlCalendar",
    "UserMe",
    "is_all_day",
    "is_recurring_master_or_unique",
    "master_uuid",
]
