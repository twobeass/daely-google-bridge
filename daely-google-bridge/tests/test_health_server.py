"""Tests for the health-check HTTP server."""
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

import pytest

from daely_google_bridge.health_server import (
    BridgeState,
    _is_fresh,
    start_health_server,
)
from daely_google_bridge.store import Store
from daely_google_bridge.sync import SyncReport


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture()
def free_port():
    """Yield an OS-allocated free TCP port for binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def server_thread(store, free_port):
    """Start a health server, yield (state, port, server), tear down."""
    state = BridgeState(poll_interval_minutes=15)
    server, thread = start_health_server(
        state, store, host="127.0.0.1", port=free_port,
    )
    yield state, free_port, server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _http_get(port: int, path: str, timeout: float = 2.0) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        return resp.status, body
    finally:
        conn.close()


# ────────── _is_fresh ──────────

def test_is_fresh_false_when_never_synced():
    state = BridgeState(poll_interval_minutes=15)
    assert _is_fresh(state) is False


def test_is_fresh_true_for_recent_sync():
    state = BridgeState(poll_interval_minutes=15)
    state.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _is_fresh(state) is True


def test_is_fresh_false_for_stale_sync():
    state = BridgeState(poll_interval_minutes=15)
    # 31 min back > 2*15 + 1min grace = 31min ⇒ on the boundary; go further out
    state.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    assert _is_fresh(state) is False


# ────────── BridgeState.update_from_report ──────────

def test_state_update_from_report_copies_counters():
    state = BridgeState(poll_interval_minutes=15)
    rep = SyncReport(inserts=3, patches=5, deletes=1, no_ops=10)
    rep.errors.append(("d-x", "boom"))
    rep.completed_at = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    rep.duration_seconds = 1.234
    state.update_from_report(rep)
    snap = state.snapshot()
    assert snap["last_inserts"] == 3
    assert snap["last_patches"] == 5
    assert snap["last_deletes"] == 1
    assert snap["last_errors_count"] == 1
    assert snap["last_run_id"] == rep.run_id
    assert snap["last_duration_seconds"] == 1.234


# ────────── /healthz ──────────

def test_healthz_503_when_never_synced(server_thread):
    _, port, _ = server_thread
    status, body = _http_get(port, "/healthz")
    assert status == 503
    assert "stale" in body


def test_healthz_200_after_recent_sync(server_thread):
    state, port, _ = server_thread
    rep = SyncReport()
    rep.completed_at = datetime.now(timezone.utc)
    state.update_from_report(rep)
    status, body = _http_get(port, "/healthz")
    assert status == 200
    assert "ok" in body


# ────────── /readyz ──────────

def test_readyz_503_when_no_tokens(server_thread):
    _, port, _ = server_thread
    status, body = _http_get(port, "/readyz")
    assert status == 503
    assert "missing" in body
    assert "daely" in body
    assert "google" in body


def test_readyz_200_when_both_tokens_present(server_thread, store):
    _, port, _ = server_thread
    store.put_token(provider="daely", refresh_token="rt-d")
    store.put_token(provider="google", refresh_token="rt-g")
    status, body = _http_get(port, "/readyz")
    assert status == 200
    assert "ready" in body


def test_readyz_503_lists_only_the_missing_provider(server_thread, store):
    _, port, _ = server_thread
    store.put_token(provider="daely", refresh_token="rt-d")
    # Google missing
    status, body = _http_get(port, "/readyz")
    assert status == 503
    assert "google" in body
    assert "daely" not in body


# ────────── /status ──────────

def test_status_returns_json_with_state_and_store_info(server_thread, store):
    state, port, _ = server_thread
    rep = SyncReport(inserts=2, patches=1)
    rep.completed_at = datetime.now(timezone.utc)
    rep.duration_seconds = 0.5
    state.update_from_report(rep)
    store.record_sync_history(
        run_id=rep.run_id,
        started_at=rep.started_at,
        completed_at=rep.completed_at,
        duration_seconds=rep.duration_seconds,
        inserts=2, patches=1, deletes=0, no_ops=0,
        skipped_external=0, skipped_no_target=0,
        errors=[],
    )

    status, body = _http_get(port, "/status")
    assert status == 200
    payload = json.loads(body)
    assert payload["last_inserts"] == 2
    assert payload["last_patches"] == 1
    assert payload["schema_version"] >= 2
    assert payload["mappings_total"] == 0
    assert isinstance(payload["recent_history"], list)
    assert any(h["run_id"] == rep.run_id for h in payload["recent_history"])


# ────────── 404 ──────────

def test_unknown_route_404(server_thread):
    _, port, _ = server_thread
    status, body = _http_get(port, "/nope")
    assert status == 404
    assert "not found" in body


# ────────── threading sanity ──────────

def test_repeated_requests_complete(server_thread):
    """Sanity: the server keeps serving across multiple sequential requests
    without leaking sockets or deadlocking. Sequential rather than threaded
    to avoid stdlib http.server connection-handling races under contention."""
    _, port, _ = server_thread
    for _ in range(10):
        status, _ = _http_get(port, "/readyz")
        assert status == 503  # no tokens in store


# ────────── HealthServerConfig (config wiring) ──────────

def test_config_health_server_defaults_to_disabled():
    from pathlib import Path

    from daely_google_bridge.config import BridgeConfig
    cfg = BridgeConfig(
        daely_email="x@example.com",
        google_oauth_client_secrets_file=Path("/tmp/x"),
    )
    assert cfg.health_server.enabled is False
    assert cfg.health_server.bind_host == "127.0.0.1"
    assert cfg.health_server.bind_port == 8090


def test_config_health_server_rejects_extra_fields():
    from pathlib import Path

    from pydantic import ValidationError

    from daely_google_bridge.config import BridgeConfig
    with pytest.raises(ValidationError):
        BridgeConfig(
            daely_email="x@example.com",
            google_oauth_client_secrets_file=Path("/tmp/x"),
            health_server={"enabled": True, "unknown": 1},
        )


def test_config_health_server_port_validated():
    from pathlib import Path

    from pydantic import ValidationError

    from daely_google_bridge.config import BridgeConfig
    with pytest.raises(ValidationError):
        BridgeConfig(
            daely_email="x@example.com",
            google_oauth_client_secrets_file=Path("/tmp/x"),
            health_server={"bind_port": 80},  # below the privileged threshold
        )


# Avoid an unused-import warning when this file is loaded standalone.
_ = time
