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
- `GET/POST/PUT/DELETE /api/groups/<gid>/checklists/...` (legacy)
- `GET/POST/PUT/DELETE /api/groups/<gid>/meal-plan/...` (legacy)
- `GET/POST/PUT/DELETE /api/v2/groups/<gid>/checklists/...`
- `GET/POST/PUT/DELETE /api/v2/groups/<gid>/meal-plan/entries/...`
- `GET/POST/PUT/DELETE /api/v2/groups/<gid>/meals/...`
- `GET/POST/PUT/DELETE /api/v2/groups/<gid>/grocery/...`
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
    Checklist,
    ChecklistCreateRequest,
    ChecklistItem,
    ChecklistItemMutationResult,
    ChecklistItemReorderResult,
    ChecklistItemsMutationResult,
    ChecklistMutationResult,
    ChecklistSortDirection,
    ChecklistSortMode,
    ChecklistsOverview,
    ChecklistSyncRequest,
    ChecklistSyncResponse,
    CreateGroceryListItemRequest,
    CreateGroceryListItemsRequest,
    DeleteRecurrenceType,
    ExternalAccount,
    GroceryItem,
    GroceryItemMutationResult,
    GroceryItemOverview,
    GroceryListItem,
    GroceryListItemCheckResult,
    GroceryListItemMutationResult,
    GroceryListItemsMutationResult,
    GroceryListOverview,
    GroceryOverview,
    Group,
    LoyaltyCard,
    LoyaltyCardMutationResult,
    LoyaltyCardOverview,
    LoyaltyCardReorderResult,
    Meal,
    MealCategory,
    MealCategoryMutationResult,
    MealCategoryV2,
    MealDetail,
    MealMutationResult,
    MealPlanEntries,
    MealPlanEntry,
    MealPlanEntryMutationResult,
    MealPlanOverview,
    MealsOverview,
    PaginatedMeals,
    Profile,
    UrlCalendar,
    UserMe,
)

DEFAULT_API_BASE = "https://daely-connect.com"
DEFAULT_OIDC_BASE = "https://sso.daely-connect.com/realms/daely/protocol/openid-connect"
DEFAULT_CLIENT_ID = "mobile-app"
DEFAULT_USER_AGENT = "daely-google-bridge/0.1 (research; private use)"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_BACKOFF_BASE = 1.0  # seconds
_BACKOFF_MAX = 300.0  # 5 min cap

log = structlog.get_logger(__name__)


