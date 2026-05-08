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

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _migration_002_retry_and_history(conn: sqlite3.Connection) -> None:
    """v2 — add retry-loop columns to event_mapping + sync_history table.

    New columns on event_mapping:
      - retry_after: ISO timestamp after which a failed event should be retried
      - retry_count: count of consecutive failed retries (drives exponential backoff)
      - last_error: human-readable last error string

    New table sync_history: append-only audit log of completed sync cycles.
    """
    conn.execute("ALTER TABLE event_mapping ADD COLUMN retry_after TEXT")
    conn.execute("ALTER TABLE event_mapping ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE event_mapping ADD COLUMN last_error TEXT")
    conn.execute("""
        CREATE TABLE sync_history (
            run_id            TEXT PRIMARY KEY,
            started_at        TEXT NOT NULL,
            completed_at      TEXT NOT NULL,
            duration_seconds  REAL NOT NULL,
            inserts           INTEGER NOT NULL,
            patches           INTEGER NOT NULL,
            deletes           INTEGER NOT NULL,
            no_ops            INTEGER NOT NULL,
            skipped_external  INTEGER NOT NULL,
            skipped_no_target INTEGER NOT NULL,
            errors_count      INTEGER NOT NULL,
            errors_json       TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX idx_sync_history_started_at
            ON sync_history(started_at DESC)
    """)


