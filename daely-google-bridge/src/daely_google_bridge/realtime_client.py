"""Daely SignalR-realtime client (SSE transport).

Connects to `https://daely-connect.com/realtime` via SignalR JSON Hub
Protocol (negotiate → SSE GET + parallel POSTs for client-to-server).
On each `ReceiveNotification` push, calls a user-supplied callback with
a parsed `RealtimeEvent`.

Design constraints (see `findings/10_REALTIME_API.md`):

- SSE transport only — simpler than WebSockets, no binary frames.
  SignalR negotiates SSE as one of three options.
- Server pushes a `{"type":6}` ping every 15s. Lack of pings within
  ~45s = dead connection → reconnect.
- Client must POST handshake `{"protocol":"json","version":1}\\x1e` after
  opening the SSE GET, then POST the SetFilter invocation.
- On reconnect, SetFilter must be re-sent (server filter is per
  connection-token, not per user-account).
- Access tokens may expire mid-stream — caller provides a fresh-token
  callable that we re-invoke for every (re)connect attempt.

Threading model: a single background daemon thread owns the SSE connection.
Public API (`start`, `stop`, `is_connected`) is thread-safe via a lock.
Callbacks fire on the SSE-reader thread — keep them short.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

from .models import RealtimeEvent

log = structlog.get_logger(__name__)

# SignalR record separator — terminates each Hub Protocol message
_RS = b"\x1e"
_RS_STR = "\x1e"

# Frame separator on the SSE side
_SSE_SEP_CRLF = b"\r\n\r\n"
_SSE_SEP_LF = b"\n\n"

# SignalR message type codes (subset we handle)
_TYPE_INVOCATION = 1
_TYPE_COMPLETION = 3
_TYPE_PING = 6
_TYPE_CLOSE = 7

# Backoff caps
_BACKOFF_BASE_S = 1.0
_BACKOFF_MAX_S = 300.0  # 5 min
_PING_DEAD_THRESHOLD_S = 45.0   # 3× ping interval

DEFAULT_API_BASE = "https://daely-connect.com"
DEFAULT_USER_AGENT = "daely-google-bridge/realtime (private use)"


@dataclass
class RealtimeFilter:
    """Subscribe filter sent to the server via `SetFilter` RPC.

    Mirrors the Dart `RealtimeFilter` model bit-for-bit, including the
    `calendars` whitelist field.

    `calendars=None` is the Dart-default state ("no explicit whitelist —
    use the boolean toggles only"). An empty list `[]` means "explicitly
    no calendars" — empirically this disables subscription on the
    server side. A non-empty list is a whitelist of specific UUIDs.
    """
    user: str
    group: str
    subscribe_user_calendars: bool = True
    subscribe_group_calendars: bool = True
    calendars: list[str] | None = None
    subscribe_chores: bool = False
    subscribe_checklists: bool = False

    def to_json(self) -> dict:
        """Wire-format JSON exactly matching the Dart toJson order.

        `calendars` is emitted as `null` when our list is None (Dart
        default), or as a JSON array when set explicitly.
        """
        return {
            "user": self.user,
            "group": self.group,
            "subscribeUserCalendars": self.subscribe_user_calendars,
            "subscribeGroupCalendars": self.subscribe_group_calendars,
            "calendars": (
                list(self.calendars) if self.calendars is not None else None
            ),
            "subscribeChores": self.subscribe_chores,
            "subscribeChecklists": self.subscribe_checklists,
        }


@dataclass
class RealtimeStats:
    """Counters for diagnostics; safe to read from any thread."""
    connect_attempts: int = 0
    handshakes_ok: int = 0
    pings_received: int = 0
    notifications_received: int = 0
    reconnects: int = 0
    last_ping_at: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None


# Type alias for the event callback. Caller's responsibility:
# - return promptly (fires on the reader thread)
# - exceptions are caught + logged but don't kill the client
EventCallback = Callable[[RealtimeEvent], None]
TokenProvider = Callable[[], str]


class RealtimeClient:
    """Background SSE-based SignalR client for Daely's `/realtime` hub.

    Usage:
        client = RealtimeClient(
            access_token_provider=lambda: my_daely.access_token,
            user_id="...", group_id="...",
            on_event=my_handler,
        )
        client.start()
        ...
        client.stop()
    """

    def __init__(
        self,
        *,
        access_token_provider: TokenProvider,
        user_id: str,
        group_id: str,
        on_event: EventCallback,
        api_base: str = DEFAULT_API_BASE,
        user_agent: str = DEFAULT_USER_AGENT,
        subscribe_user_calendars: bool = True,
        subscribe_group_calendars: bool = True,
        subscribe_chores: bool = False,
        subscribe_checklists: bool = False,
        calendars: list[str] | None = None,  # None → JSON null (subscribe per booleans, no whitelist)
        connect_timeout: float = 10.0,
        write_timeout: float = 10.0,
        # Injectable for tests
        httpx_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._token_provider = access_token_provider
        self._on_event = on_event
        self._api_base = api_base.rstrip("/")
        self._user_agent = user_agent
        self._filter = RealtimeFilter(
            user=user_id, group=group_id,
            subscribe_user_calendars=subscribe_user_calendars,
            subscribe_group_calendars=subscribe_group_calendars,
            calendars=calendars,  # pass through; None means "no whitelist"
            subscribe_chores=subscribe_chores,
            subscribe_checklists=subscribe_checklists,
        )
        self._connect_timeout = connect_timeout
        self._write_timeout = write_timeout
        self._httpx_client_factory = httpx_client_factory or self._default_httpx_factory

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connected = False
        self.stats = RealtimeStats()
        # Subjects we've already logged in full (to dedupe debug spam)
        self._logged_subjects: set[str] = set()

    @staticmethod
    def _default_httpx_factory() -> httpx.Client:
        # Per-attempt client — easier to recreate cleanly on reconnect
        return httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0, read=None,  # no read timeout (server may be silent for 15s+)
                write=10.0, pool=10.0,
            ),
            http2=False,  # keep simple for this transport
        )

    # ─────────────────── lifecycle ───────────────────

    def start(self) -> None:
        """Start the background reader thread. Returns immediately."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="daely-realtime", daemon=True,
            )
            self._thread.start()
        log.info("realtime.start", api_base=self._api_base)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the reader thread to exit and wait briefly for it."""
        self._stop_event.set()
        with self._lock:
            t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        log.info("realtime.stop")

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    # ─────────────────── main loop ───────────────────

    def _run(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self.stats.connect_attempts += 1
                duration = self._run_one_session()
                # If we ever connected successfully in this session, reset backoff
                if duration > 30.0:
                    attempt = 0
            except _StopRequested:
                break
            except Exception as e:
                self.stats.last_error = repr(e)
                log.warning("realtime.session_error", err=repr(e))
            if self._stop_event.is_set():
                break
            # Reconnect backoff
            attempt += 1
            wait = min(_BACKOFF_BASE_S * (2 ** (attempt - 1)), _BACKOFF_MAX_S)
            log.info(
                "realtime.reconnect_wait",
                attempt=attempt, wait_seconds=wait,
            )
            self.stats.reconnects += 1
            self._stop_event.wait(timeout=wait)
        log.info("realtime.run_exit")

    def _run_one_session(self) -> float:
        """Run one full connection lifecycle. Returns seconds connected
        (used by caller for backoff reset). Raises _StopRequested on graceful
        shutdown so the outer loop knows to exit clean.
        """
        access_token = self._token_provider()
        if not access_token:
            raise RuntimeError("realtime: empty access token from provider")

        client = self._httpx_client_factory()
        session_start = time.monotonic()
        try:
            # 1. Negotiate
            connection_token = self._negotiate(client, access_token)
            log.info("realtime.negotiate_ok",
                     token_len=len(connection_token))

            # 2. Open SSE GET in this thread (we'll iterate it inline)
            url = f"{self._api_base}/realtime?id={connection_token}"
            with client.stream(
                "GET", url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "User-Agent": self._user_agent,
                },
            ) as resp:
                if resp.status_code == 401:
                    raise _UnauthorizedError("SSE GET 401")
                if resp.status_code != 200:
                    body = resp.read()
                    raise RuntimeError(
                        f"SSE GET non-200: {resp.status_code} {body[:300]!r}"
                    )

                # 3. POST handshake. Server only sends data after this.
                self._post_message(
                    client, url, access_token,
                    b'{"protocol":"json","version":1}' + _RS,
                    label="handshake",
                )

                # 4. Drive the parser to find the handshake_ok ({}\x1e),
                #    then POST SetFilter.
                self._set_connected(True)
                handshake_seen = False
                filter_sent = False
                last_ping_monotonic = time.monotonic()
                buf = b""

                for chunk in resp.iter_raw():
                    if self._stop_event.is_set():
                        raise _StopRequested()
                    if chunk:
                        buf += chunk

                    for msg_text in self._drain_frames(buf):
                        # We mutate buf inside _drain_frames via a side
                        # channel — simpler to just reparse from scratch
                        pass
                    buf, messages = self._parse_buffer(buf)

                    for msg_text in messages:
                        try:
                            obj = json.loads(msg_text)
                        except json.JSONDecodeError:
                            log.warning("realtime.bad_json", msg=msg_text[:200])
                            continue

                        if obj == {}:
                            handshake_seen = True
                            self.stats.handshakes_ok += 1
                            log.info("realtime.handshake_ok")
                            # Send filter as soon as handshake clears
                            if not filter_sent:
                                self._post_set_filter(client, url, access_token)
                                filter_sent = True
                            continue

                        msg_type = obj.get("type")
                        if msg_type == _TYPE_PING:
                            self.stats.pings_received += 1
                            self.stats.last_ping_at = datetime.now(timezone.utc)
                            last_ping_monotonic = time.monotonic()
                            # Log every 4th ping (≈ once per minute) so the
                            # user can see in `docker logs` that the
                            # connection is alive without log spam.
                            if self.stats.pings_received % 4 == 1:
                                log.info(
                                    "realtime.ping",
                                    pings_total=self.stats.pings_received,
                                )
                            continue

                        if msg_type == _TYPE_COMPLETION:
                            err = obj.get("error")
                            if err:
                                log.warning(
                                    "realtime.completion_error",
                                    invocationId=obj.get("invocationId"),
                                    error=err,
                                )
                            else:
                                log.info(
                                    "realtime.completion_ok",
                                    invocationId=obj.get("invocationId"),
                                )
                            continue

                        if msg_type == _TYPE_CLOSE:
                            err = obj.get("error")
                            log.info(
                                "realtime.server_close", error=err,
                            )
                            return time.monotonic() - session_start

                        if msg_type == _TYPE_INVOCATION:
                            self._handle_invocation(obj)
                            continue

                        # Unknown type — log full payload so we can adapt
                        # the parser if the server's protocol drifts.
                        log.info(
                            "realtime.unknown_type",
                            type=msg_type, msg=msg_text[:500],
                        )

                    # Liveness check — if no ping for 45s, the connection is dead
                    if (handshake_seen
                            and time.monotonic() - last_ping_monotonic
                            > _PING_DEAD_THRESHOLD_S):
                        raise RuntimeError(
                            f"no ping for >{_PING_DEAD_THRESHOLD_S}s; "
                            f"connection presumed dead"
                        )

                    if not chunk and self._stop_event.is_set():
                        raise _StopRequested()

                return time.monotonic() - session_start
        finally:
            self._set_connected(False)
            try:
                client.close()
            except Exception:
                pass

    # ─────────────────── HTTP helpers ───────────────────

    def _negotiate(self, client: httpx.Client, access_token: str) -> str:
        url = f"{self._api_base}/realtime/negotiate?negotiateVersion=1"
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": self._user_agent,
            },
        )
        if r.status_code == 401:
            raise _UnauthorizedError("negotiate 401")
        r.raise_for_status()
        body = r.json()
        token = body.get("connectionToken")
        if not token:
            raise RuntimeError(f"negotiate response missing connectionToken: {body!r}")
        return token

    def _post_message(
        self,
        client: httpx.Client,
        url: str,
        access_token: str,
        payload: bytes,
        *,
        label: str,
    ) -> None:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "text/plain;charset=UTF-8",
                "User-Agent": self._user_agent,
            },
            content=payload,
        )
        if r.status_code == 401:
            raise _UnauthorizedError(f"{label} POST 401")
        if r.status_code >= 400:
            raise RuntimeError(
                f"{label} POST non-2xx: {r.status_code} {r.text[:300]!r}"
            )

    def _post_set_filter(
        self, client: httpx.Client, url: str, access_token: str,
    ) -> None:
        filter_json = self._filter.to_json()
        invoke = {
            "type": _TYPE_INVOCATION,
            "invocationId": "1",
            "target": "SetFilter",
            "arguments": [filter_json],
        }
        body = json.dumps(invoke, separators=(",", ":")).encode("utf-8") + _RS
        self._post_message(client, url, access_token, body, label="SetFilter")
        # Log the FULL filter JSON shape (with UUIDs truncated to 8 chars
        # for log readability — full UUIDs aren't logs-PII because they're
        # the user's own ids). This is the single most important diagnostic
        # when subscriptions look accepted but no notifications arrive.
        truncated = dict(filter_json)
        for k in ("user", "group"):
            v = truncated.get(k)
            if isinstance(v, str) and len(v) >= 12:
                truncated[k] = v[:8] + "…"
        if isinstance(truncated.get("calendars"), list):
            truncated["calendars"] = [
                (c[:8] + "…") if isinstance(c, str) and len(c) >= 12 else c
                for c in truncated["calendars"]
            ]
        log.info("realtime.set_filter_sent", filter=truncated)

    # ─────────────────── parsing ───────────────────

    @staticmethod
    def _parse_buffer(buf: bytes) -> tuple[bytes, list[str]]:
        """Pull complete SSE frames out of `buf`, return (leftover, messages).

        Each SSE frame ends in `\\r\\n\\r\\n` (CRLF) or `\\n\\n` (LF).
        Within an event, lines starting with `data: ` carry the SignalR
        payload; one frame may contain multiple SignalR messages, separated
        by `\\x1e` (RS).

        Note: we deliberately split on `\\n` (not `splitlines()`) because
        Python's str.splitlines() also treats `\\x1e` itself as a line
        separator — which would shred our SignalR messages before we can
        split on it ourselves.
        """
        messages: list[str] = []
        while True:
            sep_idx = -1
            sep_len = 0
            for sep in (_SSE_SEP_CRLF, _SSE_SEP_LF):
                i = buf.find(sep)
                if i != -1 and (sep_idx == -1 or i < sep_idx):
                    sep_idx = i
                    sep_len = len(sep)
            if sep_idx == -1:
                return buf, messages
            ev_bytes = buf[:sep_idx]
            buf = buf[sep_idx + sep_len:]
            text = ev_bytes.decode("utf-8", errors="replace")
            # Split on LF only; trim any trailing CR from CRLF endings.
            for line in text.split("\n"):
                line = line.rstrip("\r")
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                for msg in payload.split(_RS_STR):
                    msg = msg.strip()
                    if msg:
                        messages.append(msg)

    def _drain_frames(self, buf: bytes):
        # placeholder kept to keep the class shape extensible; real work is
        # in _parse_buffer (returns leftover + messages). Yields nothing.
        return
        yield  # pragma: no cover

    # ─────────────────── invocation dispatch ───────────────────

    def _handle_invocation(self, obj: dict) -> None:
        target = obj.get("target")
        args = obj.get("arguments") or []
        if target != "ReceiveNotification":
            # Log at INFO with full payload — if the server ever pushes a
            # different invocation target, we want to see it loud and clear
            # so we can adapt.
            log.info(
                "realtime.unknown_target",
                target=target, args_preview=str(args)[:300],
            )
            return

        if not args or not isinstance(args[0], dict):
            log.warning("realtime.bad_notification_args", args=args)
            return

        payload = args[0]
        try:
            event = RealtimeEvent.model_validate(payload)
        except Exception as e:
            log.warning(
                "realtime.event_parse_failed",
                err=repr(e), payload_keys=list(payload.keys()),
            )
            return

        self.stats.notifications_received += 1
        self.stats.last_event_at = datetime.now(timezone.utc)

        # First-time-per-subject debug log: full payload, so we can validate
        # the event shape against the static RE assumptions in production.
        subj = event.subject or "<no-subject>"
        if subj not in self._logged_subjects:
            self._logged_subjects.add(subj)
            log.info(
                "realtime.first_event_for_subject",
                subject=subj, raw=payload,
            )
        else:
            log.info(
                "realtime.event",
                subject=subj,
                main_topic=event.main_topic,
                entity_id=event.entityId,
            )

        try:
            self._on_event(event)
        except Exception:
            log.exception("realtime.callback_failed", subject=subj)


# ─────────────────── private exceptions ───────────────────

class _StopRequested(Exception):
    """Internal: outer loop should exit cleanly."""


class _UnauthorizedError(Exception):
    """401 from server — outer loop will reconnect after refreshing the token."""


__all__ = [
    "DEFAULT_API_BASE",
    "EventCallback",
    "RealtimeClient",
    "RealtimeFilter",
    "RealtimeStats",
    "TokenProvider",
]
