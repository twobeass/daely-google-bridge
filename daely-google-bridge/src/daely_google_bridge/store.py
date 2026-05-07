"""SQLite persistence — pure stdlib, no ORM.

Schema:

    event_mapping(daely_id PK,
                  daely_calendar_id,
                  google_event_id,
                  google_calendar_id,
                  last_seen_updated,
                  last_synced_at,
                  failed)
    sync_state(calendar_id PK, internal_token, external_token,
               recommended_interval_min, last_polled_at)
    tokens(provider PK, refresh_token, access_token, expires_at)

All writes use ON CONFLICT … DO UPDATE so each method is idempotent: calling
put() with the same key always converges to the desired row, regardless of
prior state. Reads return None for missing rows; never raise.

`db_path=":memory:"` is supported for tests.

Field semantics in event_mapping:
- `daely_id`: primary key. The Daely event id (composite for recurring instances,
  e.g. `<masterUuid>_<startUTC>`; for the survivor of a recurring series after
  dedup it's the earliest instance's composite id).
- `daely_calendar_id`: which Daely calendar the event belongs to. Used by the
  sync layer for deletion detection (snapshot vs. store diff per calendar).
- `google_event_id`: id assigned by Google after `events.insert()`.
- `google_calendar_id`: which Google sub-calendar the event lives in.
- `last_seen_updated`: Daely's `CalendarEvent.updated` value last persisted to
  Google. Drives patch decisions.
- `last_synced_at`: wall-clock time of the last successful sync touch
  (insert/patch). Diagnostic.
- `failed`: sticky failure flag. Set by the sync layer when an event repeatedly
  errors out so it can be skipped + retried later.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_mapping (
    daely_id            TEXT PRIMARY KEY,
    daely_calendar_id   TEXT NOT NULL,
    google_event_id     TEXT NOT NULL,
    google_calendar_id  TEXT NOT NULL,
    last_seen_updated   TEXT,
    last_synced_at      TEXT NOT NULL,
    failed              INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_event_mapping_daely_cal
    ON event_mapping(daely_calendar_id);

CREATE INDEX IF NOT EXISTS idx_event_mapping_google_cal
    ON event_mapping(google_calendar_id);

CREATE TABLE IF NOT EXISTS sync_state (
    calendar_id                TEXT PRIMARY KEY,
    internal_token             TEXT,
    external_token             TEXT,
    recommended_interval_min   INTEGER,
    last_polled_at             TEXT
);

CREATE TABLE IF NOT EXISTS tokens (
    provider        TEXT PRIMARY KEY,
    refresh_token   TEXT NOT NULL,
    access_token    TEXT,
    expires_at      TEXT
);
"""


@dataclass(frozen=True, slots=True)
class EventMapping:
    daely_id: str
    daely_calendar_id: str
    google_event_id: str
    google_calendar_id: str
    last_seen_updated: datetime | None
    last_synced_at: datetime
    failed: bool


@dataclass(frozen=True, slots=True)
class SyncState:
    calendar_id: str
    internal_token: str | None
    external_token: str | None
    recommended_interval_min: int | None
    last_polled_at: datetime | None


