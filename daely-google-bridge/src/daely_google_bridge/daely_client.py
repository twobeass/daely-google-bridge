"""HTTPX-backed client for the Daely backend.

Auth: ROPC (Resource Owner Password Credentials) against the Daely Keycloak
realm. Refresh-token flow handles AT expiry transparently.

Behavioural guarantees:
- Min `min_pause_seconds` between consecutive Daely calls (configurable).
- 401 → one transparent refresh-and-retry. If the refresh itself fails the
  caller gets the original 401's body raised.
- 5xx → exponential backoff (1s, 2s, 4s, 8s, …, capped at 5 min) up to
  `max_retries` attempts, then raise.
- 4xx (other than 401) → raise immediately with body.

The client never logs token values. Callers are expected to feed any tokens
they receive into the Store, not to log them either.

Endpoints used:
- `POST {OIDC}/token`            ROPC + refresh
- `GET  /api/users/me`           UserMe
- `GET  /api/groups/me`          list[Group]
- `GET  /api/groups/<gid>/calendars`                              list[Calendar]
- `GET  /api/groups/<gid>/calendars/with-events?startDate=&endDate=`
- `GET  /api/external-accounts`  list[ExternalAccount]
- `GET  /api/url-calendars`      list[UrlCalendar]
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import httpx
import structlog

from .models import (
    Calendar,
    CalendarWithEvents,
    ExternalAccount,
    Group,
    Profile,
    UrlCalendar,
    UserMe,
)

DEFAULT_API_BASE = "https://daely-connect.com"
DEFAULT_OIDC_BASE = "https://sso.daely-connect.com/realms/daely/protocol/openid-connect"
DEFAULT_CLIENT_ID = "mobile-app"
DEFAULT_USER_AGENT = "daely-google-bridge/0.1 (research; private use)"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_BACKOFF_BASE = 1.0      # seconds
_BACKOFF_MAX = 300.0     # 5 min cap

log = structlog.get_logger(__name__)


class DaelyAuthError(RuntimeError):
    """ROPC failed (wrong password, MFA enabled, account locked, etc.)."""


class DaelyAPIError(RuntimeError):
    """Non-200 response after retries are exhausted."""

    def __init__(self, message: str, *, status_code: int, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class _TokenState:
    access_token: str | None = None
    refresh_token: str | None = None


@dataclass
class DaelyClient:
    """High-level Daely backend client. Synchronous, thread-unsafe.

    Construct once, reuse for the lifetime of a sync run. Pass `httpx_client`
    in tests so respx can intercept.
    """

    api_base: str = DEFAULT_API_BASE
    oidc_base: str = DEFAULT_OIDC_BASE
    client_id: str = DEFAULT_CLIENT_ID
    user_agent: str = DEFAULT_USER_AGENT
    min_pause_seconds: float = 1.0
    max_retries: int = 5
    httpx_client: httpx.Client | None = None

    _tokens: _TokenState = field(default_factory=_TokenState)
    _last_call_at: float = 0.0
    _own_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.httpx_client is None:
            self.httpx_client = httpx.Client(
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": self.user_agent},
            )
            self._own_client = True

    # ─────────────── lifecycle ───────────────

    def close(self) -> None:
        if self._own_client and self.httpx_client is not None:
            self.httpx_client.close()
            self.httpx_client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ─────────────── token state ───────────────

    @property
    def access_token(self) -> str | None:
        return self._tokens.access_token

    @property
    def refresh_token(self) -> str | None:
        return self._tokens.refresh_token

    def set_tokens(self, *, access_token: str | None, refresh_token: str | None) -> None:
        """Inject pre-existing tokens (e.g. loaded from the Store at startup)."""
        self._tokens.access_token = access_token
        self._tokens.refresh_token = refresh_token

    # ─────────────── auth ───────────────

    def login_password(self, email: str, password: str) -> dict:
        """ROPC login. Returns the raw token response from Keycloak.

        Stores access+refresh tokens internally. Caller should also persist
        the refresh token externally (Store) for cross-process restart.

        Raises:
            DaelyAuthError: if the IdP responds with anything other than 200.
              Includes the (often informative) Keycloak error_description.
        """
        log.info("daely.login_password.attempt", email_len=len(email))
        resp = self.httpx_client.post(
            f"{self.oidc_base}/token",
            data={
                "grant_type": "password",
                "client_id": self.client_id,
                "username": email,
                "password": password,
                "scope": "openid profile email offline_access",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise DaelyAuthError(f"ROPC failed: status={resp.status_code} body={body!r}")
        data = resp.json()
        self._tokens.access_token = data["access_token"]
        self._tokens.refresh_token = data.get("refresh_token")
        log.info("daely.login_password.ok", expires_in=data.get("expires_in"))
        return data

    def refresh(self) -> dict:
        """Exchange the stored refresh token for a fresh AT (and possibly a new RT).

        Raises:
            DaelyAuthError: if no refresh_token is available, or Keycloak
              rejects (e.g. invalid_grant when the RT has expired/been revoked).
        """
        if not self._tokens.refresh_token:
            raise DaelyAuthError("no refresh token available")
        log.info("daely.refresh.attempt")
        resp = self.httpx_client.post(
            f"{self.oidc_base}/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": self._tokens.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise DaelyAuthError(f"refresh failed: status={resp.status_code} body={body!r}")
        data = resp.json()
        self._tokens.access_token = data["access_token"]
        # Keycloak rotates the refresh token by default — store the new one.
        if "refresh_token" in data:
            self._tokens.refresh_token = data["refresh_token"]
        log.info("daely.refresh.ok", expires_in=data.get("expires_in"))
        return data

    # ─────────────── core request wrapper ───────────────

    def _pause_if_needed(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call_at
        if elapsed < self.min_pause_seconds:
            time.sleep(self.min_pause_seconds - elapsed)
        self._last_call_at = time.monotonic()

    def _backoff_seconds(self, attempt: int) -> float:
        return min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        _retried_after_refresh: bool = False,
    ) -> httpx.Response:
        if not self._tokens.access_token:
            raise DaelyAuthError(
                "no access_token; call login_password() or set_tokens() first"
            )

        url = f"{self.api_base}{path}"
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Accept": "application/json",
        }
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            self._pause_if_needed()
            try:
                resp = self.httpx_client.request(
                    method, url, params=params, json=json, headers=headers,
                )
            except httpx.HTTPError as e:
                last_exc = e
                wait = self._backoff_seconds(attempt)
                log.warning(
                    "daely.request.transport_error",
                    method=method, path=path, attempt=attempt, wait=wait, err=repr(e),
                )
                time.sleep(wait)
                continue

            if resp.status_code == 401 and not _retried_after_refresh:
                log.info("daely.request.401_refreshing", method=method, path=path)
                self.refresh()
                return self._request(
                    method, path, params=params, json=json,
                    _retried_after_refresh=True,
                )

            if resp.status_code >= 500:
                wait = self._backoff_seconds(attempt)
                log.warning(
                    "daely.request.5xx_backoff",
                    method=method, path=path, status=resp.status_code,
                    attempt=attempt, wait=wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                # client error — surface immediately (no retry helps)
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                raise DaelyAPIError(
                    f"{method} {path} → {resp.status_code}",
                    status_code=resp.status_code,
                    body=str(body),
                )

            return resp

        # exhausted retries
        if last_exc is not None:
            raise DaelyAPIError(
                f"{method} {path} transport-error after {self.max_retries} retries: {last_exc!r}",
                status_code=-1,
                body=None,
            )
        raise DaelyAPIError(
            f"{method} {path} 5xx after {self.max_retries} retries",
            status_code=599,
            body=None,
        )

    # ─────────────── typed reads ───────────────

    def get_me(self) -> UserMe:
        resp = self._request("GET", "/api/users/me")
        return UserMe.model_validate(resp.json())

    def get_my_groups(self) -> list[Group]:
        """Returns the list of groups the user belongs to (typically 1)."""
        resp = self._request("GET", "/api/groups/me")
        return [Group.model_validate(g) for g in resp.json()]

    def get_calendars(self, group_id: str) -> list[Calendar]:
        resp = self._request("GET", f"/api/groups/{group_id}/calendars")
        return [Calendar.model_validate(c) for c in resp.json()]

    def get_calendars_with_events(
        self,
        group_id: str,
        *,
        start_date: date,
        end_date: date,
    ) -> list[CalendarWithEvents]:
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }
        resp = self._request(
            "GET",
            f"/api/groups/{group_id}/calendars/with-events",
            params=params,
        )
        return [CalendarWithEvents.model_validate(c) for c in resp.json()]

    def get_external_accounts(self) -> list[ExternalAccount]:
        resp = self._request("GET", "/api/external-accounts")
        return [ExternalAccount.model_validate(a) for a in resp.json()]

    def get_profiles(self, group_id: str) -> list[Profile]:
        """Fetch the profile list for a group.

        Endpoint discovered in Phase 3e/A — see findings/08_PROFILES.md.
        """
        resp = self._request("GET", f"/api/groups/{group_id}/profiles")
        return [Profile.model_validate(p) for p in resp.json()]

    def get_url_calendars(self) -> list[UrlCalendar]:
        resp = self._request("GET", "/api/url-calendars")
        return [UrlCalendar.model_validate(u) for u in resp.json()]


__all__ = [
    "DEFAULT_API_BASE",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_OIDC_BASE",
    "DEFAULT_USER_AGENT",
    "DaelyAPIError",
    "DaelyAuthError",
    "DaelyClient",
]
