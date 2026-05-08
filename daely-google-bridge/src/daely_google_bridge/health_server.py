"""Tiny stdlib HTTP server exposing health + status endpoints.

Routes:
  GET /healthz   200 if last sync is newer than (poll_interval * 2 + grace) min,
                 else 503. Body: "ok\\n" / "stale\\n".
  GET /readyz    200 if Daely + Google refresh tokens are present in the store,
                 else 503. Body: "ready\\n" / "not-ready\\n".
  GET /status    200 with JSON: schema_version, last_run, mapping counts,
                 recent sync history.

Bind defaults to 127.0.0.1 to avoid accidental exposure. Override via
`health_server.bind_host` in config; "0.0.0.0" is allowed but the user is
responsible for putting it behind a reverse proxy or firewall.

The server runs in a daemon thread so the main process can exit without
explicit teardown. State is fed through a thread-safe `BridgeState` object
that the sync loop updates after each cycle.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store

# 60-second grace beyond `poll_interval * 2` so a slow sync doesn't flap us
# into 503 the moment it starts overrunning.
HEALTHZ_GRACE_SECONDS = 60


@dataclass
class BridgeState:
    """Thread-safe snapshot of bridge runtime state, fed by the sync loop."""
    poll_interval_minutes: int = 15
    last_sync_at: datetime | None = None
    last_run_id: str | None = None
    last_inserts: int = 0
    last_patches: int = 0
    last_deletes: int = 0
    last_no_ops: int = 0
    last_errors_count: int = 0
    last_duration_seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_from_report(self, report) -> None:  # noqa: ANN001  duck-typed
        with self._lock:
            self.last_sync_at = report.completed_at
            self.last_run_id = report.run_id
            self.last_inserts = report.inserts
            self.last_patches = report.patches
            self.last_deletes = report.deletes
            self.last_no_ops = report.no_ops
            self.last_errors_count = len(report.errors)
            self.last_duration_seconds = report.duration_seconds

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "poll_interval_minutes": self.poll_interval_minutes,
                "last_sync_at": (
                    self.last_sync_at.isoformat()
                    if self.last_sync_at else None
                ),
                "last_run_id": self.last_run_id,
                "last_inserts": self.last_inserts,
                "last_patches": self.last_patches,
                "last_deletes": self.last_deletes,
                "last_no_ops": self.last_no_ops,
                "last_errors_count": self.last_errors_count,
                "last_duration_seconds": round(self.last_duration_seconds, 3),
            }


def _is_fresh(state: BridgeState, *, now: datetime | None = None) -> bool:
    """True iff last_sync_at is within poll_interval * 2 + grace."""
    snap = state.snapshot()
    last_iso = snap["last_sync_at"]
    if last_iso is None:
        return False
    last = datetime.fromisoformat(last_iso)
    now = now or datetime.now(timezone.utc)
    age = (now - last).total_seconds()
    threshold = snap["poll_interval_minutes"] * 60 * 2 + HEALTHZ_GRACE_SECONDS
    return age <= threshold


def _make_handler(state: BridgeState, store: Store):
    """Closure factory: handler class wired with shared `state` and `store`."""

    class _Handler(BaseHTTPRequestHandler):
        # Silence the default access-log spam — sync logs are the source of truth.
        def log_message(self, *_args, **_kwargs) -> None:
            return

        def _respond(self, code: int, body: str, content_type: str = "text/plain") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802  stdlib API name
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                if _is_fresh(state):
                    self._respond(200, "ok\n")
                else:
                    self._respond(503, "stale\n")
                return
            if path == "/readyz":
                daely = store.get_token("daely") is not None
                google = store.get_token("google") is not None
                if daely and google:
                    self._respond(200, "ready\n")
                else:
                    missing = []
                    if not daely:
                        missing.append("daely")
                    if not google:
                        missing.append("google")
                    self._respond(503, f"not-ready missing={','.join(missing)}\n")
                return
            if path == "/status":
                snap = state.snapshot()
                snap["schema_version"] = store.schema_version
                snap["mappings_total"] = len(store.all_event_mappings())
                snap["mappings_failed"] = sum(
                    1 for m in store.all_event_mappings() if m.failed
                )
                snap["recent_history"] = [
                    {
                        "run_id": h.run_id,
                        "started_at": h.started_at.isoformat(),
                        "duration_seconds": round(h.duration_seconds, 3),
                        "inserts": h.inserts,
                        "patches": h.patches,
                        "deletes": h.deletes,
                        "errors": h.errors_count,
                    }
                    for h in store.recent_sync_history(limit=10)
                ]
                self._respond(
                    200, json.dumps(snap, indent=2, default=str) + "\n",
                    content_type="application/json",
                )
                return
            self._respond(404, "not found\n")

    return _Handler


def start_health_server(
    state: BridgeState,
    store: Store,
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start the HTTP server in a daemon thread. Returns (server, thread).

    The server stays alive for the lifetime of the process; daemon=True
    so the main thread exiting takes it down. Caller can also call
    `server.shutdown()` for a clean stop in tests.
    """
    handler_cls = _make_handler(state, store)
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever, name="health-server", daemon=True,
    )
    thread.start()
    return server, thread


__all__ = ["BridgeState", "start_health_server"]
