"""Google Calendar v3 client.

Wraps `google-api-python-client` and `google-auth-oauthlib` with a thin,
easily-mockable surface. Only the methods the bridge actually needs.

Authentication flow:
- First-time setup: `authorize_via_local_server()` opens a local HTTP listener
  on a random port, prints the consent URL for the user, and exchanges the
  resulting code for a refresh token. The refresh token is persisted via the
  Store.
- Subsequent runs: `load_credentials()` pulls the refresh token from the Store
  and constructs a `google.oauth2.credentials.Credentials` instance. The
  google-auth library refreshes the AT automatically when needed.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .store import Store

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PROVIDER = "google"

log = structlog.get_logger(__name__)


class GoogleClient:
    """Calendar-v3 client backed by a service-resource the caller can mock."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        service: Any | None = None,
    ) -> None:
        self._credentials = credentials
        # Allow injection of a mock resource for tests.
        self._service = service or build(
            "calendar", "v3", credentials=credentials, cache_discovery=False,
        )

    # ─────────────── auth helpers ───────────────

    @staticmethod
    def authorize_via_local_server(
        client_secrets_path: Path | str,
        *,
        scopes: list[str] | None = None,
    ) -> Credentials:
        """Run the InstalledAppFlow on a fixed local port (headless-friendly).

        Tailored for headless VM use:
        - Port hard-coded to 8080 so the user's SSH tunnel
          (`ssh -L 8080:localhost:8080 user@vm`) always lines up.
        - `open_browser=False` because there is no browser on the VM. The user
          opens the printed URL on their local machine.
        - Bind host is `localhost` by default. Inside Docker, set
          `BRIDGE_OAUTH_HOST=0.0.0.0` so Docker's port-mapping can route
          external traffic to the in-container listener.

        On success a refresh-token-bearing `Credentials` instance is returned.
        Caller is expected to persist it via `persist_credentials()`.
        """
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path),
            scopes=scopes or DEFAULT_SCOPES,
        )
        bind_host = os.environ.get("BRIDGE_OAUTH_HOST", "localhost")
        creds = flow.run_local_server(
            host=bind_host,
            port=8080,
            open_browser=False,
            authorization_prompt_message=(
                "\n=== Google OAuth ===\n"
                "1. Stelle sicher, dass dein SSH-Tunnel offen ist:\n"
                "     ssh -L 8080:localhost:8080 user@vm-ip\n"
                "2. Öffne diese URL im Browser auf deinem LOKALEN Rechner:\n\n"
                "     {url}\n\n"
                "3. Bei der 'App nicht verifiziert'-Warnung:\n"
                "     Erweitert -> Weiter zu Daely Google Bridge\n"
                "4. Berechtigungen 'calendar' bestätigen.\n"
                "Warte auf Redirect (Browser sollte 'Erfolg' zeigen)..."
            ),
            success_message=(
                "Login erfolgreich. Du kannst dieses Browserfenster schließen "
                "und zur VM-Konsole zurückkehren."
            ),
        )
        return creds

    @staticmethod
    def persist_credentials(creds: Credentials, store: Store) -> None:
        """Save refresh+access tokens to the Store under provider="google"."""
        if not creds.refresh_token:
            raise ValueError(
                "google credentials have no refresh_token; "
                "did you forget access_type=offline or did the user previously "
                "approve without offline access?"
            )
        expires_at = creds.expiry
        if expires_at is not None and expires_at.tzinfo is None:
            # google-auth sometimes returns naive UTC; normalise.
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        store.put_token(
            provider=TOKEN_PROVIDER,
            refresh_token=creds.refresh_token,
            access_token=creds.token,
            expires_at=expires_at,
        )

    @staticmethod
    def load_credentials(
        store: Store,
        client_secrets_path: Path | str,
        *,
        scopes: list[str] | None = None,
    ) -> Credentials | None:
        """Reconstruct a Credentials object from the Store.

        Returns None if no token is persisted yet (caller should run
        `authorize_via_local_server`). The returned Credentials will refresh
        the access token automatically when used.
        """
        record = store.get_token(TOKEN_PROVIDER)
        if record is None:
            return None
        # Read client_id/client_secret from the secrets file the user downloaded
        # from Google Cloud Console — google-auth needs these to refresh.
        import json
        secrets_data = json.loads(Path(client_secrets_path).read_text())
        installed = secrets_data.get("installed") or secrets_data.get("web") or {}
        creds = Credentials(
            token=record.access_token,
            refresh_token=record.refresh_token,
            token_uri=installed.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=installed["client_id"],
            client_secret=installed["client_secret"],
            scopes=scopes or DEFAULT_SCOPES,
        )
        # Force a refresh if expired (google-auth would do this lazily on first
        # request; doing it eagerly here makes startup errors surface clearly).
        if creds.expired or not creds.valid:
            try:
                creds.refresh(Request())
                # write the freshened access token back to the store
                GoogleClient.persist_credentials(creds, store)
            except Exception:
                log.exception("google.refresh_failed")
                return None
        return creds

    # ─────────────── calendar list/create ───────────────

    def list_calendars(self) -> list[dict]:
        """Returns Google `calendarList` items the user can read."""
        out: list[dict] = []
        page_token: str | None = None
        while True:
            kwargs = {"pageToken": page_token} if page_token else {}
            resp = self._service.calendarList().list(**kwargs).execute()
            out.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def create_calendar(
        self,
        summary: str,
        time_zone: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a secondary calendar. Returns the new Calendar resource (incl. id)."""
        body: dict[str, str] = {"summary": summary}
        if time_zone:
            body["timeZone"] = time_zone
        if description:
            body["description"] = description
        return self._service.calendars().insert(body=body).execute()

    # ─────────────── event CRUD ───────────────

    def insert_event(self, calendar_id: str, body: dict) -> dict:
        return self._service.events().insert(calendarId=calendar_id, body=body).execute()

    def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        return self._service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body,
        ).execute()

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event. 404 (already gone) is treated as success."""
        try:
            self._service.events().delete(
                calendarId=calendar_id, eventId=event_id,
            ).execute()
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or e.resp.status == 404:
                log.info(
                    "google.delete_event.404_silent",
                    calendar_id=calendar_id, event_id=event_id,
                )
                return
            raise


__all__ = [
    "DEFAULT_SCOPES",
    "TOKEN_PROVIDER",
    "GoogleClient",
]
