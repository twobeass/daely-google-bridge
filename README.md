# daely-google-bridge

> One-way sync from a [Dæly® Calendar](https://daely-shop.com) family setup to
> Google Calendar — so events you create on the wall-mounted Daely tablet show
> up in the same calendar app you already use everywhere else.

```
   ┌──────────────────┐                     ┌──────────────────┐
   │  Dæly Calendar   │   read-only sync    │ Google Calendar  │
   │  (family tablet) │   ──────────────►   │ (per profile)    │
   └──────────────────┘                     └──────────────────┘
       source of truth                       what you check
                                             on the go
```

The bridge runs as a small Docker container, polls Daely's API on an interval,
maps each event to Google Calendar — including a profile-name footer
(„👥 Beteiligt: Anna, Bob") so you can tell at a glance who's involved.

## Why

We bought a Dæly Calendar for the kitchen. It's a great wall device — a 15.6"
touch screen that shows the whole family's schedule in one glance — and the
companion app is fine for adding events on the go. But it has two friction
points for people who already live in Google Calendar:

1. **One-way visibility.** The Daely tablet shows what's on it, but events
   added there don't surface in our regular Google Calendar app. Checking
   "what's coming up this week" then needs two phones.
2. **Sub-calendar layout.** Daely thinks in profiles per family member, while
   Google Calendar lets us share, colour-code, and route per-person calendars.
   The natural mapping is one Google sub-calendar per Daely profile.

This bridge does exactly one thing: every event on a Daely *internal*
calendar is mirrored into a Google sub-calendar (one per profile, plus a
"Family" fallback). It's deliberately **one-way only** — Google Calendar
shouldn't change Daely; Daely is the source of truth. (If you want the other
direction, Daely already supports CalDAV/Google subscriptions natively;
this project doesn't try to replace that.)

## What you need

- A Dæly Calendar account (email + password, no MFA — see *Limitations* below)
- A [Google Cloud project](https://console.cloud.google.com/) with the
  Calendar API enabled and an OAuth 2.0 *Desktop application* client. Download
  the `client_secret_*.json` and rename it to `google_oauth_client.json`.
- A Linux host with Docker (a small VPS, a Raspberry Pi, an old laptop, a NAS
  that supports Docker — anything works). Running 24/7 makes most sense.
- An SSH client on your local machine that supports port forwarding (every
  default `ssh` does).

That's it.

## Quick start (Docker)

On the host that will run the bridge:

```bash
# 1. Get the code
git clone https://github.com/twobeass/daely-google-bridge.git
cd daely-google-bridge/daely-google-bridge

# 2. Layout
mkdir -p data secrets
sudo chown -R 1100:1100 data            # container's bridge user is uid 1100
chmod 700 secrets

cp config.docker.example.yaml config.yaml
${EDITOR:-vi} config.yaml                # set daely_email; rest can stay default

# 3. Drop the OAuth client JSON next to the bridge
cp /path/to/google_oauth_client.json secrets/
chmod 600 secrets/google_oauth_client.json

# 4. Build the image
docker compose build
```

Now the **one-time bootstrap**, with a port forward back to your laptop so
the OAuth consent redirect can complete:

```bash
# from your local machine, in a separate terminal:
ssh -L 8080:localhost:8080 user@docker-host
```

Then on the host:

```bash
docker compose run --rm --service-ports bootstrap
```

The script will:
1. Ask for your Daely password (no echo).
2. Print a Google consent URL — open it in your **local** browser.
3. After you approve, the redirect lands on `localhost:8080` → through the SSH
   tunnel → into the container → done.
4. Create one Google sub-calendar per Daely profile + a `Daely – Family`
   fallback.
5. Persist refresh tokens in `data/bridge.db` and write the
   profile→calendar mapping into `config.yaml` (with a `.bak` backup).

After bootstrap, **start the long-running sync**:

```bash
docker compose up -d
docker compose logs -f bridge | jq -R 'fromjson? // .'
```

That's the whole setup. The bridge does an initial full sync, then re-syncs
every 15 minutes (configurable in `config.yaml`).

## What the bridge does and doesn't do

✅ Reads internal Daely calendars (`calendarType=0`) and forwards every event
   to Google Calendar.\
✅ Routes events to per-profile Google sub-calendars based on the profile
   mapping bootstrap creates.\
✅ Server-expanded recurring events get deduplicated to a master event with the
   original `RRULE` so Google handles expansion.\
✅ Resolves profile UUIDs into a friendly *„👥 Beteiligt: …"* footer in the
   event description.\
✅ Detects deletions (events removed in Daely disappear from Google on the
   next full sync).\
✅ Idempotent: every sync converges; runs are safe to repeat.

❌ **Does not** push Google changes back to Daely. If you need that, use Daely's
   built-in CalDAV/Google integration in the official app — the bridge stays
   out of the way.\
❌ **Does not** sync photos. Daely's 15-image limit is a separate problem;
   tackling it would require image uploads against the Daely API and is out
   of scope here.\
❌ **Does not** support MFA-protected Daely accounts. The login uses ROPC
   (Resource Owner Password Credentials), which Keycloak rejects when MFA is
   enabled. Workaround if you want both: keep MFA off for the technical
   account that owns the bridge and use a different account for day-to-day
   tablet use, or wait for a future device-flow implementation.

## Project layout

```
.
├── README.md                 — this file
├── LICENSE                   — MIT
├── daely-google-bridge/      — the bridge itself: Python package + Dockerfile
│   ├── README.md             — developer-oriented docs
│   ├── src/, tests/          — code + 131 tests, ~91% coverage
│   └── docker-compose.yml
├── findings/                 — reverse-engineering writeups that informed the
│                              bridge (08 markdown files); not needed to use
│                              the bridge, useful if you want to extend it
├── scripts/                  — the live-read & anonymisation tools used to
│                              produce the test fixtures
└── tests/fixtures_anonymized/ — anonymised snapshots of real Daely API
                                 responses; the test suite runs against these
                                 fully offline
```

The technical bridge documentation lives in
[`daely-google-bridge/README.md`](daely-google-bridge/README.md) — including
running tests, mapping decisions, and a troubleshooting section.

The story of how the API was figured out lives under
[`findings/`](findings/) — start with `00_OVERVIEW.md`.

## How was this built

Daely's official app is a Flutter (Dart-AOT) Android binary, so a typical
APK-decompile workflow doesn't show much. The approach used here was:

1. **Static analysis with [blutter](https://github.com/worawit/blutter)** on
   the `libapp.so` to recover Dart class layouts, model fields, enum values,
   and string constants. Got us to ~80% of the picture.
2. **A small live-read script** with explicit per-call user approval that
   exercised each candidate endpoint once, captured the JSON response, and
   filled in the gaps blutter couldn't.
3. **Anonymisation tooling** that replaces personal UUIDs, names, emails, and
   sync tokens in those captures with deterministic placeholders, so the test
   suite can run against real-shape data without ever shipping real data.
4. **A Python bridge** built outside-in: pure functions first (mapper,
   models), then a SQLite store, then HTTP clients with respx- and
   `unittest.mock`-driven tests, then a sync orchestrator, then OAuth + CLI,
   then Docker.

131 tests run offline. No live calls in CI.

## Disclaimer & legal

- This project is **not affiliated with** daely-shop.com or Moonlight Studio.
  „Dæly" and the product trademarks belong to their respective owners.
- The bridge only operates on accounts and hardware that the operator owns.
  It uses the same public API the official app uses; it does not exploit any
  vulnerability and does not bypass any access control.
- The findings in `findings/` describe the API surface as observed from a
  legitimate authenticated session. They do not include credentials, tokens,
  or any other personal data.
- If you operate this against an account you don't own, that's on you.
- The bridge is provided AS-IS under the [MIT license](LICENSE). It writes
  events to your Google Calendar; before pointing it at a calendar you care
  about, run `bridge run --once` once and inspect the output.

## Contributing

Issues and PRs welcome. The test suite (`pytest -q` from inside
`daely-google-bridge/`) is a good entry point — every behavioural change has
a test. If you're adding a feature that talks to the network, please mock the
network in tests; live calls in CI are out of scope for this repo.

If your contribution requires a new fixture, please run it through
`scripts/anonymize_fixtures.py` before committing — never commit raw API
responses.

## Status

| Phase | Status | Scope |
|---|---|---|
| 3a | ✅ | live read & fixture anonymisation |
| 3b | ✅ | mapper + store, pure logic |
| 3c | ✅ | HTTP clients + bootstrap CLI |
| 3d | ✅ | sync orchestrator + scheduler |
| 3e | ✅ | profile-footer in event descriptions |
| 3f | ✅ | Dockerfile + compose |
| next | — | one-shot `bridge resync <cal_id>` for forced re-pushes |
