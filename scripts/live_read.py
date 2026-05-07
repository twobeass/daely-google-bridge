#!/usr/bin/env python3
"""
Throwaway live-read script: ROPC-Login + read-only GETs against Daely backend.

Saves raw JSON responses as fixtures for local development of the bridge.
NEVER does POST/PUT/DELETE. NEVER commits credentials or tokens.

Usage:
    /home/claude/.venvs/daely/bin/python scripts/live_read.py

Reads:
    ~/.daely-secrets/credentials.env  (DAELY_EMAIL=..., DAELY_PASSWORD=...)

Writes:
    ~/.daely-secrets/tokens.json                     (chmod 600)
    tests/fixtures_private/<endpoint>.json           (raw responses)
    tests/fixtures_private/_meta.json                (HTTP status + size per call)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = Path.home() / ".daely-secrets"
CREDENTIALS_FILE = SECRETS_DIR / "credentials.env"
TOKENS_FILE = SECRETS_DIR / "tokens.json"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures_private"

API_BASE = "https://daely-connect.com"
OIDC_BASE = "https://sso.daely-connect.com/realms/daely/protocol/openid-connect"
CLIENT_ID = "mobile-app"
USER_AGENT = "daely-google-bridge/0.1 (research; tobi)"

PAUSE_SECONDS = 1.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def load_credentials() -> tuple[str, str]:
    if not CREDENTIALS_FILE.exists():
        sys.exit(f"FATAL: {CREDENTIALS_FILE} not found.")
    creds: dict[str, str] = {}
    for line in CREDENTIALS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        creds[key.strip()] = val.strip().strip('"').strip("'")
    email = creds.get("DAELY_EMAIL")
    password = creds.get("DAELY_PASSWORD")
    if not email or not password:
        sys.exit("FATAL: DAELY_EMAIL or DAELY_PASSWORD missing in credentials.env")
    return email, password


def save_tokens(token_response: dict) -> None:
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    payload = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token"),
        "expires_in": token_response.get("expires_in"),
        "token_type": token_response.get("token_type"),
        "scope": token_response.get("scope"),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    }
    TOKENS_FILE.write_text(json.dumps(payload, indent=2))
    TOKENS_FILE.chmod(0o600)


def ropc_login(client: httpx.Client, email: str, password: str) -> dict:
    print(f"[auth] POST {OIDC_BASE}/token (grant_type=password)")
    resp = client.post(
        f"{OIDC_BASE}/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": email,
            "password": password,
            "scope": "openid profile email offline_access",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    print(f"[auth] HTTP {resp.status_code}")
    if resp.status_code != 200:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        sys.exit(
            f"FATAL: ROPC failed. status={resp.status_code} body={body!r}\n"
            "  invalid_grant likely means: wrong password OR MFA enabled.\n"
            "  No retry attempted by design."
        )
    data = resp.json()
    print(f"[auth] OK — access_token expires in {data.get('expires_in')}s, scope={data.get('scope')}")
    return data


def fixtures_meta_init() -> dict:
    return {
        "obtained_at": datetime.now(timezone.utc).isoformat(),
        "api_base": API_BASE,
        "client_id": CLIENT_ID,
        "calls": [],
    }


def do_get(
    client: httpx.Client,
    name: str,
    path: str,
    *,
    params: dict | None = None,
    bearer: str,
    meta: dict,
) -> tuple[int, dict | list | str | None]:
    url = f"{API_BASE}{path}"
    print(f"[get ] {name}: GET {path} {params or ''}".rstrip())
    try:
        resp = client.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as e:
        print(f"[get ] {name}: HTTPError {e!r}")
        meta["calls"].append({"name": name, "path": path, "params": params, "error": repr(e)})
        return -1, None

    size = len(resp.content)
    print(f"[get ] {name}: HTTP {resp.status_code}, {size} bytes")
    meta_entry = {
        "name": name,
        "path": path,
        "params": params,
        "status": resp.status_code,
        "content_length": size,
        "content_type": resp.headers.get("content-type"),
    }
    meta["calls"].append(meta_entry)

    if size > MAX_RESPONSE_BYTES:
        print(f"FATAL: response > {MAX_RESPONSE_BYTES} bytes; aborting.")
        sys.exit(2)

    if resp.status_code != 200:
        # Persist 4xx/5xx body as fixture too — useful to know error shape
        out = FIXTURES_DIR / f"{name}.error.json"
        try:
            out.write_text(json.dumps(resp.json(), indent=2, ensure_ascii=False))
        except Exception:
            out.write_text(resp.text)
        print(f"[get ] {name}: persisted error body to {out.name}")
        return resp.status_code, None

    try:
        data = resp.json()
    except json.JSONDecodeError:
        print(f"[get ] {name}: non-JSON response, persisted as .txt")
        (FIXTURES_DIR / f"{name}.txt").write_text(resp.text)
        return resp.status_code, resp.text

    out = FIXTURES_DIR / f"{name}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[get ] {name}: persisted to {out.name}")
    return resp.status_code, data


def main() -> None:
    print("=" * 70)
    print("daely live-read (read-only, single-pass)")
    print("=" * 70)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    email, password = load_credentials()
    print(f"[init] email loaded (length {len(email)}); password loaded (length {len(password)})")

    meta = fixtures_meta_init()

    with httpx.Client(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        token_response = ropc_login(client, email, password)
        access_token = token_response["access_token"]
        save_tokens(token_response)
        print(f"[init] tokens saved to {TOKENS_FILE}")
        time.sleep(PAUSE_SECONDS)

        # (a) /api/users/me
        do_get(client, "users_me", "/api/users/me", bearer=access_token, meta=meta)
        time.sleep(PAUSE_SECONDS)

        # (b) /api/groups
        status, groups = do_get(client, "groups", "/api/groups", bearer=access_token, meta=meta)
        time.sleep(PAUSE_SECONDS)

        group_ids: list[str] = []
        if isinstance(groups, list):
            for g in groups:
                if isinstance(g, dict):
                    gid = g.get("id") or g.get("groupId") or g.get("uuid")
                    if gid:
                        group_ids.append(str(gid))
        elif isinstance(groups, dict):
            # maybe wrapped in {data: [...]}
            for key in ("data", "groups", "items", "results"):
                inner = groups.get(key)
                if isinstance(inner, list):
                    for g in inner:
                        if isinstance(g, dict):
                            gid = g.get("id") or g.get("groupId") or g.get("uuid")
                            if gid:
                                group_ids.append(str(gid))
                    break
        print(f"[init] found {len(group_ids)} groups")

        if not group_ids:
            print("[warn] no groups discovered; cannot fetch calendars / events")

        # Date window: ±30 days (UTC)
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        date_to = (now + timedelta(days=30)).isoformat().replace("+00:00", "Z")

        for idx, gid in enumerate(group_ids):
            tag = f"group{idx}"
            # (c) /api/groups/<gid>/calendars
            status, cals = do_get(
                client, f"{tag}_calendars", f"/api/groups/{gid}/calendars",
                bearer=access_token, meta=meta,
            )
            time.sleep(PAUSE_SECONDS)

            calendar_ids: list[str] = []
            if isinstance(cals, list):
                for c in cals:
                    if isinstance(c, dict):
                        cid = c.get("id")
                        if cid:
                            calendar_ids.append(str(cid))

            # (d) /api/groups/<gid>/calendars/with-events?<window>
            # Query param naming uncertain; try common variants. We'll attempt
            # 'from'/'to' first; if that 4xx's, fall back to an unparam'd call.
            params_attempts: list[dict | None] = [
                {"from": date_from, "to": date_to},
                {"start": date_from, "end": date_to},
                {"timeMin": date_from, "timeMax": date_to},
                None,
            ]
            for attempt_idx, params in enumerate(params_attempts):
                attempt_name = f"{tag}_calendars_with_events_attempt{attempt_idx}"
                status, payload = do_get(
                    client, attempt_name,
                    f"/api/groups/{gid}/calendars/with-events",
                    params=params,
                    bearer=access_token,
                    meta=meta,
                )
                time.sleep(PAUSE_SECONDS)
                if status == 200:
                    break
                if status == -1:
                    break

            # (e) /api/groups/<gid>/calendars/check-update
            do_get(
                client, f"{tag}_check_update",
                f"/api/groups/{gid}/calendars/check-update",
                params={"internal": "", "external": ""},
                bearer=access_token, meta=meta,
            )
            time.sleep(PAUSE_SECONDS)

        # (f) /api/external-accounts (no group needed)
        do_get(client, "external_accounts", "/api/external-accounts",
               bearer=access_token, meta=meta)
        time.sleep(PAUSE_SECONDS)

        # (g) /api/url-calendars
        do_get(client, "url_calendars", "/api/url-calendars",
               bearer=access_token, meta=meta)

    meta_path = FIXTURES_DIR / "_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"\n[done] meta written to {meta_path}")
    print(f"[done] {len(meta['calls'])} calls completed")
    print(f"[done] fixtures in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