class DaelyAuthError(RuntimeError):
    """ROPC failed (wrong password, MFA enabled, account locked, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        details = []
        if status_code is not None:
            details.append(f"status={status_code}")
        if error:
            details.append(f"error={error}")
        if error_description:
            details.append(f"description={error_description}")
        if details:
            message = f"{message}: {', '.join(details)}"
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.error_description = error_description


def _oidc_auth_error(operation: str, response: httpx.Response) -> DaelyAuthError:
    """Build a useful auth error without retaining arbitrary response content."""
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    def safe_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        # Keycloak's two documented error fields are enough for diagnosis.
        # Collapse control/whitespace and cap output rather than echoing a raw body.
        return " ".join(value.split())[:200] or None

    return DaelyAuthError(
        f"{operation} failed",
        status_code=response.status_code,
        error=safe_text(payload.get("error")),
        error_description=safe_text(payload.get("error_description")),
    )


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
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise _oidc_auth_error("ROPC", resp)
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
            raise _oidc_auth_error("refresh", resp)
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
        return min(_BACKOFF_BASE * (2**attempt), _BACKOFF_MAX)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        _retried_after_refresh: bool = False,
    ) -> httpx.Response:
        if not self._tokens.access_token:
            raise DaelyAuthError("no access_token; call login_password() or set_tokens() first")

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
                    method,
                    url,
                    params=params,
                    json=json,
                    files=files,
                    headers=headers,
                )
            except httpx.HTTPError as e:
                last_exc = e
                wait = self._backoff_seconds(attempt)
                log.warning(
                    "daely.request.transport_error",
                    method=method,
                    path=path,
                    attempt=attempt,
                    wait=wait,
                    err=repr(e),
                )
                time.sleep(wait)
                continue

            if resp.status_code == 401 and not _retried_after_refresh:
                log.info("daely.request.401_refreshing", method=method, path=path)
                self.refresh()
                return self._request(
                    method,
                    path,
                    params=params,
                    json=json,
                    files=files,
                    _retried_after_refresh=True,
                )

            if resp.status_code >= 500:
                wait = self._backoff_seconds(attempt)
                log.warning(
                    "daely.request.5xx_backoff",
                    method=method,
                    path=path,
                    status=resp.status_code,
                    attempt=attempt,
                    wait=wait,
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

    # ────────────── legacy checklists ──────────────

    def get_checklists(self, group_id: str) -> list[Checklist]:
        resp = self._request("GET", f"/api/groups/{group_id}/checklists")
        return [Checklist.model_validate(item) for item in resp.json()]

    def create_checklist(self, group_id: str, *, name: str) -> Checklist:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/checklists",
            json={"name": name},
        )
        return Checklist.model_validate(resp.json())

    def update_checklist(
        self,
        group_id: str,
        checklist_id: str,
        *,
        name: str,
        item_sort_mode: ChecklistSortMode,
        item_sort_direction: ChecklistSortDirection,
    ) -> None:
        self._request(
            "PUT",
            f"/api/groups/{group_id}/checklists/{checklist_id}",
            json={
                "name": name,
                "itemSortMode": item_sort_mode,
                "itemSortDirection": item_sort_direction,
            },
        )

    def delete_checklist(self, group_id: str, checklist_id: str) -> None:
        self._request("DELETE", f"/api/groups/{group_id}/checklists/{checklist_id}")

    def reorder_checklists(self, group_id: str, ordered_ids: list[str]) -> None:
        self._request(
            "PUT",
            f"/api/groups/{group_id}/checklists/reorder",
            json={"orderedIds": ordered_ids},
        )

    def create_checklist_item(
        self,
        group_id: str,
        checklist_id: str,
        *,
        title: str,
    ) -> ChecklistItem:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/checklists/{checklist_id}/items",
            json={"title": title, "completed": False},
        )
        return ChecklistItem.model_validate(resp.json())

    def update_checklist_item(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
        *,
        title: str,
    ) -> None:
        self._request(
            "PUT",
            f"/api/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
            json={"title": title},
        )

    def set_checklist_item_completed(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
        *,
        completed: bool,
    ) -> ChecklistItem:
        resp = self._request(
            "PUT",
            f"/api/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
            json={"completed": completed},
        )
        return ChecklistItem.model_validate(resp.json())

    def delete_checklist_item(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
    ) -> None:
        self._request(
            "DELETE",
            f"/api/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
        )

    def reorder_checklist_items(
        self,
        group_id: str,
        checklist_id: str,
        ordered_ids: list[str],
    ) -> None:
        self._request(
            "PUT",
            f"/api/groups/{group_id}/checklists/{checklist_id}/items/reorder",
            json={"orderedIds": ordered_ids},
        )

    # ────────────── v2 checklists (smartphone app >= 1.5.2) ──────────────

    def sync_checklists_v2(
        self,
        group_id: str,
        request: ChecklistSyncRequest,
    ) -> ChecklistSyncResponse:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/checklists/sync",
            json=self._model_body(request),
        )
        return ChecklistSyncResponse.model_validate(resp.json())

    def get_checklists_v2(
        self,
        group_id: str,
        *,
        include_items_for: list[str] | None = None,
    ) -> ChecklistsOverview:
        params: dict[str, str | list[str]] = {
            "includeAllItems": "false",
            "includeProgress": "true",
        }
        if include_items_for:
            params["includeItemsFor"] = include_items_for
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/checklists",
            params=params,
        )
        return ChecklistsOverview.model_validate(resp.json())

    def get_checklist_v2(
        self,
        group_id: str,
        checklist_id: str,
    ) -> ChecklistMutationResult:
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}",
        )
        return ChecklistMutationResult.model_validate(resp.json())

    def create_checklist_v2(
        self,
        group_id: str,
        request: ChecklistCreateRequest,
    ) -> ChecklistMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/checklists",
            json=self._model_body(request),
        )
        return ChecklistMutationResult.model_validate(resp.json())

    def update_checklist_v2(
        self,
        group_id: str,
        checklist: Checklist,
    ) -> ChecklistMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/checklists/{checklist.id}",
            json=self._model_body(checklist),
        )
        return ChecklistMutationResult.model_validate(resp.json())

    def delete_checklist_v2(
        self,
        group_id: str,
        checklist_id: str,
    ) -> ChecklistMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}",
        )
        return ChecklistMutationResult.model_validate(resp.json())

    def create_checklist_item_v2(
        self,
        group_id: str,
        checklist_id: str,
        *,
        title: str,
    ) -> ChecklistItemMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items",
            json={"title": title, "completed": False},
        )
        return ChecklistItemMutationResult.model_validate(resp.json())

    def update_checklist_item_v2(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
        *,
        title: str,
    ) -> ChecklistItemMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
            json={"title": title},
        )
        return ChecklistItemMutationResult.model_validate(resp.json())

    def set_checklist_item_completed_v2(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
        *,
        completed: bool,
    ) -> ChecklistItemMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
            json={"completed": completed},
        )
        return ChecklistItemMutationResult.model_validate(resp.json())

    def delete_checklist_item_v2(
        self,
        group_id: str,
        checklist_id: str,
        item_id: str,
    ) -> ChecklistItemMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items/{item_id}",
        )
        return ChecklistItemMutationResult.model_validate(resp.json())

    def reorder_checklist_items_v2(
        self,
        group_id: str,
        checklist_id: str,
        ordered_ids: list[str],
    ) -> ChecklistItemReorderResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items/reorder",
            json={"orderedIds": ordered_ids},
        )
        return ChecklistItemReorderResult.model_validate(resp.json())

    def uncheck_all_checklist_items_v2(
        self,
        group_id: str,
        checklist_id: str,
    ) -> ChecklistItemsMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/uncheck-all",
        )
        return ChecklistItemsMutationResult.model_validate(resp.json())

    def delete_checklist_items_v2(
        self,
        group_id: str,
        checklist_id: str,
        *,
        completed_only: bool = True,
    ) -> ChecklistItemsMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/checklists/{checklist_id}/items",
            params={"completedOnly": str(completed_only).lower()},
        )
        return ChecklistItemsMutationResult.model_validate(resp.json())

    # ────────────── v2 grocery list (smartphone app >= 1.5.2) ──────────────

    def get_grocery_items_v2(
        self,
        group_id: str,
        *,
        include_default: bool = True,
    ) -> GroceryItemOverview:
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/grocery/items",
            params={"includeDefault": str(include_default).lower()},
        )
        return GroceryItemOverview.model_validate(resp.json())

    def get_grocery_list_v2(self, group_id: str) -> GroceryListOverview:
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/grocery/lists/default/list-items",
        )
        return GroceryListOverview.model_validate(resp.json())

    def get_grocery_overview_v2(
        self,
        group_id: str,
        *,
        include_list_items: bool = True,
        include_categories: bool = True,
        include_group_items: bool = True,
        include_default_items: bool = True,
        include_loyalty_cards: bool = False,
    ) -> GroceryOverview:
        params = {
            "includeListItems": str(include_list_items).lower(),
            "includeCategories": str(include_categories).lower(),
            "includeGroupItems": str(include_group_items).lower(),
            "includeDefaultItems": str(include_default_items).lower(),
            "includeLoyaltyCards": str(include_loyalty_cards).lower(),
        }
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/grocery/overview",
            params=params,
        )
        return GroceryOverview.model_validate(resp.json())

    def update_grocery_item_v2(
        self,
        group_id: str,
        item: GroceryItem,
    ) -> GroceryItemMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/grocery/items/{item.id}",
            json=self._model_body(item),
        )
        return GroceryItemMutationResult.model_validate(resp.json())

    def delete_grocery_item_v2(
        self,
        group_id: str,
        item_id: str,
    ) -> GroceryItemMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/grocery/items/{item_id}",
        )
        return GroceryItemMutationResult.model_validate(resp.json())

    def add_grocery_list_item_v2(
        self,
        group_id: str,
        item: CreateGroceryListItemRequest,
    ) -> GroceryListItemMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/grocery/lists/default/list-items",
            json=self._model_body(item),
        )
        return GroceryListItemMutationResult.model_validate(resp.json())

    def add_grocery_list_items_v2(
        self,
        group_id: str,
        items: CreateGroceryListItemsRequest,
    ) -> GroceryListItemsMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/grocery/lists/default/list-items/batch",
            json=self._model_body(items),
        )
        return GroceryListItemsMutationResult.model_validate(resp.json())

    def update_grocery_list_item_v2(
        self,
        group_id: str,
        item: GroceryListItem,
    ) -> GroceryListItemMutationResult:
        item_id = self._require_resource_id("grocery list item", item.id)
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/grocery/lists/default/list-items/{item_id}",
            json=self._model_body(item),
        )
        return GroceryListItemMutationResult.model_validate(resp.json())

    def set_grocery_list_item_checked_v2(
        self,
        group_id: str,
        item_id: str,
        *,
        is_checked: bool,
    ) -> GroceryListItemCheckResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/grocery/lists/default/list-items/{item_id}/check",
            json={"isChecked": is_checked},
        )
        return GroceryListItemCheckResult.model_validate(resp.json())

    def get_loyalty_cards_v2(self, group_id: str) -> LoyaltyCardOverview:
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/grocery/loyalty-cards",
        )
        return LoyaltyCardOverview.model_validate(resp.json())

    def create_loyalty_card_v2(
        self,
        group_id: str,
        card: LoyaltyCard,
    ) -> LoyaltyCardMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/grocery/loyalty-cards",
            json=self._model_body(card),
        )
        return LoyaltyCardMutationResult.model_validate(resp.json())

    def update_loyalty_card_v2(
        self,
        group_id: str,
        card: LoyaltyCard,
    ) -> LoyaltyCardMutationResult:
        card_id = self._require_resource_id("loyalty card", card.id)
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/grocery/loyalty-cards/{card_id}",
            json=self._model_body(card),
        )
        return LoyaltyCardMutationResult.model_validate(resp.json())

    def delete_loyalty_card_v2(
        self,
        group_id: str,
        card_id: str,
    ) -> LoyaltyCardMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/grocery/loyalty-cards/{card_id}",
        )
        return LoyaltyCardMutationResult.model_validate(resp.json())

    def reorder_loyalty_cards_v2(
        self,
        group_id: str,
        ordered_ids: list[str],
    ) -> LoyaltyCardReorderResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/grocery/loyalty-cards/reorder",
            json={"orderedIds": ordered_ids},
        )
        return LoyaltyCardReorderResult.model_validate(resp.json())

    # ────────────── meal plan / recipes ──────────────

    @staticmethod
    def _model_body(
        model: (
            Meal
            | MealCategory
            | MealPlanEntry
            | MealDetail
            | MealCategoryV2
            | Checklist
            | ChecklistCreateRequest
            | ChecklistSyncRequest
            | GroceryItem
            | GroceryListItem
            | CreateGroceryListItemRequest
            | CreateGroceryListItemsRequest
            | LoyaltyCard
        ),
    ) -> dict:
        """Match the app's generated ``toJson`` output, including null fields."""
        return model.model_dump(mode="json")

    @staticmethod
    def _require_resource_id(resource_name: str, resource_id: str | None) -> str:
        if resource_id is None or not resource_id.strip():
            raise ValueError(f"{resource_name}.id is required for this operation")
        return resource_id

    def get_meal_plan_overview(
        self,
        group_id: str,
        *,
        start_date: date,
        end_date: date,
    ) -> MealPlanOverview:
        resp = self._request(
            "GET",
            f"/api/groups/{group_id}/meal-plan/overview",
            params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
        )
        return MealPlanOverview.model_validate(resp.json())

    def create_meal_category(
        self,
        group_id: str,
        category: MealCategory,
    ) -> MealCategory:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/meal-plan/categories",
            json=self._model_body(category),
        )
        return MealCategory.model_validate(resp.json())

    def update_meal_category(
        self,
        group_id: str,
        category: MealCategory,
    ) -> MealCategory:
        category_id = self._require_resource_id("category", category.id)
        resp = self._request(
            "PUT",
            f"/api/groups/{group_id}/meal-plan/categories/{category_id}",
            json=self._model_body(category),
        )
        return MealCategory.model_validate(resp.json())

    def delete_meal_category(self, group_id: str, category_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/groups/{group_id}/meal-plan/categories/{category_id}",
        )

    def create_meal(self, group_id: str, meal: Meal) -> Meal:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/meal-plan/meal",
            json=self._model_body(meal),
        )
        return Meal.model_validate(resp.json())

    def update_meal(self, group_id: str, meal: Meal) -> Meal:
        meal_id = self._require_resource_id("meal", meal.id)
        resp = self._request(
            "PUT",
            f"/api/groups/{group_id}/meal-plan/meal/{meal_id}",
            json=self._model_body(meal),
        )
        return Meal.model_validate(resp.json())

    def delete_meal(self, group_id: str, meal_id: str) -> None:
        self._request("DELETE", f"/api/groups/{group_id}/meal-plan/meal/{meal_id}")

    # The smartphone app introduced a separate full-recipe API in v1.5.2.
    # Keep these methods explicitly suffixed so the legacy meal-plan summary
    # methods above remain backwards compatible.

    def get_meals_v2(
        self,
        group_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        category_id: str | None = None,
        name: str | None = None,
        defaults_for_language: str | None = None,
        liked_by_profile_id: str | None = None,
    ) -> PaginatedMeals:
        params: dict[str, str | int] = {"page": page, "pageSize": page_size}
        optional_params = {
            "Filter.CategoryId": category_id,
            "Filter.Name": name,
            "Filter.DefaultsForLanguage": defaults_for_language,
            "Filter.LikedByProfileId": liked_by_profile_id,
        }
        params.update({key: value for key, value in optional_params.items() if value is not None})
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/meals",
            params=params,
        )
        return PaginatedMeals.model_validate(resp.json())

    def get_meals_overview_v2(
        self,
        group_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        defaults_for_language: str | None = None,
    ) -> MealsOverview:
        params: dict[str, str | int] = {
            "mealsPage": page,
            "mealsPageSize": page_size,
        }
        if defaults_for_language is not None:
            params["defaultsForLanguage"] = defaults_for_language
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/meals/overview",
            params=params,
        )
        return MealsOverview.model_validate(resp.json())

    def get_meal_v2(self, group_id: str, meal_id: str) -> MealMutationResult:
        resp = self._request("GET", f"/api/v2/groups/{group_id}/meals/{meal_id}")
        return MealMutationResult.model_validate(resp.json())

    def create_meal_v2(self, group_id: str, meal: MealDetail) -> MealMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/meals",
            json=self._model_body(meal),
        )
        return MealMutationResult.model_validate(resp.json())

    def update_meal_v2(self, group_id: str, meal: MealDetail) -> MealMutationResult:
        meal_id = self._require_resource_id("meal", meal.id)
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/meals/{meal_id}",
            json=self._model_body(meal),
        )
        return MealMutationResult.model_validate(resp.json())

    def delete_meal_v2(self, group_id: str, meal_id: str) -> MealMutationResult:
        resp = self._request("DELETE", f"/api/v2/groups/{group_id}/meals/{meal_id}")
        return MealMutationResult.model_validate(resp.json())

    def create_meal_category_v2(
        self,
        group_id: str,
        category: MealCategoryV2,
    ) -> MealCategoryMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/meals/categories",
            json=self._model_body(category),
        )
        return MealCategoryMutationResult.model_validate(resp.json())

    def update_meal_category_v2(
        self,
        group_id: str,
        category: MealCategoryV2,
    ) -> MealCategoryMutationResult:
        category_id = self._require_resource_id("category", category.id)
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/meals/categories/{category_id}",
            json=self._model_body(category),
        )
        return MealCategoryMutationResult.model_validate(resp.json())

    def delete_meal_category_v2(
        self,
        group_id: str,
        category_id: str,
    ) -> MealCategoryMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/meals/categories/{category_id}",
        )
        return MealCategoryMutationResult.model_validate(resp.json())

    def set_meal_likes_v2(
        self,
        group_id: str,
        meal_id: str,
        *,
        profile_ids: list[str],
    ) -> MealMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/meals/{meal_id}/likes",
            json={"profileIds": profile_ids},
        )
        return MealMutationResult.model_validate(resp.json())

    def upload_meal_picture_v2(
        self,
        group_id: str,
        meal_id: str,
        *,
        image_webp: bytes,
    ) -> MealMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/meals/{meal_id}/picture",
            files={
                "imageFile": ("meal_image.webp", image_webp, "image/webp"),
            },
        )
        return MealMutationResult.model_validate(resp.json())

    def delete_meal_picture_v2(
        self,
        group_id: str,
        meal_id: str,
    ) -> MealMutationResult:
        resp = self._request(
            "DELETE",
            f"/api/v2/groups/{group_id}/meals/{meal_id}/picture",
        )
        return MealMutationResult.model_validate(resp.json())

    def create_meal_plan_entry(
        self,
        group_id: str,
        entry: MealPlanEntry,
    ) -> MealPlanEntry:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/meal-plan/entries",
            json=self._model_body(entry),
        )
        return MealPlanEntry.model_validate(resp.json())

    def replace_meal_plan_entry(
        self,
        group_id: str,
        entry: MealPlanEntry,
    ) -> MealPlanEntry:
        resp = self._request(
            "POST",
            f"/api/groups/{group_id}/meal-plan/entries/replace",
            json=self._model_body(entry),
        )
        return MealPlanEntry.model_validate(resp.json())

    def update_meal_plan_entry(
        self,
        group_id: str,
        entry: MealPlanEntry,
    ) -> MealPlanEntry:
        entry_id = self._require_resource_id("entry", entry.id)
        resp = self._request(
            "PUT",
            f"/api/groups/{group_id}/meal-plan/entries/{entry_id}",
            json=self._model_body(entry),
        )
        return MealPlanEntry.model_validate(resp.json())

    def delete_meal_plan_entry(
        self,
        group_id: str,
        entry_id: str,
        *,
        occurrence_date: date,
        delete_type: DeleteRecurrenceType,
    ) -> None:
        self._request(
            "DELETE",
            f"/api/groups/{group_id}/meal-plan/entries/{entry_id}/{occurrence_date.isoformat()}",
            params={"deleteType": int(delete_type)},
        )

    # The current smartphone service keeps recipes under `/meals` but moves
    # dated meal-plan entries to their own v2 resource.

    def get_meal_plan_entries_v2(
        self,
        group_id: str,
        *,
        week: date,
        include_meals: bool = True,
    ) -> MealPlanEntries:
        resp = self._request(
            "GET",
            f"/api/v2/groups/{group_id}/meal-plan/entries",
            params={
                "week": week.isoformat(),
                "includeMeals": str(include_meals).lower(),
            },
        )
        return MealPlanEntries.model_validate(resp.json())

    def create_meal_plan_entry_v2(
        self,
        group_id: str,
        entry: MealPlanEntry,
    ) -> MealPlanEntryMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/meal-plan/entries",
            json=self._model_body(entry),
        )
        return MealPlanEntryMutationResult.model_validate(resp.json())

    def replace_meal_plan_entry_v2(
        self,
        group_id: str,
        entry: MealPlanEntry,
    ) -> MealPlanEntryMutationResult:
        resp = self._request(
            "POST",
            f"/api/v2/groups/{group_id}/meal-plan/entries/replace",
            json=self._model_body(entry),
        )
        return MealPlanEntryMutationResult.model_validate(resp.json())

    def update_meal_plan_entry_v2(
        self,
        group_id: str,
        entry_id: str,
        *,
        recurrence: list[str],
    ) -> MealPlanEntryMutationResult:
        resp = self._request(
            "PUT",
            f"/api/v2/groups/{group_id}/meal-plan/entries/{entry_id}",
            json={"recurrence": recurrence},
        )
        return MealPlanEntryMutationResult.model_validate(resp.json())

    def delete_meal_plan_entry_v2(
        self,
        group_id: str,
        entry_id: str,
        *,
        occurrence_date: date,
        delete_type: DeleteRecurrenceType,
    ) -> MealPlanEntryMutationResult:
        resp = self._request(
            "DELETE",
            (
                f"/api/v2/groups/{group_id}/meal-plan/entries/"
                f"{entry_id}/{occurrence_date.isoformat()}"
            ),
            params={"deleteType": int(delete_type)},
        )
        return MealPlanEntryMutationResult.model_validate(resp.json())


__all__ = [
    "DEFAULT_API_BASE",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_OIDC_BASE",
    "DEFAULT_USER_AGENT",
    "DaelyAPIError",
    "DaelyAuthError",
    "DaelyClient",
]
