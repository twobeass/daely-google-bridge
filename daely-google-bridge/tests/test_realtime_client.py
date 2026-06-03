"""Tests for the RealtimeClient (SignalR over SSE) — all offline.

Uses respx to stub the negotiate POST + the small POSTs for handshake and
SetFilter. The streaming SSE GET is mocked via a fake httpx.Client whose
`stream("GET", ...)` returns a context manager yielding pre-canned chunks.

We don't try to test the full reconnect-with-backoff loop end-to-end here
(too much waiting); we test the per-session protocol logic and the parser.
"""
import json
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from daely_google_bridge.models import RealtimeEvent
from daely_google_bridge.realtime_client import (
    RealtimeClient,
    RealtimeFilter,
)

# ─────────────────── helpers ───────────────────

class FakeStreamResponse:
    """Mimics httpx.Client.stream(...) context manager. Yields raw chunks."""

    def __init__(self, *, status_code: int = 200, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}
        self._chunks = list(chunks or [])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_raw(self):
        for chunk in self._chunks:
            if chunk == b"__SLEEP__":
                time.sleep(0.05)
                continue
            yield chunk

    def read(self):
        return b"".join(c for c in self._chunks if c != b"__SLEEP__")


class FakePostResponse:
    def __init__(self, status_code: int = 200, body: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}
        self._json: dict | None = None

    def json(self) -> dict:
        return self._json or json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

    def read(self) -> bytes:
        return self.text.encode("utf-8")


class FakeHttpxClient:
    """Fake httpx.Client wired up for one full RealtimeClient session."""

    def __init__(self, *, negotiate_body: dict, sse_chunks: list[bytes]):
        self._negotiate_body = negotiate_body
        self._sse_chunks = sse_chunks
        self.posts: list[tuple[str, bytes]] = []  # (url, body)
        self.closed = False

    def post(self, url, headers=None, content=None):
        self.posts.append((url, content if isinstance(content, bytes) else b""))
        if "/negotiate" in url:
            r = FakePostResponse(
                status_code=200,
                body=json.dumps(self._negotiate_body),
            )
            r._json = self._negotiate_body
            return r
        # All other POSTs (handshake, SetFilter) are accepted with empty 200
        return FakePostResponse(status_code=200, body="")

    def stream(self, method, url, headers=None):
        assert method == "GET"
        return FakeStreamResponse(chunks=self._sse_chunks)

    def close(self):
        self.closed = True


def _frame(payload: dict | str | bytes) -> bytes:
    """Construct one SSE frame: `data: <payload>\\x1e\\r\\n\\r\\n`."""
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":"))
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return b"data: " + payload + b"\x1e\r\n\r\n"


# ─────────────────── parser ───────────────────


def test_parse_buffer_handshake_only():
    buf = _frame({})
    leftover, msgs = RealtimeClient._parse_buffer(buf)
    assert leftover == b""
    assert msgs == ["{}"]


def test_parse_buffer_with_initial_comment_and_handshake():
    buf = b":\r\n\r\n" + _frame({})
    leftover, msgs = RealtimeClient._parse_buffer(buf)
    assert leftover == b""
    assert msgs == ["{}"]


def test_parse_buffer_keeps_leftover_when_incomplete():
    buf = _frame({}) + b"data: {\"type\":6"
    leftover, msgs = RealtimeClient._parse_buffer(buf)
    assert msgs == ["{}"]
    assert b"type" in leftover  # incomplete, not yet emitted


def test_parse_buffer_multiple_signalr_messages_in_one_frame():
    """One SSE event can carry multiple SignalR messages, RS-separated."""
    payload = json.dumps({"type": 6}) + "\x1e" + json.dumps({"type": 6})
    buf = b"data: " + payload.encode() + b"\x1e\r\n\r\n"
    leftover, msgs = RealtimeClient._parse_buffer(buf)
    assert len(msgs) == 2
    assert all(json.loads(m) == {"type": 6} for m in msgs)


def test_parse_buffer_tolerates_lf_separator():
    """Some SignalR servers use \\n\\n instead of \\r\\n\\r\\n."""
    buf = b"data: {\"type\":6}\x1e\n\n"
    leftover, msgs = RealtimeClient._parse_buffer(buf)
    assert msgs == ['{"type":6}']


# ─────────────────── RealtimeFilter ───────────────────


def test_filter_to_json_field_order():
    f = RealtimeFilter(user="u-1", group="g-1")
    j = f.to_json()
    keys = list(j.keys())
    assert keys == [
        "user", "group",
        "subscribeUserCalendars", "subscribeGroupCalendars",
        "calendars",
        "subscribeChores", "subscribeChecklists",
    ]


def test_filter_calendars_defaults_to_null():
    """Dart-default behaviour: calendars unset → JSON `null`. Empirically
    `[]` disables subscription server-side even with subscribe* booleans
    set."""
    f = RealtimeFilter(user="u", group="g")
    assert f.to_json()["calendars"] is None