# Forward-only migration list. APPEND new entries here — never edit or delete
# existing ones, since production dbs are at varying versions and rely on this
# list as the canonical history. Each callable receives an open Connection.
_MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial),
    (2, _migration_002_retry_and_history),
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
    retry_after: datetime | None = None
    retry_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class SyncHistoryRecord:
    run_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    inserts: int
    patches: int
    deletes: int
    no_ops: int
    skipped_external: int
    skipped_no_target: int
    errors_count: int
    errors: list[tuple[str, str]]


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
        # check_same_thread=False allows the read-only health server (running
        # in worker threads) to share this connection with the single-threaded
        # sync loop. Writes still all originate from the sync thread, so we
        # don't violate SQLite's "serialize writes" guidance.
        self._conn = sqlite3.connect(
            self._path,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
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

    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int] | None:
        """Force a WAL checkpoint so writes become visible to other connections.

        Defaults to PASSIVE mode — checkpoints as much as possible without
        blocking concurrent writers, never waits for locks. Returns SQLite's
        `(busy, log_frames, checkpointed)` triple, or None on non-WAL dbs.

        Why this exists: writers in WAL mode commit to bridge.db-wal, which
        other reader connections (e.g. `bridge doctor` in a separate process)
        sometimes cannot see immediately due to a known SQLite quirk around
        WAL frame visibility under concurrent access. An explicit checkpoint
        after each meaningful write closes that visibility window.
        """
        valid = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        m = mode.upper()
        if m not in valid:
            raise ValueError(f"checkpoint mode must be one of {sorted(valid)}, got {mode!r}")
        try:
            row = self._conn.execute(f"PRAGMA wal_checkpoint({m})").fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return (int(row[0]), int(row[1]), int(row[2]))

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
        retry_after: datetime | None = None,
        retry_count: int = 0,
        last_error: str | None = None,
    ) -> None:
        """Idempotent UPSERT — overwrites all fields except daely_id.

        Calling this after a successful sync (defaults: failed=False,
        retry_count=0, retry_after=None, last_error=None) clears any
        prior failure state — i.e. the retry-loop is implicitly reset by
        a successful insert/patch.
        """
        ts = (last_synced_at or datetime.now(timezone.utc)).isoformat()
        seen = last_seen_updated.isoformat() if last_seen_updated else None
        retry_iso = retry_after.isoformat() if retry_after else None
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_mapping (
                    daely_id, daely_calendar_id, google_event_id, google_calendar_id,
                    last_seen_updated, last_synced_at, failed,
                    retry_after, retry_count, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(daely_id) DO UPDATE SET
                    daely_calendar_id   = excluded.daely_calendar_id,
                    google_event_id     = excluded.google_event_id,
                    google_calendar_id  = excluded.google_calendar_id,
                    last_seen_updated   = excluded.last_seen_updated,
                    last_synced_at      = excluded.last_synced_at,
                    failed              = excluded.failed,
                    retry_after         = excluded.retry_after,
                    retry_count         = excluded.retry_count,
                    last_error          = excluded.last_error
                """,
                (daely_id, daely_calendar_id, google_event_id, google_calendar_id,
                 seen, ts, 1 if failed else 0,
                 retry_iso, retry_count, last_error),
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
            retry_after=_parse_dt(row["retry_after"]),
            retry_count=int(row["retry_count"] or 0),
            last_error=row["last_error"],
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
        """Toggle the failed flag (legacy diagnostic helper).

        Does NOT touch retry_count / retry_after / last_error — for the full
        retry-loop semantics use `record_event_error` and `clear_event_error`.
        """
        with self._cursor() as cur:
            cur.execute(
                "UPDATE event_mapping SET failed = ? WHERE daely_id = ?",
                (1 if failed else 0, daely_id),
            )

    def record_event_error(
        self,
        daely_id: str,
        *,
        error_msg: str,
        base_seconds: float = 60.0,
        max_seconds: float = 3600.0,
        now: datetime | None = None,
    ) -> EventMapping | None:
        """Bump retry_count + schedule next retry using exponential backoff.

        Backoff: `min(base * 2^(retry_count-1), max)` seconds after `now`.
        On retry_count=1 → base, retry_count=2 → 2*base, ... capped at max.

        No-op if no row exists for `daely_id` (insert-failures aren't tracked
        here — they're naturally retried on the next sync cycle).

        Returns the updated mapping, or None if no row matched.
        """
        now = now or datetime.now(timezone.utc)
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT retry_count FROM event_mapping WHERE daely_id = ?",
                (daely_id,),
            ).fetchone()
            if row is None:
                return None
            new_count = int(row["retry_count"] or 0) + 1
            backoff_seconds = min(base_seconds * (2 ** (new_count - 1)), max_seconds)
            retry_after = now + timedelta(seconds=backoff_seconds)
            # Cap error messages to avoid bloating the row with stack traces.
            trimmed = (error_msg or "")[:1000]
            cur.execute(
                """
                UPDATE event_mapping
                SET failed = 1, retry_count = ?, retry_after = ?, last_error = ?
                WHERE daely_id = ?
                """,
                (new_count, retry_after.isoformat(), trimmed, daely_id),
            )
        return self.get_event_mapping(daely_id)

    def clear_event_error(self, daely_id: str) -> None:
        """Reset the retry-loop state for a mapping (used when an external
        operator wants to force a re-attempt on the next cycle)."""
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE event_mapping
                SET failed = 0, retry_count = 0, retry_after = NULL, last_error = NULL
                WHERE daely_id = ?
                """,
                (daely_id,),
            )

    def events_due_for_retry(self, *, now: datetime | None = None) -> list[EventMapping]:
        """Return failed mappings whose cooldown has elapsed.

        Used by diagnostic / status callers; the sync engine itself just
        consults `EventMapping.retry_after` per event during normal iteration.
        """
        now = now or datetime.now(timezone.utc)
        with self._cursor() as cur:
            rows = cur.execute(
                """
                SELECT * FROM event_mapping
                WHERE failed = 1
                  AND (retry_after IS NULL OR retry_after <= ?)
                """,
                (now.isoformat(),),
            ).fetchall()
        return [self._row_to_mapping(r) for r in rows]

    def reset_event_sync_markers(
        self, *, daely_calendar_id: str | None = None,
    ) -> int:
        """Set last_seen_updated=NULL on event_mappings to force a re-patch
        on the next sync cycle.

        Optionally scoped to one Daely calendar. Returns the number of rows
        affected. Safe + idempotent — the next sync simply re-issues the
        Google patch with the current mapper output (e.g. new colors,
        new footer).
        """
        with self._cursor() as cur:
            if daely_calendar_id is None:
                cur.execute("UPDATE event_mapping SET last_seen_updated = NULL")
            else:
                cur.execute(
                    "UPDATE event_mapping SET last_seen_updated = NULL "
                    "WHERE daely_calendar_id = ?",
                    (daely_calendar_id,),
                )
            return cur.rowcount

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

    # ─────────────── sync_history ───────────────

    def record_sync_history(
        self,
        *,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
        duration_seconds: float,
        inserts: int,
        patches: int,
        deletes: int,
        no_ops: int,
        skipped_external: int,
        skipped_no_target: int,
        errors: list[tuple[str, str]],
    ) -> None:
        """Append (or upsert by run_id) one row to sync_history.

        Errors are persisted as JSON of `{id, msg}` objects, capped at the
        first 100 entries to keep row size bounded.
        """
        errors_json = (
            json.dumps([{"id": eid, "msg": emsg} for eid, emsg in errors[:100]])
            if errors else None
        )
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_history (
                    run_id, started_at, completed_at, duration_seconds,
                    inserts, patches, deletes, no_ops,
                    skipped_external, skipped_no_target,
                    errors_count, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    started_at        = excluded.started_at,
                    completed_at      = excluded.completed_at,
                    duration_seconds  = excluded.duration_seconds,
                    inserts           = excluded.inserts,
                    patches           = excluded.patches,
                    deletes           = excluded.deletes,
                    no_ops            = excluded.no_ops,
                    skipped_external  = excluded.skipped_external,
                    skipped_no_target = excluded.skipped_no_target,
                    errors_count      = excluded.errors_count,
                    errors_json       = excluded.errors_json
                """,
                (run_id, started_at.isoformat(), completed_at.isoformat(),
                 duration_seconds, inserts, patches, deletes, no_ops,
                 skipped_external, skipped_no_target,
                 len(errors), errors_json),
            )

    def _row_to_history(self, row: sqlite3.Row) -> SyncHistoryRecord:
        errors: list[tuple[str, str]] = []
        if row["errors_json"]:
            try:
                for entry in json.loads(row["errors_json"]):
                    errors.append((entry.get("id", ""), entry.get("msg", "")))
            except (ValueError, TypeError):
                pass  # bad json — leave errors empty
        return SyncHistoryRecord(
            run_id=row["run_id"],
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            duration_seconds=float(row["duration_seconds"]),
            inserts=int(row["inserts"]),
            patches=int(row["patches"]),
            deletes=int(row["deletes"]),
            no_ops=int(row["no_ops"]),
            skipped_external=int(row["skipped_external"]),
            skipped_no_target=int(row["skipped_no_target"]),
            errors_count=int(row["errors_count"]),
            errors=errors,
        )

    def recent_sync_history(self, limit: int = 20) -> list[SyncHistoryRecord]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM sync_history ORDER BY started_at DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [self._row_to_history(r) for r in rows]

    def prune_sync_history(self, *, keep_last: int = 500) -> int:
        """Delete oldest rows beyond `keep_last`. Returns number deleted."""
        with self._cursor() as cur:
            cur.execute(
                """
                DELETE FROM sync_history WHERE run_id IN (
                    SELECT run_id FROM sync_history
                    ORDER BY started_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (max(0, int(keep_last)),),
            )
            return cur.rowcount


__all__ = [
    "LATEST_SCHEMA_VERSION",
    "EventMapping",
    "Store",
    "SyncHistoryRecord",
    "SyncState",
    "TokenRecord",
]
