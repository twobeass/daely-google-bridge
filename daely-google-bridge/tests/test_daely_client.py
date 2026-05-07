"""DaelyClient tests — fully mocked via respx.

We never touch the real Daely backend in these tests. Anonymized fixtures
serve as response bodies.
"""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from daely_google_bridge.daely_client import (
    DEFAULT_API_BASE,
    DEFAULT_OIDC_BASE,
    DaelyAPIError,
    DaelyAuthError,
    DaelyClient,
)


FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures_anonymized"


def _fix(name: str) -> dict | list:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture()
def client():
    """Fast client (no pause) for testing."""
    c = DaelyClient(min_pause_seconds=0.0, max_retries=3)
    yield c
    c.close()


# ────────── ROPC + refresh ──────────

@respx.mock
def test_login_password_success(client):
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-1",
            "refresh_token": "RT-1",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "openid profile email offline_access",
        })
    )
    data = client.login_password("u@example.com", "secret")
    assert data["access_token"] == "AT-1"
    assert client.access_token == "AT-1"
    assert client.refresh_token == "RT-1"


@respx.mock
def test_login_password_invalid_grant_raises(client):
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    with pytest.raises(DaelyAuthError) as ei:
        client.login_password("u@example.com", "wrong")
    assert "invalid_grant" in str(ei.value)


@respx.mock
def test_refresh_success(client):
    client.set_tokens(access_token="AT-old", refresh_token="RT-old")
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-new",
            "refresh_token": "RT-new",
            "expires_in": 1800,
        })
    )
    data = client.refresh()
    assert data["access_token"] == "AT-new"
    assert client.access_token == "AT-new"
    assert client.refresh_token == "RT-new"


def test_refresh_without_token_raises(client):
    with pytest.raises(DaelyAuthError):
        client.refresh()


@respx.mock
def test_refresh_invalid_grant_raises(client):
    client.set_tokens(access_token="AT", refresh_token="RT-revoked")
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(DaelyAuthError):
        client.refresh()


# ────────── 401 transparent refresh ──────────

@respx.mock
def test_request_auto_refresh_on_401(client):
    client.set_tokens(access_token="AT-stale", refresh_token="RT-1")
    # First /api/users/me → 401
    me_route = respx.get(f"{DEFAULT_API_BASE}/api/users/me")
    me_route.side_effect = [
        httpx.Response(401, json={"error": "expired"}),
        httpx.Response(200, json=_fix("users_me.json")),
    ]
    # Refresh succeeds in between
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-fresh", "refresh_token": "RT-2", "expires_in": 1800,
        })
    )
    me = client.get_me()
    assert me.email == "user1@example.com"
    assert client.access_token == "AT-fresh"
    assert me_route.call_count == 2


@respx.mock
def test_request_401_only_retries_once(client):
    """If even after refresh we still get 401, surface it (no infinite loop)."""
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/users/me").mock(
        return_value=httpx.Response(401, json={"error": "still bad"})
    )
    respx.post(f"{DEFAULT_OIDC_BASE}/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-2", "refresh_token": "RT-2", "expires_in": 1800,
        })
    )
    with pytest.raises(DaelyAPIError) as ei:
        client.get_me()
    assert ei.value.status_code == 401


# ────────── 5xx exponential backoff ──────────

@respx.mock
def test_request_retries_on_5xx_then_succeeds(client, monkeypatch):
    """5xx triggers backoff; success on retry."""
    monkeypatch.setattr("time.sleep", lambda *_: None)  # no-op pauses
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/users/me").side_effect = [
        httpx.Response(503, json={"error": "down"}),
        httpx.Response(502, json={"error": "down"}),
        httpx.Response(200, json=_fix("users_me.json")),
    ]
    me = client.get_me()
    assert me.id == "00000000-0000-0000-0001-000000000001"