def test_filter_calendars_explicit_empty_list_serializes_as_empty_list():
    """If a caller explicitly passes [], we honour it (even though the
    server treats it as 'subscribe to nothing')."""
    f = RealtimeFilter(user="u", group="g", calendars=[])
    assert f.to_json()["calendars"] == []


def test_filter_calendars_propagates_user_value():
    f = RealtimeFilter(user="u", group="g", calendars=["cal-A", "cal-B"])
    assert f.to_json()["calendars"] == ["cal-A", "cal-B"]


# ─────────────────── RealtimeEvent model ───────────────────


# Real wire subject format (validated live 2026-06-03), synthetic UUIDs:
_CAL = "00000000-0000-0000-0000-0000000000c1"
_EV = "00000000-0000-0000-0000-0000000000e1"
_SUBJ_CREATED = f"calendar.calendar.{_CAL}.event.{_EV}.created"


def test_realtime_event_domain_extracts_first_dotted_segment():
    e = RealtimeEvent(subject=_SUBJ_CREATED)
    assert e.domain == "calendar"
    assert e.is_calendar_event is True


def test_realtime_event_parses_action_and_ids():
    e = RealtimeEvent(subject=_SUBJ_CREATED)
    assert e.action == "created"
    assert e.event_id == _EV
    assert e.calendar_id == _CAL


def test_realtime_event_updated_and_deleted_actions():
    upd = RealtimeEvent(subject=f"calendar.calendar.{_CAL}.event.{_EV}.updated")
    deleted = RealtimeEvent(subject=f"calendar.calendar.{_CAL}.event.{_EV}.deleted")
    assert upd.action == "updated"
    assert deleted.action == "deleted"


def test_realtime_event_no_subject_is_empty_domain():
    e = RealtimeEvent()
    assert e.domain == ""
    assert e.is_calendar_event is False
    assert e.action is None
    assert e.event_id is None


def test_realtime_event_chore_is_not_calendar_event():
    e = RealtimeEvent(subject="chore.chore.completion.created")
    assert e.domain == "chore"
    assert e.is_calendar_event is False


def test_realtime_event_calendar_level_change_still_counts():
    """A calendar-domain notification without an `.event.` segment (e.g. a
    calendar-level change) still triggers a sync."""
    e = RealtimeEvent(subject=f"calendar.calendar.{_CAL}.updated")
    assert e.is_calendar_event is True
    assert e.event_id is None  # no event segment
    assert e.action == "updated"


def test_realtime_event_tolerates_extra_fields():
    e = RealtimeEvent.model_validate({
        "resourceType": "Calendar",
        "subject": _SUBJ_CREATED,
        "time": "2026-06-03T07:18:19.9431427+00:00",
        "futureField": "if-server-adds-this-we-shouldnt-break",
        "anotherFuture": [1, 2, 3],
    })
    assert e.subject == _SUBJ_CREATED
    assert e.resourceType == "Calendar"
    assert e.time.startswith("2026-06-03")


# ─────────────────── full-session integration (mocked) ───────────────────


@pytest.fixture()
def captured_events():
    return []


def _build_client(captured_events: list, *, sse_chunks: list[bytes]):
    """Build a RealtimeClient wired to a FakeHttpxClient."""
    fake = FakeHttpxClient(
        negotiate_body={
            "negotiateVersion": 1,
            "connectionId": "conn-id",
            "connectionToken": "conn-tok",
            "availableTransports": [
                {"transport": "ServerSentEvents", "transferFormats": ["Text"]},
            ],
        },
        sse_chunks=sse_chunks,
    )
    client = RealtimeClient(
        access_token_provider=lambda: "test-at",
        user_id="u-test",
        group_id="g-test",
        on_event=captured_events.append,
        api_base="https://daely-connect.com",
        httpx_client_factory=lambda: fake,
    )
    return client, fake


def test_session_handshake_then_setfilter_then_notification(captured_events):
    """End-to-end happy path: server sends handshake_ok, client posts
    SetFilter, server pushes a ReceiveNotification, callback fires."""
    chunks = [
        _frame({}),  # handshake_ok
        _frame({"type": 3, "invocationId": "1", "result": None}),  # SetFilter completion
        _frame({
            "type": 1,
            "target": "ReceiveNotification",
            "arguments": [{
                "resourceType": "Calendar",
                "subject": _SUBJ_CREATED,
                "time": "2026-06-03T07:18:19.94+00:00",
            }],
        }),
        _frame({"type": 7, "error": None}),  # close from server
    ]
    client, fake = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    # Wait for the callback to fire
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not captured_events:
        time.sleep(0.02)
    client.stop(timeout=2.0)

    assert len(captured_events) == 1
    e = captured_events[0]
    assert e.subject == _SUBJ_CREATED
    assert e.event_id == _EV
    assert e.action == "created"
    assert e.is_calendar_event is True

    # Verify the POSTs that happened: 1 negotiate + 1 handshake + 1 SetFilter
    posted_urls = [u for u, _ in fake.posts]
    assert any("/negotiate" in u for u in posted_urls)
    assert sum(1 for u in posted_urls if "/realtime?id=" in u) == 2  # handshake + SetFilter

    # Verify SetFilter body shape
    setfilter_body = None
    for url, body in fake.posts:
        if "/realtime?id=" in url and b"SetFilter" in body:
            setfilter_body = body
            break
    assert setfilter_body is not None
    parsed = json.loads(setfilter_body.rstrip(b"\x1e"))
    assert parsed["target"] == "SetFilter"
    assert parsed["arguments"][0]["user"] == "u-test"
    assert parsed["arguments"][0]["group"] == "g-test"
    assert parsed["arguments"][0]["subscribeUserCalendars"] is True
    assert "calendars" in parsed["arguments"][0]


