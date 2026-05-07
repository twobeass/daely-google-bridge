#!/usr/bin/env python3
"""
Final pass: corrected param names + per-calendar check-update path.

Per group0_calendars_with_events_attempt0.error.json:  needs valid StartDate/EndDate.
Per group0_check_update.error.json: 'check-update' got matched as calendarId, so
path is /api/groups/<gid>/calendars/<calendarId>/check-update.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
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
    return json.loads(TOKENS_FILE.read_text())["access_token"]


def update_meta(entry: dict) -> None:
    p = FIXTURES_DIR / "_meta.json"
    m = json.loads(p.read_text())
    m["calls"].append(entry)
    p.write_text(json.dumps(m, indent=2))


def do_get(client, name, path, *, params=None, bearer):
    url = f"{API_BASE}{path}"
    print(f"[get ] {name}: GET {path} {params or ''}".rstrip())
    resp = client.get(url, params=params, headers={
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json",
    })
    size = len(resp.content)
    print(f"[get ] {name}: HTTP {resp.status_code}, {size} bytes")
    update_meta({
        "name": name, "path": path, "params": params,
        "status": resp.status_code, "content_length": size,
        "content_type": resp.headers.get("content-type"),
    })
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
    cal_data = json.loads((FIXTURES_DIR / "group0_calendars.json").read_text())
    group_id = json.loads((FIXTURES_DIR / "groups_me.json").read_text())[0]["id"]
    calendars = cal_data if isinstance(cal_data, list) else []
    cal_ids = [c["id"] for c in calendars if isinstance(c, dict) and c.get("id")]
    print(f"[init] group_id={group_id}, {len(cal_ids)} calendars")

    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    end = (today + timedelta(days=30)).isoformat()

    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        # Retry with-events with corrected param names (StartDate/EndDate, plain ISO date)
        for ai, params in enumerate([
            {"startDate": start, "endDate": end},
            {"StartDate": start, "EndDate": end},  # case-sensitive variants per error message
        ]):
            status, _ = do_get(
                client, f"group0_calendars_with_events_v2_attempt{ai}",
                f"/api/groups/{group_id}/calendars/with-events",
                params=params, bearer=token,
            )
            time.sleep(PAUSE)
            if status == 200:
                break

        # Per-calendar check-update for each calendar
        for ci, cid in enumerate(cal_ids):
            do_get(
                client, f"group0_cal{ci}_check_update",
                f"/api/groups/{group_id}/calendars/{cid}/check-update",
                params={"internal": "", "external": ""}, bearer=token,
            )
            time.sleep(PAUSE)

    print("[done] events pass complete")


if __name__ == "__main__":
    main()