@dataclass(frozen=True, slots=True)
class TokenRecord:
    provider: str
    refresh_token: str
    access_token: str | None
    expires_at: datetime | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class Store:
    """SQLite-backed persistence for the bridge."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        self._conn = sqlite3.connect(
            self._path,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    # ─────────────── lifecycle ───────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ─────────────── event_mapping ───────────────

    def put_event_mapping(
        self,
        *,
        daely_id: str,
        daely_calendar_id: str,
        google_event_id: str,
        google_calendar_id: str,
        last_seen_updated: datetime | None = None,
        failed: bool = False,
        last_synced_at: datetime | None = None,
    ) -> None:
        """Idempotent UPSERT — overwrites all fields except daely_id."""
        ts = (last_synced_at or datetime.now(timezone.utc)).isoformat()
        seen = last_seen_updated.isoformat() if last_seen_updated else None
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_mapping (
                    daely_id, daely_calendar_id, google_event_id, google_calendar_id,
                    last_seen_updated, last_synced_at, failed
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(daely_id) DO UPDATE SET
                    daely_calendar_id   = excluded.daely_calendar_id,
                    google_event_id     = excluded.google_event_id,
                    google_calendar_id  = excluded.google_calendar_id,
                    last_seen_updated   = excluded.last_seen_updated,
                    last_synced_at      = excluded.last_synced_at,
                    failed              = excluded.failed
                """,
                (daely_id, daely_calendar_id, google_event_id, google_calendar_id,
                 seen, ts, 1 if failed else 0),
            )

    def _row_to_mapping(self, row: sqlite3.Row) -> EventMapping:
        return EventMapping(
            daely_id=row["daely_id"],
            daely_calendar_id=row["daely_calendar_id"],
            google_event_id=row["google_event_id"],
            google_calendar_id=row["google_calendar_id"],
            last_seen_updated=_parse_dt(row["last_seen_updated"]),
            last_synced_at=_parse_dt(row["last_synced_at"]),
            failed=bool(row["failed"]),
        )

    def get_event_mapping(self, daely_id: str) -> EventMapping | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM event_mapping WHERE daely_id = ?",
                (daely_id,),
            ).fetchone()
        return self._row_to_mapping(row) if row else None

    def delete_event_mapping(self, daely_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM event_mapping WHERE daely_id = ?", (daely_id,))

    def all_event_mappings(self) -> list[EventMapping]:
        with self._cursor() as cur:
            rows = cur.execute("SELECT * FROM event_mapping").fetchall()
        return [self._row_to_mapping(r) for r in rows]

    def event_mappings_for_daely_calendar(self, daely_calendar_id: str) -> list[EventMapping]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM event_mapping WHERE daely_calendar_id = ?",
                (daely_calendar_id,),
            ).fetchall()
        return [self._row_to_mapping(r) for r in rows]

    def mark_event_failed(self, daely_id: str, *, failed: bool = True) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE event_mapping SET failed = ? WHERE daely_id = ?",
                (1 if failed else 0, daely_id),
            )

    # ─────────────── sync_state ───────────────

    def put_sync_state(
        self,
        *,
        calendar_id: str,
        internal_token: str | None = None,
        external_token: str | None = None,
        recommended_interval_min: int | None = None,
        last_polled_at: datetime | None = None,
    ) -> None:
        ts = last_polled_at.isoformat() if last_polled_at else None
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_state (
                    calendar_id, internal_token, external_token,
                    recommended_interval_min, last_polled_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(calendar_id) DO UPDATE SET
                    internal_token            = excluded.internal_token,
                    external_token            = excluded.external_token,
                    recommended_interval_min  = excluded.recommended_interval_min,
                    last_polled_at            = excluded.last_polled_at
                """,
                (calendar_id, internal_token, external_token, recommended_interval_min, ts),
            )

    def get_sync_state(self, calendar_id: str) -> SyncState | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM sync_state WHERE calendar_id = ?",
                (calendar_id,),
            ).fetchone()
        if row is None:
            return None
        return SyncState(
            calendar_id=row["calendar_id"],
            internal_token=row["internal_token"],
            external_token=row["external_token"],
            recommended_interval_min=row["recommended_interval_min"],
            last_polled_at=_parse_dt(row["last_polled_at"]),
        )

    # ─────────────── tokens ───────────────

    def put_token(
        self,
        *,
        provider: str,
        refresh_token: str,
        access_token: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        ts = expires_at.isoformat() if expires_at else None
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO tokens (provider, refresh_token, access_token, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    refresh_token = excluded.refresh_token,
                    access_token  = excluded.access_token,
                    expires_at    = excluded.expires_at
                """,
                (provider, refresh_token, access_token, ts),
            )

    def get_token(self, provider: str) -> TokenRecord | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM tokens WHERE provider = ?",
                (provider,),
            ).fetchone()
        if row is None:
            return None
        return TokenRecord(
            provider=row["provider"],
            refresh_token=row["refresh_token"],
            access_token=row["access_token"],
            expires_at=_parse_dt(row["expires_at"]),
        )

    def delete_token(self, provider: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM tokens WHERE provider = ?", (provider,))


__all__ = [
    "EventMapping",
    "Store",
    "SyncState",
    "TokenRecord",
]
