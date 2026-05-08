# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in diesem File festgehalten.

Format orientiert sich an [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versionsnummern folgen [Semantic Versioning 2.0](https://semver.org/spec/v2.0.0.html).

Image-Tag pro Release: `ghcr.io/twobeass/daely-google-bridge:vX.Y.Z` (zusätzlich
zu `:latest`). Wer pinnen will, kann auf einen konkreten Release-Tag fixieren.

## [Unreleased]

### Hinzugefügt
- Schema-Migration-Framework in `store.py`: forward-only, beim `Store()`-Init
  automatisch angewendet. Vor jedem Upgrade auf File-DBs ein Best-Effort-Backup
  unter `bridge.db.bak.v<N>-<timestamp>`. Bestehende pre-framework-DBs werden
  als v1 erkannt, ohne dass die Initial-Migration neu läuft.
- `Store.schema_version`, `Store.migrated_from_version`, `Store.last_backup_path`
  Properties zur Diagnose.
- `tests.yml` GitHub-Action: läuft `pytest` auf jedem Push und PR gegen `main`.
- `release.yml` GitHub-Action: bei Push eines `v*`-Tags automatisches GitHub-
  Release mit auto-generierten Notes.
- Diese `CHANGELOG.md`.
- **Schema v2** (Migration v2): drei neue Spalten auf `event_mapping`
  (`retry_after`, `retry_count`, `last_error`) + neue Tabelle `sync_history`
  als Audit-Log abgeschlossener Sync-Cycles.
- **Retry-Loop** für `failed=true`-Mappings: nach einem Patch-/Delete-Fehler
  wird der nächste Versuch exponentiell zurückgehalten (Default: 60s
  verdoppelnd, gedeckelt auf 1h). In der Cooldown-Zeit wird das Event
  übersprungen statt erneut gegen Google zu hämmern. Erfolgreiche Re-Patches
  setzen den Retry-Zustand automatisch zurück. Skip-Counter im `SyncReport`:
  `skipped_retry_cooldown`.
- **`bridge resync`** als echter Command (war Stub):
  `bridge resync [--calendar <daely_id>] [--dry-run]`. Setzt
  `last_seen_updated=NULL` auf den passenden Mapping-Rows, damit die nächste
  Sync-Runde sie mit aktueller Mapper-Output (z. B. neuen Farben) re-patcht.
- **`bridge re-color`** als Convenience-Alias für `bridge resync` über alle
  Calendars — discoverable Shortcut nach Profil-Color-Config-Änderungen.
- **Sync-History-Persistenz**: jeder Sync-Cycle schreibt eine Zeile in die
  neue `sync_history`-Tabelle. Auto-Pruning hält die letzten 500 Einträge.
  `Store.recent_sync_history(limit)` und `prune_sync_history(keep_last)` für
  Diagnose und Maintenance.
- **§1.4 Health-Check-HTTP-Endpoint** (opt-in, default off):
  - `GET /healthz` — 200 wenn letzter Sync innerhalb von `poll_interval × 2 + 60s`
  - `GET /readyz` — 200 wenn beide Refresh-Tokens (Daely + Google) im Store
  - `GET /status` — JSON mit Schema-Version, Mapping-Counts, letzten 10
    History-Einträgen
  - Bind default `127.0.0.1:8090`, in Config konfigurierbar (`health_server`-
    Sektion). Server läuft als Daemon-Thread parallel zum Sync-Loop.
- `BridgeState`-Object in `health_server.py` als Thread-safe Snapshot
  zwischen Sync-Loop und Health-Endpoint.

### Geändert
- `Store(...)` öffnet die SQLite-Connection mit `check_same_thread=False`,
  damit der Health-Server (worker threads) lesend auf den gleichen Store
  zugreifen kann. Writer bleibt der Sync-Loop (Single-Thread); SQLite WAL
  serialisiert konkurrente Schreiber sowieso.
- `SyncReport` hat ein neues Feld `skipped_retry_cooldown: int` — nicht
  breaking, ist Dataclass mit Default 0.

## [0.1.0] - 2026-05-08

Erstes getaggtes Release. Stand der Codebase nach Phase 3f
(Profil-Color-Mapping + Multi-Participant-Emoji-Prefix).

### Hinzugefügt
- Einseitiger Sync **Daely → Google Calendar** mit Polling (default alle
  15 min, `lookback_days=30`, `lookahead_days=365`).
- ROPC-Auth gegen Daely-Keycloak (`sso.daely-connect.com`, Realm `daely`,
  Client `mobile-app`). Refresh-Token-Rotation transparent.
- Google-OAuth via InstalledAppFlow, redirect_uri `http://localhost:<port>`,
  konfigurierbarer `oauth_local_port`.
- Bootstrap-CLI: Daely-Login → Google-OAuth → automatisches Anlegen pro Profil
  einer Google-Sub-Calendar → Persistenz der Mapping-Tabelle.
- Mapper:
  - `daely_event_to_google()` – pure function, alle 17 Daely-Event-Felder
    auf Google-Body abgebildet.
  - Recurring: master-only Strategie (`deduplicate_recurring()`), RRULE wird
    durchgereicht, Google expandiert.
  - Filter: Events aus externen Daely-Kalendern (`calendarType != 0`)
    werden übersprungen.
  - **Profil-Footer** (Phase 3e): `👥 Beteiligt: …` an die Description, Namen
    case-insensitive sortiert.
  - **Profil-Color-Mapping** (Phase 3f): Daely-`colorCode` → eine der 11
    Google-`colorId`s per nearest-RGB-Match. `profile_overrides` in der
    Config zum Pinnen einzelner Profile.
  - **Multi-Participant-Title-Prefix** (Phase 3f): bei ≥2 Beteiligten
    farbige Punkt-Emojis vor dem Titel, Reihenfolge identisch zum Footer.
  - `customColorCode` und Daely-only-Felder (`recurringId`, `profileId`, …)
    werden in `extendedProperties.private` gespiegelt für Diagnose.
- SQLite-Store (`bridge.db`), 3 Tabellen (`event_mapping`, `sync_state`,
  `tokens`), idempotente UPSERTs.
- Sync-Engine:
  - Polling-Loop (apscheduler).
  - `full_sync` mit Deletion-Detection per Snapshot-Diff.
  - `incremental_sync` (kürzeres Window, nur `deleted=true`-Events).
  - Per-Event-Error-Isolation: ein Failure stoppt den Cycle nicht.
- Multi-Arch-Docker-Image (amd64+arm64) via GHA, published auf `ghcr.io`
  bei jedem Push auf `main`.
- 193 Offline-Tests (`respx` + `unittest.mock`), ~91 % Coverage.
- Vollständige sanitisierte API-Dokumentation in `findings/00–08`.

### Bekannte Limitationen
- Kein MFA-Support (ROPC-Limitation). Workaround: MFA für den Bridge-Account
  deaktivieren.
- Keine Reverse-Sync (Google → Daely) — bewusst out-of-scope; Daely's eigene
  Google-/CalDAV-Integration übernimmt das, falls gewünscht.
- Kein Photo-Upload (ursprüngliches Mission-Ziel, zurückgestellt).

[Unreleased]: https://github.com/twobeass/daely-google-bridge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.0