def test_session_pings_increment_stats(captured_events):
    chunks = [
        _frame({}),
        _frame({"type": 3, "invocationId": "1", "result": None}),
        _frame({"type": 6}),
        _frame({"type": 6}),
        _frame({"type": 6}),
        _frame({"type": 7, "error": None}),
    ]
    client, _ = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and client.stats.pings_received < 3:
        time.sleep(0.02)
    client.stop(timeout=2.0)
    assert client.stats.pings_received >= 3
    assert client.stats.handshakes_ok == 1
    assert client.stats.notifications_received == 0


def test_non_calendar_event_passes_to_callback_too(captured_events):
    """We dispatch all ReceiveNotifications; the bridge layer decides
    which subjects to act on."""
    chunks = [
        _frame({}),
        _frame({"type": 3, "invocationId": "1", "result": None}),
        _frame({
            "type": 1, "target": "ReceiveNotification",
            "arguments": [{"resourceType": "Chore",
                           "subject": "chore.chore.completion.created"}],
        }),
        _frame({"type": 7, "error": None}),
    ]
    client, _ = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not captured_events:
        time.sleep(0.02)
    client.stop(timeout=2.0)
    assert len(captured_events) == 1
    assert captured_events[0].is_calendar_event is False
    assert captured_events[0].domain == "chore"


def test_unknown_invoke_target_is_ignored_gracefully(captured_events):
    chunks = [
        _frame({}),
        _frame({"type": 3, "invocationId": "1", "result": None}),
        _frame({"type": 1, "target": "SomeUnknownThing", "arguments": []}),
        _frame({"type": 7, "error": None}),
    ]
    client, _ = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    time.sleep(0.5)
    client.stop(timeout=2.0)
    assert captured_events == []  # SomeUnknownThing dropped
    assert client.stats.notifications_received == 0


def test_callback_exception_does_not_kill_client(captured_events):
    """A misbehaving callback should be logged but not crash the reader."""
    bad_calls: list[int] = []

    def bad_callback(event):
        bad_calls.append(1)
        raise RuntimeError("simulated callback explosion")

    fake = FakeHttpxClient(
        negotiate_body={
            "negotiateVersion": 1, "connectionId": "ci", "connectionToken": "ct",
            "availableTransports": [{"transport": "ServerSentEvents",
                                     "transferFormats": ["Text"]}],
        },
        sse_chunks=[
            _frame({}),
            _frame({"type": 3, "invocationId": "1", "result": None}),
            _frame({"type": 1, "target": "ReceiveNotification",
                    "arguments": [{"subject": _SUBJ_CREATED}]}),
            _frame({"type": 1, "target": "ReceiveNotification",
                    "arguments": [{"subject": _SUBJ_CREATED}]}),
            _frame({"type": 7, "error": None}),
        ],
    )
    client = RealtimeClient(
        access_token_provider=lambda: "at",
        user_id="u", group_id="g",
        on_event=bad_callback,
        httpx_client_factory=lambda: fake,
    )
    client.start()
    time.sleep(0.7)
    client.stop(timeout=2.0)
    # Both notifications were dispatched (callback didn't kill the loop)
    assert len(bad_calls) == 2


def test_set_filter_invocation_uses_both_calendar_subscribes_by_default(captured_events):
    chunks = [_frame({}), _frame({"type": 7, "error": None})]
    client, fake = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    time.sleep(0.5)
    client.stop(timeout=2.0)
    setfilter_body = None
    for url, body in fake.posts:
        if b"SetFilter" in body:
            setfilter_body = body
            break
    assert setfilter_body is not None
    j = json.loads(setfilter_body.rstrip(b"\x1e"))
    args = j["arguments"][0]
    assert args["subscribeUserCalendars"] is True
    assert args["subscribeGroupCalendars"] is True
    assert args["subscribeChores"] is False
    assert args["subscribeChecklists"] is False


def test_lifecycle_start_is_idempotent(captured_events):
    chunks = [_frame({}), _frame({"type": 7, "error": None})]
    client, _ = _build_client(captured_events, sse_chunks=chunks)
    client.start()
    client.start()  # second start is a no-op
    assert client._thread is not None
    client.stop(timeout=2.0)


def test_stop_can_be_called_without_start(captured_events):
    client, _ = _build_client(captured_events, sse_chunks=[])
    client.stop(timeout=0.5)  # must not raise
