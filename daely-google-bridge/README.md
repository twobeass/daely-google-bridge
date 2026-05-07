# daely-google-bridge

One-way sync from a [Dæly Calendar](https://daely-shop.com) family to Google Calendar.

**Implementation status: Phase 3f – Docker-Deploy komplett.** The bridge is
end-to-end with profile-name display, packaged as a multi-stage Docker image
with a `docker-compose.yml` for production use. `bridge bootstrap` provisions
the Google sub-calendars, `bridge run` performs an initial full sync and then
keeps incremental sync running on a configurable interval, and every event
synced into Google carries a footer listing the involved family members.

## What works

- ✅ ROPC login + automatic refresh-token handling for Daely
- ✅ All 6 read endpoints typed via pydantic models
- ✅ 401 transparent refresh, 5xx exponential backoff, 1 s min-pause
- ✅ Google OAuth headless-VM-ready (port 8080 + SSH tunnel)
- ✅ `bridge bootstrap` discovers profiles and creates Google sub-calendars
- ✅ `bridge status` shows token state + mapping count
- ✅ `bridge run [--once]` — full sync + scheduled incremental sync
- ✅ Insert / Patch / Delete / No-op choreography with per-event error isolation
- ✅ Master-only recurrence handling (RRULE passes through to Google)
- ✅ Mapper (Daely event → Google body) — pure function, fixture-tested
- ✅ Store (SQLite) — 3 tables, idempotent UPSERTs
- ✅ Profile resolution — `additionalParticipants` UUIDs become a German
  description footer („👥 Beteiligt: Anna, Bob")

## What's still planned

- `bridge resync <calendar_id>` — currently a stub
- Dockerfile / systemd unit (Phase 3e)

## Quickstart (local development)

Requires Python 3.12.

```bash
# from repo root (daely-re/)
cd daely-google-bridge
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -v       # 115 tests, all green
```

## End-to-end run on a real (headless) VM

You need:
- A Daely account (email + password)
- Google Cloud OAuth client secrets JSON (Desktop application type), saved at
  the path you set in `config.yaml` (`google_oauth_client_secrets_file`).
- An SSH tunnel forwarding port 8080 from your local machine to the VM:
  ```bash
  ssh -L 8080:localhost:8080 user@vm-ip
  ```

## Docker deployment

The bridge ships with a multi-stage `Dockerfile` and a `docker-compose.yml`
that defines three services:

| Service | Profile | What it does |
|---|---|---|
| `bridge` | (default) | Long-running sync loop, `restart: unless-stopped` |
| `bootstrap` | `bootstrap` | One-shot interactive setup, exposes port 8080 for OAuth |
| `status` | `ops` | Prints store state, then exits |

### Layout next to the compose file

```
deployment-dir/
├── docker-compose.yml          (copied from the repo)
├── config.yaml                 (you edit; bootstrap fills it in)
├── data/                       (mounted at /data — bridge.db lives here)
└── secrets/
    └── google_oauth_client.json   (the JSON from Google Cloud Console)
```

### One-time bootstrap

1. Copy `config.docker.example.yaml` → `./config.yaml`, edit `daely_email`
   and check the secrets file path is `/secrets/google_oauth_client.json`.
2. Drop your `google_oauth_client.json` into `./secrets/`.
3. Make sure the SSH tunnel forwards 8080 to the host running Docker:
   ```bash
   ssh -L 8080:localhost:8080 user@docker-host
   ```
4. Run the interactive bootstrap container:
   ```bash
   docker compose run --rm --service-ports bootstrap
   ```
   - It prompts for your Daely password (no echo).
   - It prints the Google consent URL — open it in **your local** browser.
   - After consent, the redirect goes to `localhost:8080`, the SSH tunnel
     forwards to the host, the host forwards (via `--service-ports`) to the
     container, where `BRIDGE_OAUTH_HOST=0.0.0.0` lets the in-container
     listener accept it.
   - Bootstrap creates Google sub-calendars, writes the mapping to
     `config.yaml` (with a `.bak` backup), persists tokens in
     `data/bridge.db`, and exits.

### Day-to-day operation

```bash
# Start the long-running sync loop (incremental every poll_interval_minutes)
docker compose up -d

# Tail logs (JSON because log_format: json)
docker compose logs -f bridge | jq -R 'fromjson? // .'

# Show what the store knows
docker compose --profile ops run --rm status

# Stop / restart
docker compose down
docker compose restart bridge
```

### Volume permissions

The container runs as uid 1100 (`bridge`). On the host, make sure `./data/`
is writeable for that uid:

```bash
mkdir -p data secrets
chmod 700 secrets
# after dropping config.yaml + google_oauth_client.json into place:
sudo chown -R 1100:1100 data secrets
sudo chown 1100:1100 config.yaml
```

The chown applies to **all three** paths:
- `data/` — container writes `bridge.db` here.
- `secrets/` — container reads `google_oauth_client.json` here.
- `config.yaml` — bootstrap writes the Google calendar IDs back into
  this file after creating the sub-calendar(s).

Skipping `secrets/` causes a `PermissionError` reading the OAuth client
JSON; skipping `config.yaml` causes a `PermissionError` writing the
mapping back at the end of bootstrap.

If you can't change ownership (e.g. because the host has a strict uid map),
edit the Dockerfile's `useradd --uid 1100` to match your host user.

### Image size

```
$ docker images daely-google-bridge
REPOSITORY              TAG         CONTENT SIZE
daely-google-bridge     0.1         ~80 MB
```

Multi-stage build keeps the runtime layer minimal — only Python 3.12-slim
plus the venv with the bridge's dependencies. Tests, fixtures, and dev
tooling are excluded via `.dockerignore`.

### Patching to a new version

```bash
git pull                             # in the repo containing Dockerfile
docker compose build --no-cache      # rebuild image
docker compose up -d                 # picks up the new image, keeps data/
```

`bridge.db` and `secrets/` are bind-mounted, so they survive image rebuilds
and container recreations untouched.

### Docker troubleshooting

**"connection refused" when consent redirects to localhost:8080**
The SSH tunnel isn't active or `--service-ports` was forgotten. Re-run
bootstrap with `docker compose run --rm --service-ports bootstrap`.

**"permission denied" writing to /data**
Host directory ownership doesn't match the container's uid 1100. Fix with
`sudo chown -R 1100:1100 ./data`.

**Container restarts in a loop**
Likely a credentials problem. Run `docker compose --profile ops run --rm status`
to see whether tokens are present. If `daely refresh-token: MISSING`, run
bootstrap again.

**OAuth flow times out after 60 seconds**
The Google `InstalledAppFlow` has a default timeout. Reset by re-running
bootstrap; the container exits cleanly so it's safe to retry.



### 1. Bootstrap (one-time)

```bash
cp config.example.yaml config.yaml
# edit config.yaml: set daely_email + google_oauth_client_secrets_file path

bridge bootstrap
```

Bootstrap walks through:
1. Daely password prompt → ROPC → refresh token persisted in `bridge.db`
2. Daely group + calendars discovery
3. Google OAuth via SSH-tunnelled browser (the CLI prints the consent URL,
   you open it locally, approve, the browser redirects to `localhost:8080`,
   the SSH tunnel forwards that to the VM)
4. For each Daely profile, an empty Google sub-calendar is created (or
   reused if one with the same `Daely – <Profile>` name already exists);
   plus a fallback `Daely – Family` calendar for un-profiled events.
5. The mapping is written to `config.yaml` (with a `.bak` backup).

Bootstrap is idempotent — re-running reuses what's already there.

### 2. Run

```bash
# one-off:
bridge run --once

# daemon mode (full sync once, then incremental every poll_interval_minutes):
bridge run
```

`bridge run` (without `--once`) runs in the foreground. Send `SIGTERM` or
`Ctrl+C` for graceful shutdown.

Per cycle the bridge prints a structured report:

```
sync run_id=ab12cd34ef56
  inserts:        4
  patches:        1
  deletes:        0
  no-ops:         42
  skip-external:  37
  skip-no-target: 0
  errors:         0
  duration:       2.41s
```

### 3. Status

```bash
bridge status
```

Shows token presence, count of event mappings, and whether the bootstrap has
been run.

## Mapping decisions (summary)

The mapping is documented in detail in `../findings/05_EVENT_MODEL.md` and
`../findings/06_BRIDGE_ARCHITECTURE.md`. Key choices:

- **Calendar filter**: only events from Daely calendars with `calendarType=0`
  (internal) are forwarded. Externally-synced calendars (Google/MS/Apple
  already wired up via Daely's integrations) are skipped to prevent loops.
- **Recurrence**: master-only. The earliest server-expanded instance per series
  is forwarded with its original RRULE; Google does the rest of the expansion.
  Mappings are stored under the master UUID, not the composite instance id, so
  shifting sync windows don't create duplicates.
- **Profile routing**: one Google sub-calendar per Daely profile. Fallback
  calendar for un-profiled events.
- **Daely-only fields** (id, recurringId, calendar id, profile id, additional
  participants, customColorCode, privateEvent, hasError) are mirrored to
  `extendedProperties.private.daely_*` for diagnostic visibility.
- **Privacy**: `privateEvent: true` → `visibility: "private"` in Google.
- **Reminders**: Daely's `List[int]` minutes → Google `reminders.overrides[]`
  with `method: "popup"`, `useDefault: false`.
- **Profile footer (Phase 3e)**: when an event has `additionalParticipants`
  (a list of profile UUIDs) and the bridge has fetched the profile list from
  `GET /api/groups/<gid>/profiles`, the resolved names are appended to the
  event's `description` as `\n\n👥 Beteiligt: <name>, <name>, …` (sorted
  case-insensitively). Unknown UUIDs are silently dropped. The original UUIDs
  remain in `extendedProperties.private.daely_additional_participants` for
  diagnostics.

### Example: profile footer

A Daely event with `title="Schwimmkurs"`, `description="Treffpunkt Foyer"`,
and `additionalParticipants=["<uuid-of-Anna>", "<uuid-of-Bob>"]` becomes:

```
title:       Schwimmkurs
description: Treffpunkt Foyer

             👥 Beteiligt: Anna, Bob
```

If the original description is empty, only the footer is set:

```
title:       Schultag
description: 👥 Beteiligt: Anna
```

## Sync semantics

| Sync type | Window | Deletion detection |
|---|---|---|
| `full_sync` | `[today − lookback_days, today + lookahead_days]` | `deleted=true` flags **AND** snapshot-vs-store diff |
| `incremental_sync` | `[today − 1 day, today + 30 days]` | only `deleted=true` flags |

Long-term physical deletions outside the incremental window are caught by the
next `full_sync` (which `bridge run` invokes once at startup; in production
you'd schedule another full_sync nightly via cron or apscheduler).

State per event in `bridge.db.event_mapping`:
- `daely_id` (master UUID for recurring; event id otherwise) — primary key
- `daely_calendar_id`, `google_event_id`, `google_calendar_id`
- `last_seen_updated` — Daely's `CalendarEvent.updated`. Drives patch decisions.
- `last_synced_at` — wall-clock timestamp, diagnostic.
- `failed` — sticky flag; useful when extending the bridge with retry logic.

## Repo layout

```
daely-google-bridge/
├── pyproject.toml
├── config.example.yaml
├── README.md                          # ← this file
├── src/daely_google_bridge/
│   ├── __init__.py
│   ├── __main__.py                    # python -m daely_google_bridge ...
│   ├── cli.py                         # argparse + bootstrap + run + status
│   ├── config.py                      # BridgeConfig + YAML I/O
│   ├── daely_client.py                # Daely backend client (ROPC + 6 reads)
│   ├── google_client.py               # Google Calendar v3 wrapper
│   ├── mapper.py                      # pure: Daely event → Google body
│   ├── models.py                      # pydantic models
│   ├── store.py                       # SQLite (3 tables, idempotent)
│   └── sync.py                        # orchestration: full + incremental
└── tests/
    ├── conftest.py
    ├── test_cli.py
    ├── test_config.py
    ├── test_daely_client.py           # respx-mocked
    ├── test_google_client.py          # MagicMock-mocked
    ├── test_google_oauth_flow.py      # InstalledAppFlow params
    ├── test_mapper.py
    ├── test_store.py
    └── test_sync.py                   # all 115 tests live here
```

## Test coverage (Phase 3e)

```
src/daely_google_bridge/__init__.py           100%
src/daely_google_bridge/cli.py                 80%
src/daely_google_bridge/config.py             100%
src/daely_google_bridge/daely_client.py        90%
src/daely_google_bridge/google_client.py       91%
src/daely_google_bridge/mapper.py              96%
src/daely_google_bridge/models.py             100%
src/daely_google_bridge/store.py               99%
src/daely_google_bridge/sync.py                95%
TOTAL                                          91%   (130 tests)
```

**No live calls in tests** — Daely is mocked via [respx](https://github.com/lundberg/respx),
Google via `unittest.mock`.

## Troubleshooting

### `bridge run` exits with "no Daely refresh-token in store"
Run `bridge bootstrap` first. The token is persisted only after a successful
ROPC login during bootstrap.

### `bridge bootstrap` fails with `invalid_grant`
- Either the password is wrong, or
- MFA is enabled on the Daely account (ROPC won't work in that case — the
  bridge currently has no second-factor path; you'd have to disable MFA on
  the test account).

### Google consent URL doesn't open / browser shows "site can't be reached"
Make sure the SSH tunnel is active **before** running `bridge bootstrap`:
```bash
ssh -L 8080:localhost:8080 user@vm-ip
```
The bridge listens on port 8080 of the VM; the tunnel forwards your local
:8080 there. If the tunnel breaks mid-flow, restart bootstrap.

### `bridge run` reports `skip-no-target: N` for some events
A Daely calendar's `profileId` isn't mapped to a Google calendar in
`config.yaml::profile_calendar_mapping`. Either:
- re-run `bridge bootstrap` to detect the new profile, or
- manually add the mapping; or
- set `fallback_google_calendar_id` to capture all unmapped events.

### Events appear duplicated after recurrence change
The bridge stores mappings under the master UUID (not the composite instance
id). If Daely returns a different recurrence rule on the same series, the
patch will rewrite the existing Google event. If you ever see a duplicated
series in Google, you can inspect `bridge.db` (sqlite3 CLI) to see whether
two mappings reference the same `recurringId` and clean it up manually.

### How do I force a re-sync after editing the mapping?
Right now: stop `bridge run`, optionally delete entries from
`bridge.db::event_mapping` for the calendars you want re-synced, then
`bridge run --once`. A proper `bridge resync <cal_id>` is still on the
roadmap (Phase 3f).

### After upgrading to Phase 3e: how do I get the new profile footers onto already-synced events?

The patch-trigger uses `event.updated` from Daely vs. the cached
`last_seen_updated` in the store. Daely-side events haven't changed (Daely
doesn't know about the bridge's footer logic), so step 4 of the sync
choreography ("no-op when updated == last_seen_updated") fires and the
new footer is **not** applied to existing mappings.

To force a one-time re-patch of all events:

```bash
sqlite3 bridge.db "UPDATE event_mapping SET last_seen_updated = NULL;"
```

The next `bridge run` then sees `existing.last_seen_updated (None) !=
event.updated (datetime)` for every event and patches them all. The
mapping ids stay intact, so no Google duplicates are created — the same
Google events are simply rewritten with the new description.

Same trick applies whenever the mapper logic changes in a way Daely can't
trigger (e.g. future toString/format adjustments). It's a controlled,
diagnosable workaround; keep it documented but don't bake it into the
sync loop — the auto-rewrite would mask actual mapper bugs.

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| 3a — live read & fixtures | ✅ done | ROPC, sample API reads, anonymization, doc |
| 3b — mapper + store | ✅ done | pure logic, offline-testable |
| 3c — clients + bootstrap | ✅ done | DaelyClient, GoogleClient, CLI bootstrap |
| 3d — orchestration | ✅ done | full / incremental sync, scheduler |
| 3e — profile footer | ✅ done | resolved profile UUIDs become a description footer |
| **3f — Docker deploy** | ✅ **done** | Dockerfile, compose, OAuth-host env override |
| 3g — operational polish | ⏳ next | `bridge resync`, retry-on-failed, optional systemd unit |

## Background

This package is part of a larger reverse-engineering project for the Dæly
Calendar Android app. The findings, including the static analysis (blutter on
the Flutter `libapp.so`) and the live-read writeups, are in `../findings/`.

The user is the legitimate owner of the hardware and the account; the work is
about interoperability with their own data, not adversarial activity.
