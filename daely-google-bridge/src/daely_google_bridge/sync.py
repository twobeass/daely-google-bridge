"""Sync orchestrator — Daely → Google Calendar.

Two entry points:

- `full_sync()`        scans `[today − lookback_days, today + lookahead_days]`,
                       inserts/patches/deletes, AND detects deletions by
                       diffing the snapshot against the store.
- `incremental_sync()` scans the same configured window by default, but only
                       acts on `deleted=true` flags from the snapshot (no
                       store-vs-snapshot diff). Long-term physical deletes are
                       caught by a periodic `full_sync`.

Both call into a shared `_run_sync()` helper.

Error model: per-event errors are isolated. A single event that fails to
insert/patch/delete is recorded in `SyncReport.errors` and the sync continues
with the next event. Authentication errors at the Daely or Google level
propagate (they make further work impossible).

State: the store's `event_mapping` table is the bridge's view of the world.
After every sync action, the corresponding mapping row is upserted (or
deleted) so reruns are convergent.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import structlog

from .config import BridgeConfig
from .daely_client import DaelyClient
from .google_client import GoogleClient
from .mapper import (
    daely_event_to_google,
    deduplicate_recurring,
    exdates_by_recurring_id,
    select_target_calendar_id,
    should_skip_calendar,
)
from .models import Calendar, CalendarEvent, CalendarWithEvents, Profile
from .store import EventMapping, Store

log = structlog.get_logger(__name__)


@dataclass
class SyncReport:
    """Result of one sync cycle.

    `errors` is a list of (daely_id, message) tuples. The daely_id is empty
    for non-event-scoped failures (e.g. "no groups").

    `skipped_retry_cooldown` counts events whose mapping is in the failed-retry
    cooldown — they were observed in the snapshot but skipped to avoid hammering
    Google with predictably-failing patches.
    """
    inserts: int = 0
    patches: int = 0
    deletes: int = 0
    no_ops: int = 0
    skipped_external_calendar_events: int = 0
    skipped_no_target_events: int = 0
    skipped_retry_cooldown: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def total_actions(self) -> int:
        return self.inserts + self.patches + self.deletes

    def __str__(self) -> str:  # pragma: no cover (cosmetic)
        return (
            f"SyncReport(run_id={self.run_id} "
            f"inserts={self.inserts} patches={self.patches} "
            f"deletes={self.deletes} no_ops={self.no_ops} "
            f"skip_external={self.skipped_external_calendar_events} "
            f"skip_no_target={self.skipped_no_target_events} "
            f"skip_retry_cooldown={self.skipped_retry_cooldown} "
            f"errors={len(self.errors)} duration={self.duration_seconds:.2f}s)"
        )


# ─────────────── helpers ───────────────

def _store_key(event: CalendarEvent) -> str:
    """Return the stable Daely-side key under which the bridge stores a mapping.

    For non-recurring events this is just `event.id`. For recurring events the
    composite id (`<master>_<startUTC>`) of the dedup survivor changes between
    sync windows when the earliest occurrence shifts; using the master UUID
    keeps the mapping stable across windows.
    """
    return event.recurringId or event.id


def _body_fingerprint(body: dict) -> str:
    """Stable hash of a rendered Google event body.

    Used by the no-op check to detect when the desired Google state changed
    even though Daely's `event.updated` did not — most importantly when a
    single recurring instance is deleted (Daely drops it from the expansion
    silently, so `updated` stays put but the synthesized EXDATE set changes).

    The body is fully derived from the event/calendar/profiles (no timestamps
    or nondeterminism), so `json.dumps(sort_keys=True)` is a stable key.
    """
    serialized = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _delete_via_google(
    *,
    google: GoogleClient,
    mapping: EventMapping,
    store: Store,
    report: SyncReport,
) -> None:
    # Honour retry cooldown for previously-failed mappings.
    now = datetime.now(timezone.utc)
    if mapping.failed and mapping.retry_after and mapping.retry_after > now:
        report.skipped_retry_cooldown += 1
        log.debug(
            "sync.delete.retry_cooldown",
            run_id=report.run_id,
            daely_id=mapping.daely_id,
            retry_after=mapping.retry_after.isoformat(),
        )
        return
    try:
        google.delete_event(mapping.google_calendar_id, mapping.google_event_id)
        store.delete_event_mapping(mapping.daely_id)
        report.deletes += 1
        log.info(
            "sync.delete.ok",
            run_id=report.run_id,
            daely_id=mapping.daely_id,
            google_event_id=mapping.google_event_id,
        )
    except Exception as e:
        msg = f"delete: {e!r}"
        report.errors.append((mapping.daely_id, msg))
        store.record_event_error(mapping.daely_id, error_msg=msg)
        log.warning(
            "sync.delete.error",
            run_id=report.run_id,
            daely_id=mapping.daely_id,
            err=msg,
        )


def _process_event(
    *,
    event: CalendarEvent,
    daely_calendar: Calendar,
    google_calendar_id: str,
    google: GoogleClient,
    store: Store,
    profile_calendar_map: dict[str, str],
    profiles_map: dict[str, Profile] | None,
    apply_colors: bool,
    color_overrides: dict[str, str] | None,
    recurrence_exdates: list[str] | None,
    report: SyncReport,
) -> None:
    """Handle one (already-deduped) Daely event."""
    daely_id = _store_key(event)

    # Soft-delete from Daely → propagate to Google.
    if event.deleted:
        existing = store.get_event_mapping(daely_id)
        if existing is not None:
            _delete_via_google(
                google=google, mapping=existing, store=store, report=report,
            )
        return

    body = daely_event_to_google(
        event, daely_calendar, profile_calendar_map,
        profiles_map=profiles_map,
        apply_colors=apply_colors,
        color_overrides=color_overrides,
        recurrence_exdates=recurrence_exdates,
    )
    if body is None:
        # mapper said skip — should be unreachable here (caller pre-filters by
        # calendarType), but guard against future filter extensions.
        return

    fingerprint = _body_fingerprint(body)

    existing = store.get_event_mapping(daely_id)
    if existing is None:
        # Insert. Insert-failures aren't tracked in the retry-cooldown system;
        # they retry naturally on the next cycle (no mapping row exists).
        try:
            response = google.insert_event(google_calendar_id, body)
            store.put_event_mapping(
                daely_id=daely_id,
                daely_calendar_id=daely_calendar.id,
                google_event_id=response["id"],
                google_calendar_id=google_calendar_id,
                last_seen_updated=event.updated,
                body_fingerprint=fingerprint,
            )
            report.inserts += 1
            log.info(
                "sync.insert.ok",
                run_id=report.run_id,
                daely_id=daely_id,
                google_event_id=response["id"],
            )
        except Exception as e:
            msg = f"insert: {e!r}"
            report.errors.append((daely_id, msg))
            log.warning(
                "sync.insert.error",
                run_id=report.run_id, daely_id=daely_id, err=msg,
            )
        return

    # Mapping exists. Honour retry cooldown for previously-failed events
    # so we don't hammer Google with the same predictable failure.
    now = datetime.now(timezone.utc)
    if existing.failed and existing.retry_after and existing.retry_after > now:
        report.skipped_retry_cooldown += 1
        log.debug(
            "sync.patch.retry_cooldown",
            run_id=report.run_id,
            daely_id=daely_id,
            retry_count=existing.retry_count,
            retry_after=existing.retry_after.isoformat(),
        )
        return

    # No-op only if there's nothing to change AND no failure pending. We
    # require BOTH the source `updated` to be unchanged AND the rendered body
    # to match what we last pushed (by fingerprint). The body check catches
    # changes Daely makes without bumping `updated` — chiefly a deleted
    # recurring instance, which alters the synthesized EXDATE set but leaves
    # the master's `updated` untouched. A NULL fingerprint (pre-v3 mappings)
    # never matches, so each existing mapping re-patches exactly once after
    # upgrade and then settles. A cooldown-elapsed failed mapping falls
    # through to a retry below.
    if (
        existing.last_seen_updated == event.updated
        and existing.body_fingerprint == fingerprint
        and not existing.failed
    ):
        report.no_ops += 1
        return

    try:
        google.patch_event(
            existing.google_calendar_id, existing.google_event_id, body,
        )
        # Successful patch clears any prior failure state via put_event_mapping
        # defaults (failed=False, retry_count=0, retry_after=None, last_error=None).
        store.put_event_mapping(
            daely_id=daely_id,
            daely_calendar_id=daely_calendar.id,
            google_event_id=existing.google_event_id,
            google_calendar_id=existing.google_calendar_id,
            last_seen_updated=event.updated,
            body_fingerprint=fingerprint,
        )
        report.patches += 1
        log.info(
            "sync.patch.ok",
            run_id=report.run_id,
            daely_id=daely_id,
            google_event_id=existing.google_event_id,
            recovered_from_failure=existing.failed,
        )
    except Exception as e:
        msg = f"patch: {e!r}"
        report.errors.append((daely_id, msg))
        store.record_event_error(daely_id, error_msg=msg)
        log.warning(
            "sync.patch.error",
            run_id=report.run_id, daely_id=daely_id,
            retry_count=existing.retry_count + 1, err=msg,
        )


def _process_calendar(
    cwe: CalendarWithEvents,
    *,
    google: GoogleClient,
    store: Store,
    profile_calendar_map: dict[str, str],
    profiles_map: dict[str, Profile] | None,
    apply_colors: bool,
    color_overrides: dict[str, str] | None,
    fallback_google_calendar_id: str | None,
    detect_missing_as_deleted: bool,
    report: SyncReport,
) -> None:
    if should_skip_calendar(cwe):
        report.skipped_external_calendar_events += len(cwe.events)
        return

    try:
        target = select_target_calendar_id(
            cwe.profileId, profile_calendar_map,
            fallback_calendar_id=fallback_google_calendar_id,
        )
    except KeyError:
        report.skipped_no_target_events += len(cwe.events)
        report.errors.append((
            cwe.id,
            f"no target calendar for profile {cwe.profileId!r} "
            f"(fallback also unset)",
        ))
        return

    # §3.1: compute EXDATEs per recurring series BEFORE dedup — we need the
    # full instance list to detect occurrences Daely silently dropped. After
    # dedup we only keep the master, so the gap signal would be gone.
    series_exdates = exdates_by_recurring_id(cwe.events)

    deduped = deduplicate_recurring(cwe.events)
    seen_keys = {_store_key(e) for e in deduped if not e.deleted}

    for ev in deduped:
        _process_event(
            event=ev,
            daely_calendar=cwe,
            google_calendar_id=target,
            google=google,
            store=store,
            profile_calendar_map=profile_calendar_map,
            profiles_map=profiles_map,
            apply_colors=apply_colors,
            color_overrides=color_overrides,
            recurrence_exdates=(
                series_exdates.get(ev.recurringId) if ev.recurringId else None
            ),
            report=report,
        )

    if detect_missing_as_deleted:
        for mapping in store.event_mappings_for_daely_calendar(cwe.id):
            if mapping.daely_id in seen_keys:
                continue
            _delete_via_google(
                google=google, mapping=mapping, store=store, report=report,
            )


# ─────────────── public entry points ───────────────

def _run_sync(
    daely: DaelyClient,
    google: GoogleClient,
    store: Store,
    config: BridgeConfig,
    *,
    start_date: date,
    end_date: date,
    detect_missing_as_deleted: bool,
) -> SyncReport:
    report = SyncReport()
    t0 = time.monotonic()
    log.info(
        "sync.start",
        run_id=report.run_id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        detect_missing_as_deleted=detect_missing_as_deleted,
    )

    groups = daely.get_my_groups()
    if not groups:
        report.errors.append(("", "no Daely groups returned"))
        return _finalize(report, t0, store=store)
    group = groups[0]

    # Phase 3e/3f: load profiles once per cycle. Footer (3e) needs the names;
    # color-mapping (3f) additionally needs colorCode + sortOrder. A failure
    # here demotes BOTH features to no-ops but doesn't abort the sync.
    profiles_map: dict[str, Profile] = {}
    try:
        profiles = daely.get_profiles(group.id)
        profiles_map = {p.id: p for p in profiles if p.name}
        log.info(
            "sync.profiles_loaded", run_id=report.run_id, count=len(profiles_map),
        )
    except Exception as e:
        log.warning(
            "sync.profile_fetch_failed", run_id=report.run_id, error=repr(e),
        )

    apply_colors = config.color_mapping.enabled
    color_overrides = (
        config.color_mapping.profile_overrides if apply_colors else None
    )

    cwes = daely.get_calendars_with_events(
        group.id, start_date=start_date, end_date=end_date,
    )
    for cwe in cwes:
        _process_calendar(
            cwe,
            google=google,
            store=store,
            profile_calendar_map=config.profile_calendar_mapping,
            profiles_map=profiles_map,
            apply_colors=apply_colors,
            color_overrides=color_overrides,
            fallback_google_calendar_id=config.fallback_google_calendar_id,
            detect_missing_as_deleted=detect_missing_as_deleted,
            report=report,
        )

    return _finalize(report, t0, store=store)


def _finalize(report: SyncReport, t0: float, *, store: Store | None = None) -> SyncReport:
    report.duration_seconds = time.monotonic() - t0
    report.completed_at = datetime.now(timezone.utc)
    log.info(
        "sync.done",
        run_id=report.run_id,
        inserts=report.inserts,
        patches=report.patches,
        deletes=report.deletes,
        no_ops=report.no_ops,
        skipped_external=report.skipped_external_calendar_events,
        skipped_no_target=report.skipped_no_target_events,
        skipped_retry_cooldown=report.skipped_retry_cooldown,
        errors=len(report.errors),
        duration=report.duration_seconds,
    )
    # Persist sync_history row + prune to keep table bounded. Best-effort:
    # a store error here doesn't poison an otherwise-successful sync.
    if store is not None:
        try:
            store.record_sync_history(
                run_id=report.run_id,
                started_at=report.started_at,
                completed_at=report.completed_at,
                duration_seconds=report.duration_seconds,
                inserts=report.inserts,
                patches=report.patches,
                deletes=report.deletes,
                no_ops=report.no_ops,
                skipped_external=report.skipped_external_calendar_events,
                skipped_no_target=report.skipped_no_target_events,
                errors=report.errors,
            )
            store.prune_sync_history(keep_last=500)
            # Explicit checkpoint so concurrent reader connections (e.g. a
            # separate `bridge doctor` process) see the new history row
            # immediately, without waiting for SQLite's auto-checkpoint.
            store.checkpoint()
        except Exception:
            log.exception("sync.history_record_failed", run_id=report.run_id)
    return report


def full_sync(
    daely: DaelyClient,
    google: GoogleClient,
    store: Store,
    config: BridgeConfig,
) -> SyncReport:
    """Full window scan with deletion detection via store-vs-snapshot diff."""
    today = date.today()
    return _run_sync(
        daely, google, store, config,
        start_date=today - timedelta(days=config.lookback_days),
        end_date=today + timedelta(days=config.lookahead_days),
        detect_missing_as_deleted=True,
    )


def incremental_sync(
    daely: DaelyClient,
    google: GoogleClient,
    store: Store,
    config: BridgeConfig,
    *,
    lookback_days: int | None = None,
    lookahead_days: int | None = None,
) -> SyncReport:
    """Incremental scan — relies on `deleted=true` flags only.

    The window defaults to the configured `lookback_days`/`lookahead_days`
    (same as `full_sync`) so edits to events anywhere in the configured range
    propagate on every poll — not just the next restart-triggered full_sync.
    Pass explicit `lookback_days`/`lookahead_days` to override (e.g. tests).

    Long-term physical deletions (events that vanish from the snapshot without
    a `deleted=true` flag) are still only caught by a `full_sync`, since
    incremental runs with `detect_missing_as_deleted=False`.
    """
    if lookback_days is None:
        lookback_days = config.lookback_days
    if lookahead_days is None:
        lookahead_days = config.lookahead_days
    today = date.today()
    return _run_sync(
        daely, google, store, config,
        start_date=today - timedelta(days=lookback_days),
        end_date=today + timedelta(days=lookahead_days),
        detect_missing_as_deleted=False,
    )


__all__ = [
    "SyncReport",
    "full_sync",
    "incremental_sync",
]
