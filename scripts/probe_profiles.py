#!/usr/bin/env python3
"""Phase 3e/A: probe candidate profile-listing endpoints against Daely.

Runs the existing DaelyClient against 5 candidate paths in order and persists
each response. Auth is bootstrapped from either:
- ~/projects/daely-re/daely-google-bridge/bridge.db (if it has a `daely` token), or
- ~/.daely-secrets/tokens.json (the Phase-3a live_read.py output).

The refresh-token is used to obtain a fresh access-token before probing.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# allow running without `pip install -e` of the bridge package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "daely-google-bridge" / "src"))

from daely_google_bridge.daely_client import DaelyAuthError, DaelyClient  # noqa: E402
from daely_google_bridge.store import Store  # noqa: E402

BRIDGE_DB = PROJECT_ROOT / "daely-google-bridge" / "bridge.db"
TOKENS_JSON = Path.home() / ".daely-secrets" / "tokens.json"
FIXTURES_PRIVATE = PROJECT_ROOT / "tests" / "fixtures_private"

PAUSE = 1.0
MAX_BODY_PREVIEW_CHARS = 800


def load_initial_tokens() -> tuple[str | None, str]:
    """Return (access_token_or_None, refresh_token). Prefers bridge.db."""
    if BRIDGE_DB.exists():
        with Store(BRIDGE_DB) as store:
            rec = store.get_token("daely")
        if rec is not None:
            print(f"[init] loaded tokens from {BRIDGE_DB}")
            return rec.access_token, rec.refresh_token
    if TOKENS_JSON.exists():
        data = json.loads(TOKENS_JSON.read_text())
        rt = data.get("refresh_token")
        if not rt:
            sys.exit(f"FATAL: {TOKENS_JSON} has no refresh_token")
        print(f"[init] loaded tokens from {TOKENS_JSON}")
        return data.get("access_token"), rt
    sys.exit(
        "FATAL: no tokens available. Either run `bridge bootstrap`, or restore "
        f"~/.daely-secrets/tokens.json from a previous live_read run."
    )


def save_tokens_back(daely: DaelyClient) -> None:
    """Persist refreshed tokens to whichever source we read from."""
    if BRIDGE_DB.exists():
        with Store(BRIDGE_DB) as store:
            store.put_token(
                provider="daely",
                refresh_token=daely.refresh_token,
                access_token=daely.access_token,
            )
        print(f"[init] tokens saved to {BRIDGE_DB}")
        return
    # tokens.json
    if TOKENS_JSON.exists():
        existing = json.loads(TOKENS_JSON.read_text())
        existing["access_token"] = daely.access_token
        existing["refresh_token"] = daely.refresh_token
        TOKENS_JSON.write_text(json.dumps(existing, indent=2))
        TOKENS_JSON.chmod(0o600)
        print(f"[init] tokens saved to {TOKENS_JSON}")


def find_group_id() -> str | None:
    """Read group id from the Phase-3a fixture if available."""
    fix = FIXTURES_PRIVATE / "groups_me.json"
    if not fix.exists():
        return None
    data = json.loads(fix.read_text())
    if data and isinstance(data, list) and isinstance(data[0], dict):
        return data[0].get("id")
    return None


def probe(daely: DaelyClient, name: str, path: str) -> tuple[int, int, dict | list | str | None]:
    print(f"\n[probe] {name}: GET {path}")
    try:
        resp = daely._request("GET", path)
        status = resp.status_code
        size = len(resp.content)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
    except Exception as e:  # DaelyAPIError or others
        status = getattr(e, "status_code", -1)
        size = 0
        body = repr(e)
        if hasattr(e, "body"):
            body = {"error_repr": repr(e), "body": e.body}
    print(f"[probe] {name}: status={status} size={size}B")
    out_path = FIXTURES_PRIVATE / f"probe_{name}.json"
    out_path.write_text(
        json.dumps(body, indent=2, ensure_ascii=False)
        if not isinstance(body, str)
        else body
    )
    if isinstance(body, (dict, list)):
        preview = json.dumps(body, indent=2, ensure_ascii=False)
    else:
        preview = str(body)
    print(f"[probe] {name}: preview (first {MAX_BODY_PREVIEW_CHARS} chars)\n"
          + preview[:MAX_BODY_PREVIEW_CHARS])
    return status, size, body


def main() -> None:
    FIXTURES_PRIVATE.mkdir(parents=True, exist_ok=True)
    access_token, refresh_token = load_initial_tokens()

    daely = DaelyClient(min_pause_seconds=PAUSE)
    daely.set_tokens(access_token=access_token, refresh_token=refresh_token)

    print("[init] proactive refresh")
    try:
        token_response = daely.refresh()
        print(f"[init] refresh ok, expires_in={token_response.get('expires_in')}s")
    except DaelyAuthError as e:
        sys.exit(f"FATAL: refresh failed: {e}")
    save_tokens_back(daely)

    gid = find_group_id()
    if not gid:
        sys.exit("FATAL: cannot determine group_id; need tests/fixtures_private/groups_me.json")
    print(f"[init] group_id={gid}")

    summary: list[tuple[str, int, int]] = []

    # (a) groups/<gid>/profiles
    s, sz, _ = probe(daely, "a_groups_gid_profiles", f"/api/groups/{gid}/profiles")
    summary.append(("a_groups_gid_profiles", s, sz))
    time.sleep(PAUSE)

    if s == 200:
        print("\n[done] endpoint (a) succeeded — no need to try the rest.")
    else:
        # (b) /api/profiles
        s, sz, _ = probe(daely, "b_profiles", "/api/profiles")
        summary.append(("b_profiles", s, sz))
        time.sleep(PAUSE)

        # (c) /api/groups/<gid>
        s, sz, _ = probe(daely, "c_groups_gid", f"/api/groups/{gid}")
        summary.append(("c_groups_gid", s, sz))
        time.sleep(PAUSE)

        # (d) /api/groups/<gid>/members
        s, sz, _ = probe(daely, "d_groups_gid_members", f"/api/groups/{gid}/members")
        summary.append(("d_groups_gid_members", s, sz))
        time.sleep(PAUSE)

        # (e) /api/groups/me re-check
        s, sz, _ = probe(daely, "e_groups_me_recheck", "/api/groups/me")
        summary.append(("e_groups_me_recheck", s, sz))

    daely.close()

    print("\n[summary]")
    for name, status, size in summary:
        print(f"  {name:30s} {status:4d}  {size}B")


if __name__ == "__main__":
    main()
