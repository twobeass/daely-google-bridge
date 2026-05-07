"""GoogleClient tests with mock Resource — no real Google API calls."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from daely_google_bridge.google_client import TOKEN_PROVIDER, GoogleClient
from daely_google_bridge.store import Store


# ────────── builders for mock service ──────────

def _mock_service(
    *,
    calendar_list_pages: list[dict] | None = None,
    insert_returns: dict | None = None,
    patch_returns: dict | None = None,
    delete_raises: Exception | None = None,
    create_calendar_returns: dict | None = None,
) -> MagicMock:
    """Build a mock googleapiclient resource that mimics .execute() chains."""
    service = MagicMock()

    # calendarList().list().execute() with pagination support
    if calendar_list_pages is not None:
        list_calls = [MagicMock(execute=MagicMock(return_value=p)) for p in calendar_list_pages]
        service.calendarList.return_value.list.side_effect = list_calls

    # calendars().insert(body=...).execute()
    if create_calendar_returns is not None:
        service.calendars.return_value.insert.return_value.execute.return_value = create_calendar_returns

    # events().insert(...).execute()
    if insert_returns is not None:
        service.events.return_value.insert.return_value.execute.return_value = insert_returns

    # events().patch(...).execute()
    if patch_returns is not None:
        service.events.return_value.patch.return_value.execute.return_value = patch_returns

    # events().delete(...).execute()
    if delete_raises is not None:
        service.events.return_value.delete.return_value.execute.side_effect = delete_raises

    return service


def _http_error(status: int, msg: str = "err") -> HttpError:
    resp = httplib2.Response({"status": str(status)})
    resp.reason = msg
    return HttpError(resp, content=msg.encode())


def _dummy_credentials() -> MagicMock:
    """Just enough of a Credentials object for GoogleClient(credentials=...)."""
    creds = MagicMock()
    creds.token = "ya29.fake"
    creds.refresh_token = "1//rt-fake"
    creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    creds.valid = True
    creds.expired = False
    return creds


# ────────── list_calendars (paging) ──────────

def test_list_calendars_single_page():
    svc = _mock_service(calendar_list_pages=[{
        "items": [{"id": "cal-1", "summary": "S1"}, {"id": "cal-2", "summary": "S2"}],
    }])
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    cals = gc.list_calendars()
    assert [c["id"] for c in cals] == ["cal-1", "cal-2"]


def test_list_calendars_multi_page():
    svc = _mock_service(calendar_list_pages=[
        {"items": [{"id": "cal-1"}], "nextPageToken": "tok-1"},
        {"items": [{"id": "cal-2"}, {"id": "cal-3"}]},
    ])
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    cals = gc.list_calendars()
    assert [c["id"] for c in cals] == ["cal-1", "cal-2", "cal-3"]


def test_list_calendars_empty():
    svc = _mock_service(calendar_list_pages=[{}])
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    assert gc.list_calendars() == []


# ────────── create_calendar ──────────

def test_create_calendar_minimal():
    svc = _mock_service(create_calendar_returns={
        "id": "new-cal@group.calendar.google.com",
        "summary": "Daely – Profile 1",
    })
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    res = gc.create_calendar("Daely – Profile 1")
    assert res["id"].endswith("@group.calendar.google.com")
    body_arg = svc.calendars.return_value.insert.call_args.kwargs["body"]
    assert body_arg == {"summary": "Daely – Profile 1"}


def test_create_calendar_with_timezone_and_description():
    svc = _mock_service(create_calendar_returns={"id": "x"})
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    gc.create_calendar(
        summary="t", time_zone="Europe/Berlin", description="a daely sub-cal",
    )
    body = svc.calendars.return_value.insert.call_args.kwargs["body"]
    assert body["timeZone"] == "Europe/Berlin"
    assert body["description"] == "a daely sub-cal"


# ────────── insert/patch/delete events ──────────

def test_insert_event():
    svc = _mock_service(insert_returns={"id": "evt-1", "summary": "x"})
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    res = gc.insert_event("cal-A", {"summary": "x", "start": {}, "end": {}})
    assert res["id"] == "evt-1"


def test_patch_event():
    svc = _mock_service(patch_returns={"id": "evt-1", "summary": "y"})
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    res = gc.patch_event("cal-A", "evt-1", {"summary": "y"})
    assert res["summary"] == "y"
    kwargs = svc.events.return_value.patch.call_args.kwargs
    assert kwargs["calendarId"] == "cal-A"
    assert kwargs["eventId"] == "evt-1"


def test_delete_event_normal():
    svc = MagicMock()
    svc.events.return_value.delete.return_value.execute.return_value = ""
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    gc.delete_event("cal-A", "evt-1")
    svc.events.return_value.delete.assert_called_once()


def test_delete_event_404_is_silent():
    svc = _mock_service(delete_raises=_http_error(404))
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    gc.delete_event("cal-A", "evt-already-gone")  # must not raise


def test_delete_event_other_error_propagates():
    svc = _mock_service(delete_raises=_http_error(403, "forbidden"))
    gc = GoogleClient(credentials=_dummy_credentials(), service=svc)
    with pytest.raises(HttpError):
        gc.delete_event("cal-A", "evt-x")


# ────────── persist_credentials / load_credentials ──────────

def test_persist_credentials_writes_to_store(tmp_path):
    store = Store(":memory:")
    creds = MagicMock()
    creds.refresh_token = "rt-x"
    creds.token = "at-x"
    creds.expiry = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    GoogleClient.persist_credentials(creds, store)
    rec = store.get_token(TOKEN_PROVIDER)
    assert rec.refresh_token == "rt-x"
    assert rec.access_token == "at-x"
    store.close()


def test_persist_credentials_without_refresh_raises():
    creds = MagicMock()
    creds.refresh_token = None
    with pytest.raises(ValueError):
        GoogleClient.persist_credentials(creds, Store(":memory:"))


def test_load_credentials_returns_none_when_store_empty(tmp_path):
    store = Store(":memory:")
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {
        "client_id": "cid", "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}))
    assert GoogleClient.load_credentials(store, secrets) is None
    store.close()


def test_load_credentials_reconstructs_from_store(tmp_path, monkeypatch):
    store = Store(":memory:")
    store.put_token(
        provider=TOKEN_PROVIDER,
        refresh_token="rt-x",
        access_token="at-x",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {
        "client_id": "cid", "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}))
    creds = GoogleClient.load_credentials(store, secrets)
    assert creds is not None
    assert creds.refresh_token == "rt-x"
    assert creds.client_id == "cid"
    store.close()


def test_load_credentials_uses_web_section_if_no_installed(tmp_path):
    store = Store(":memory:")
    store.put_token(
        provider=TOKEN_PROVIDER,
        refresh_token="rt-x",
        access_token="at-x",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"web": {
        "client_id": "cid-web", "client_secret": "csec-web",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}))
    creds = GoogleClient.load_credentials(store, secrets)
    assert creds.client_id == "cid-web"
    store.close()
