"""SQLite persistence — pure stdlib, no ORM.

Schema is managed by a forward-only migration framework (see `_MIGRATIONS`
below). On `Store()` init we detect the db's current version and apply any
outstanding migrations, with an automatic best-effort backup of file-based
dbs before each upgrade.

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# ─────────────────── migration framework ───────────────────

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
"""


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    """v1 — baseline schema (event_mapping, sync_state, tokens).

    Pre-framework production dbs already have this layout; the framework
    detects them via `event_mapping` presence and stamps schema_version=1
    without re-running this migration.
    """
    conn.execute("""
        CREATE TABLE event_mapping (
            daely_id            TEXT PRIMARY KEY,
            daely_calendar_id   TEXT NOT NULL,
            google_event_id     TEXT NOT NULL,
            google_calendar_id  TEXT NOT NULL,
            last_seen_updated   TEXT,
            last_synced_at      TEXT NOT NULL,
            failed              INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX idx_event_mapping_daely_cal
            ON event_mapping(daely_calendar_id)
    """)
    conn.execute("""
        CREATE INDEX idx_event_mapping_google_cal
            ON event_mapping(google_calendar_id)
    """)
    conn.execute("""
        CREATE TABLE sync_state (
            calendar_id                TEXT PRIMARY KEY,
            internal_token             TEXT,
            external_token             TEXT,
            recommended_interval_min   INTEGER,
            last_polled_at             TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE tokens (
            provider        TEXT PRIMARY KEY,
            refresh_token   TEXT NOT NULL,
            access_token    TEXT,
            expires_at      TEXT
        )
    """)


# Forward-only migration list. APPEND new entries here — never edit or delete
# existing ones, since production dbs are at varying versions and rely on this
# list as the canonical history. Each callable receives an open Connection.
_MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial),
]

LATEST_SCHEMA_VERSION = _MIGRATIONS[-1][0] if _MIGRATIONS else 0


def _detect_current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version this db is currently at.

    - 0 = fresh db, no tables of ours
    - N = `schema_version` row already says version=N
    - 1 = pre-framework db detected via `event_mapping` presence
    """
    try:
        row = conn.execute(
            "SELECT version FROM schema_version WHERE id=1",
        ).fetchone()
        if row is not None:
            return int(row[0])
    except sqlite3.OperationalError:
        pass  # schema_version table doesn't exist yet
    has_event_mapping = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='event_mapping'",
    ).fetchone() is not None
    return 1 if has_event_mapping else 0


def _backup_db_file(db_path: str, current_version: int) -> Path | None:
    """Best-effort copy of a file-based db before applying migrations.

    Skipped for `:memory:` or empty paths and on permission errors. Returns
    the backup Path on success, None otherwise. Naming: keeps the original
    suffix and appends `.bak.v{N}-{timestamp}`.
    """
    if db_path in (":memory:", ""):
        return None
    p = Path(db_path)
    if not p.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = p.with_name(f"{p.name}.bak.v{current_version}-{ts}")
    try:
        backup_path.write_bytes(p.read_bytes())
    except OSError:
        return None
    return backup_path


def _apply_migrations(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    backup: bool = True,
) -> tuple[int, int, Path | None]:
    """Bring `conn` up to LATEST_SCHEMA_VERSION.

    Returns a tuple `(from_version, to_version, backup_path_or_None)`.
    Caller can use the backup path for logging or post-migration verification.
    """
    from_version = _detect_current_version(conn)
    target = LATEST_SCHEMA_VERSION

    # The schema_version table is itself meta; ensure it exists before we
    # start writing version stamps to it.
    conn.executescript(SCHEMA_VERSION_DDL)

    if from_version >= target:
        # Nothing to do. Pin the row so future startups bypass detection.
        conn.execute(
            "INSERT INTO schema_version (id, version) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
            (from_version,),
        )
        return (from_version, from_version, None)

    # Only back up when there's actually pre-existing user data to lose.
    # from_version==0 means an empty file just opened by sqlite — nothing to save.
    backup_path = (
        _backup_db_file(db_path, from_version)
        if backup and from_version > 0
        else None
    )

    for version, migration_fn in _MIGRATIONS:
        if version > from_version:
            migration_fn(conn)
            conn.execute(
                "INSERT INTO schema_version (id, version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
                (version,),
            )

    return (from_version, target, backup_path)


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
    """SQLite-backed persistence for the bridge.

    Migrations are applied automatically on init. `backup_on_migrate=True`
    (default) writes a sibling `.bak.v<N>-<timestamp>` file before any
    upgrade actually runs, so a botched migration leaves the user with a
    recoverable copy.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        backup_on_migrate: bool = True,
    ) -> None:
        self._path = str(db_path)
        self._conn = sqlite3.connect(
            self._path,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migration_result = _apply_migrations(
            self._conn, db_path=self._path, backup=backup_on_migrate,
        )

    @property
    def schema_version(self) -> int:
        """The schema version this Store is currently operating at."""
        return self._migration_result[1]

    @property
    def migrated_from_version(self) -> int:
        """The schema version this db was at before init applied migrations.

        Useful for callers that want to log/announce upgrades — equal to
        `schema_version` if no migrations were run on this open.
        """
        return self._migration_result[0]

    @property
    def last_backup_path(self) -> Path | None:
        """Filesystem path of the pre-migration backup written on this open,
        or None if no backup was created (fresh db, :memory:, or no migrations
        needed)."""
        return self._migration_result[2]

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
    "LATEST_SCHEMA_VERSION",
    "EventMapping",
    "Store",
    "SyncState",
    "TokenRecord",
]