@respx.mock
def test_request_5xx_exhausts_retries_and_raises(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/users/me").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(DaelyAPIError):
        client.get_me()


# ────────── 4xx (other than 401) raises immediately ──────────

@respx.mock
def test_4xx_other_than_401_raises_no_retry(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    route = respx.get(f"{DEFAULT_API_BASE}/api/groups/me").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(DaelyAPIError) as ei:
        client.get_my_groups()
    assert ei.value.status_code == 404
    assert route.call_count == 1


# ────────── auth precondition ──────────

def test_request_without_token_raises(client):
    with pytest.raises(DaelyAuthError):
        client.get_me()


# ────────── typed read methods (full happy path) ──────────

@respx.mock
def test_get_me_parses_user(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/users/me").mock(
        return_value=httpx.Response(200, json=_fix("users_me.json"))
    )
    me = client.get_me()
    assert me.email == "user1@example.com"
    assert me.firstName == "FirstName1"
    assert me.locale == "de"


@respx.mock
def test_get_my_groups(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/groups/me").mock(
        return_value=httpx.Response(200, json=_fix("groups_me.json"))
    )
    groups = client.get_my_groups()
    assert len(groups) == 1
    assert groups[0].setupComplete is True


@respx.mock
def test_get_calendars(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    fix = _fix("group0_calendars.json")
    gid = "group-uuid"
    respx.get(f"{DEFAULT_API_BASE}/api/groups/{gid}/calendars").mock(
        return_value=httpx.Response(200, json=fix)
    )
    cals = client.get_calendars(gid)
    assert len(cals) == 3
    types = sorted(c.calendarType for c in cals)
    assert types == [0, 1, 1]
    # writeable spelling preserved
    assert all(c.writeable for c in cals)


@respx.mock
def test_get_calendars_with_events(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    fix = _fix("group0_calendars_with_events_v2_attempt0.json")
    gid = "group-uuid"
    route = respx.get(
        f"{DEFAULT_API_BASE}/api/groups/{gid}/calendars/with-events",
    ).mock(return_value=httpx.Response(200, json=fix))
    cwes = client.get_calendars_with_events(
        gid, start_date=date(2026, 4, 7), end_date=date(2026, 6, 6),
    )
    assert len(cwes) == 3
    total_events = sum(len(c.events) for c in cwes)
    assert total_events == 80
    # Verify query params went in.
    call = route.calls.last.request
    assert "startDate=2026-04-07" in str(call.url)
    assert "endDate=2026-06-06" in str(call.url)


@respx.mock
def test_get_external_accounts(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/external-accounts").mock(
        return_value=httpx.Response(200, json=_fix("external_accounts.json"))
    )
    accs = client.get_external_accounts()
    assert len(accs) == 1
    assert accs[0].accountType == 1


@respx.mock
def test_get_url_calendars_empty(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/url-calendars").mock(
        return_value=httpx.Response(200, json=_fix("url_calendars.json"))
    )
    urls = client.get_url_calendars()
    assert urls == []


@respx.mock
def test_get_profiles(client):
    client.set_tokens(access_token="AT", refresh_token="RT")
    fix = _fix("profiles_groups_gid.json")
    gid = "group-uuid"
    respx.get(f"{DEFAULT_API_BASE}/api/groups/{gid}/profiles").mock(
        return_value=httpx.Response(200, json=fix)
    )
    profiles = client.get_profiles(gid)
    assert len(profiles) == 5
    # Names round-trip through pydantic
    names = [p.name for p in profiles]
    assert all(isinstance(n, str) and n for n in names)
    # nullable userId tolerated
    assert any(p.userId is None for p in profiles)
    # colorCode kept as-is
    assert all(p.colorCode and p.colorCode.startswith("#") for p in profiles)


# ────────── min_pause_seconds is enforced ──────────

@respx.mock
def test_min_pause_is_enforced(monkeypatch):
    """Two consecutive calls must be separated by at least min_pause_seconds."""
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("time.sleep", fake_sleep)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    c = DaelyClient(min_pause_seconds=2.5, max_retries=1)
    c.set_tokens(access_token="AT", refresh_token="RT")
    respx.get(f"{DEFAULT_API_BASE}/api/users/me").mock(
        return_value=httpx.Response(200, json=_fix("users_me.json"))
    )
    c.get_me()
    c.get_me()
    # First call: pause is min_pause_seconds (since _last_call_at=0 and now=0).
    # Second call: same — both fully waited.
    assert any(s >= 2.5 for s in sleeps)
    c.close()


# ────────── header injected ──────────

@respx.mock
def test_authorization_bearer_header_sent(client):
    client.set_tokens(access_token="AT-the-best", refresh_token="RT")
    route = respx.get(f"{DEFAULT_API_BASE}/api/users/me").mock(
        return_value=httpx.Response(200, json=_fix("users_me.json"))
    )
    client.get_me()
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer AT-the-best"
    assert "User-Agent" in req.headers
    assert "daely-google-bridge" in req.headers["User-Agent"]
