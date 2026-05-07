#!/usr/bin/env python3
"""
Follow-up: probe the correct group-list endpoint and continue the read pass.

Reuses the access_token from tokens.json. Tries /api/groups/me first
(per blutter analysis of GroupRestService.getAvailableGroups, which builds
URL "${base}/me" where base is /api/groups).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = Path.home() / ".daely-secrets" / "tokens.json"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures_private"

API_BASE = "https://daely-connect.com"
USER_AGENT = "daely-google-bridge/0.1 (research; tobi)"
PAUSE = 1.0
MAX_BYTES = 5 * 1024 * 1024


def load_token() -> str:
    data = json.loads(TOKENS_FILE.read_text())
    return data["access_token"]


def update_meta(entry: dict) -> None:
    meta_path = FIXTURES_DIR / "_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["calls"].append(entry)
    meta_path.write_text(json.dumps(meta, indent=2))


def do_get(client: httpx.Client, name: str, path: str, *, params=None, bearer: str) -> tuple[int, object]:
    url = f"{API_BASE}{path}"
    print(f"[get ] {name}: GET {path} {params or ''}".rstrip())
    resp = client.get(url, params=params, headers={
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json",
    })
    size = len(resp.content)
    print(f"[get ] {name}: HTTP {resp.status_code}, {size} bytes")
    entry = {
        "name": name, "path": path, "params": params,
        "status": resp.status_code, "content_length": size,
        "content_type": resp.headers.get("content-type"),
    }
    update_meta(entry)
    if size > MAX_BYTES:
        sys.exit("FATAL: response too large")
    if resp.status_code != 200:
        out = FIXTURES_DIR / f"{name}.error.json"
        try:
            out.write_text(json.dumps(resp.json(), indent=2, ensure_ascii=False))
        except Exception:
            out.write_text(resp.text)
        return resp.status_code, None
    try:
        data = resp.json()
    except json.JSONDecodeError:
        (FIXTURES_DIR / f"{name}.txt").write_text(resp.text)
        return resp.status_code, resp.text
    (FIXTURES_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return resp.status_code, data


def main() -> None:
    token = load_token()

    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        # Probe 1: /api/groups/me (per blutter getAvailableGroups → "${base}/me")
        status, groups = do_get(client, "groups_me", "/api/groups/me", bearer=token)
        time.sleep(PAUSE)

        # Fallback only if 404
        if status != 200:
            status, groups = do_get(client, "users_me_groups", "/api/users/me/groups", bearer=token)
            time.sleep(PAUSE)

        if status != 200 or not groups:
            print("[error] could not list groups via either path; stopping group-dependent probes")
            return

        # Extract group IDs
        group_ids: list[str] = []
        candidates = groups if isinstance(groups, list) else (
            groups.get("data") or groups.get("groups") or groups.get("items") or []
        )
        for g in candidates:
            if isinstance(g, dict) and (gid := g.get("id") or g.get("groupId")):
                group_ids.append(str(gid))
        print(f"[init] discovered {len(group_ids)} groups")

        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        date_to = (now + timedelta(days=30)).isoformat().replace("+00:00", "Z")

        for idx, gid in enumerate(group_ids):
            tag = f"group{idx}"
            do_get(client, f"{tag}_calendars", f"/api/groups/{gid}/calendars", bearer=token)
            time.sleep(PAUSE)

            for ai, params in enumerate([
                {"from": date_from, "to": date_to},
                {"start": date_from, "end": date_to},
                {"timeMin": date_from, "timeMax": date_to},
                None,
            ]):
                status, _ = do_get(
                    client, f"{tag}_calendars_with_events_attempt{ai}",
                    f"/api/groups/{gid}/calendars/with-events",
                    params=params, bearer=token,
                )
                time.sleep(PAUSE)
                if status == 200:
                    break

            do_get(
                client, f"{tag}_check_update",
                f"/api/groups/{gid}/calendars/check-update",
                params={"internal": "", "external": ""}, bearer=token,
            )
            time.sleep(PAUSE)

    print("[done] follow-up complete")


if __name__ == "__main__":
    main()
