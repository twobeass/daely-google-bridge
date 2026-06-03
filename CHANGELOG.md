# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in diesem File festgehalten.

Format orientiert sich an [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versionsnummern folgen [Semantic Versioning 2.0](https://semver.org/spec/v2.0.0.html).

Image-Tag pro Release: `ghcr.io/twobeass/daely-google-bridge:vX.Y.Z` (zusätzlich
zu `:latest`). Wer pinnen will, kann auf einen konkreten Release-Tag fixieren.

## [Unreleased]

_(noch nichts.)_

## [1.6.0] - 2026-06-03

**Realtime-Push funktioniert — und ist jetzt Default.** Calendar-Änderungen
propagieren in Sekunden statt erst beim 15-Minuten-Poll. Das räumt die seit
v1.1.0 als „experimentell/tot" markierte SignalR-Integration endgültig auf:
sie war nie strukturell unmöglich, wir haben nur zwei Bugs übersehen.

### Behoben — zwei Ursachen, live diagnostiziert (2026-06-03)
- **`calendars: null`/`[]` heißt „subscribe zu KEINEN Kalendern".** Der
  SignalR-`SetFilter` wurde zwar immer mit `result:null` (Erfolg) bestätigt,
  aber ein leerer `calendars`-Array registriert die Connection für **nichts**
  — daher kamen in v1.0–v1.5 nie Notifications. Mit den **echten internen
  Calendar-UUIDs** im Filter kommen Pushes sofort (live verifiziert: Termin
  am Handy angelegt → Notification in ~1 s auf der Bridge-Connection).
  Die alte „Same-Account-Suppression"-Hypothese aus v1.1.0 war **falsch**.
- **Subject-Parsing war auf das falsche Format.** Das echte
  `ReceiveNotification`-Payload ist `{"resourceType": "Calendar", "subject":
  "calendar.calendar.<calId>.event.<evId>.<action>", "time": …}` — ein
  **punkt**-getrennter Pfad, nicht `calendar/event` mit Slash (so hatte's die
  statische RE vermutet). `is_calendar_event` hätte den echten Push verworfen,
  selbst wenn er angekommen wäre. Beides jetzt korrekt.

### Geändert
- **`realtime.enabled` Default `true`** (war `false`). Polling bleibt parallel
  als Safety-Net. Wer Realtime nicht will: `realtime.enabled: false`.
- **`calendar_filter_mode: auto`** (Default) holt jetzt automatisch die
  internen Calendar-UUIDs der Gruppe und subscribed darauf. Der alte
  `null`-Pfad (der nichts empfing) ist entfernt. `internal-only` ist ein
  Alias von `auto`; `explicit` nutzt `calendar_uuids` verbatim.
- `RealtimeEvent`-Modell auf das echte Wire-Format umgeschrieben:
  `resourceType`, dotted `subject`, `time`, plus Properties `domain`,
  `is_calendar_event`, `action` (created/updated/deleted), `event_id`,
  `calendar_id`. Die spekulativen Felder `topic`/`entityId`/`topicKind`/
  `topicKindId` (nie real) sind raus.
- Realtime-Client logt die volle Payload jetzt einmal pro `(domain, action)`
  statt pro Subject (das echte Subject enthält die Event-UUID, wäre also
  immer eindeutig).

### Hinzugefügt
- Realtime-Trigger nutzt `full_sync` (Deletion-Detection) — ein Push für ein
  gelöschtes Event propagiert die Löschung sofort. Debounce (Default 2 s)
  bündelt Event-Bursts zu einem Sync.

### Doku
- `findings/10_REALTIME_API.md` auf das verifizierte echte Protokoll
  aktualisiert (Subject-Format, calendars-Pflicht, Wire-Beispiel).
- README: Realtime-Abschnitt von „experimentell" auf „funktioniert, default an".

## [1.5.0] - 2026-06-03

**Behebt: gelöschte LETZTE Instanz einer endlichen Serie blieb in Google.**
Folgefund zu v1.4.0 — an echten Live-Daten („Musik" donnerstags) diagnostiziert.

### Behoben
- **EXDATE-Synthese erkennt jetzt eine gelöschte letzte Instanz endlicher
  Serien.** Bisher diffte `compute_series_exdates()` die RRULE nur über den
  *beobachteten* Bereich `[erste … letzte gelieferte Instanz]`. Eine gelöschte
  Instanz **hinter** der letzten überlebenden (z. B. Serie läuft per `UNTIL`
  bis 11.06., letzter gelieferter Termin ist der 28.05., gelöscht wurde der
  04.06.) lag außerhalb dieses Bereichs → wurde nie als Lücke erkannt → Google
  zeigte sie weiter. Neu: bei Serien mit explizitem `UNTIL` expandiert die
  Synthese bis zum echten Serienende — **gekappt durchs Sync-Fenster**, damit
  nie Termine außerhalb des abgefragten Bereichs fälschlich EXDATE't werden.
  Innerhalb des Fensters gilt: RRULE-erwartet aber von Daely nicht geliefert
  = gelöscht.

### Geändert
- `compute_series_exdates(instances, *, window_end=None)` und
  `exdates_by_recurring_id(events, *, window_end=None)` nehmen jetzt das
  Sync-Fenster-Ende; `_run_sync` reicht es durch. Ohne `window_end` bleibt das
  konservative Alt-Verhalten (nur beobachteter Bereich) — offene Serien (ohne
  `UNTIL`) werden grundsätzlich nicht über die letzte Instanz hinaus erweitert.
- 4 neue Tests (gelöschte Last-Instance mit UNTIL+Fenster erkannt; Fenster
  kappt UNTIL-Expansion; offene Serie nicht erweitert; window_end-Durchreichung).

### Verbleibende Grenze
- Eine gelöschte **erste** Instanz ist weiterhin nicht erkennbar (kein früherer
  Nachbar → unbekannter `dtstart`). In der Praxis selten.

## [1.4.0] - 2026-06-03

**Behebt: gelöschte Serien-Einzeltermine verschwanden nicht aus Google.**
Eine in Daely gelöschte einzelne Instanz einer weiterlaufenden Serie blieb
im Google-Kalender sichtbar — obwohl jeder Sync „0 errors" meldete.

### Behoben
- **No-op-Check ignorierte Body-Änderungen ohne `updated`-Bump.** Wenn der
  User eine einzelne Serien-Instanz löscht, lässt Daely sie lautlos aus der
  Expansion weg, **ohne** das `updated`-Feld des Serien-Masters zu ändern.
  Die §3.1-EXDATE-Synthese (v1.2.0) berechnete zwar korrekt die neue EXDATE,
  aber der alte No-op-Check (`last_seen_updated == event.updated`) verwarf den
  gerenderten Body → die EXDATE landete nie in Google → die gelöschte Instanz
  blieb sichtbar. Das war die eigentliche Ursache hinter der „nach dem Update
  einmal `bridge resync`"-Notiz aus v1.2.0 — sie traf aber **jede** künftige
  Einzel-Löschung, nicht nur die Migration.

### Hinzugefügt
- **`body_fingerprint` pro Event-Mapping** (Schema v3). Die Bridge speichert
  einen Hash des zuletzt nach Google gepushten Bodys und re-patcht, sobald
  sich der gewünschte Body ändert — auch wenn `updated` gleich bleibt. Fängt
  damit nicht nur gelöschte Serien-Instanzen, sondern generell jede
  Body-Änderung ohne `updated`-Bump (z. B. Farb-/Titeländerungen).
- **Self-healing-Migration:** bestehende Mappings haben `body_fingerprint =
  NULL` → sie re-patchen beim ersten Sync nach dem Update genau einmal und
  pendeln sich dann ein. **Kein manueller `bridge resync` nötig.**
- 6 neue Tests (Re-Patch bei Body-Änderung trotz gleichem `updated`,
  NULL-Fingerprint-Self-Heal, echter No-op mit passendem Fingerprint,
  Store-Roundtrip + Default-NULL).

## [1.3.0] - 2026-05-21

**Behebt stille Drift zwischen Daely und Google** — Änderungen und
Löschungen außerhalb eines schmalen Fensters wurden im laufenden Betrieb
nicht mehr propagiert, obwohl jeder Sync-Zyklus „0 errors" meldete.

### Behoben
- **`incremental_sync` ignorierte die Config.** Der 15-Minuten-Poll lief
  hart mit `lookback=1` / `lookahead=30` Tagen statt der konfigurierten
  `lookback_days` / `lookahead_days`. Ein in Daely geänderter Termin, der
  älter als gestern oder weiter als 30 Tage in der Zukunft lag, wurde so nie
  nach Google gespiegelt — bis zum nächsten Neustart (der einen `full_sync`
  triggert). Das Fenster folgt jetzt der Config; explizite kwargs übersteuern
  weiterhin (z. B. Tests).
- **Healthcheck nutzte `pgrep`**, das im Slim-Image nicht installiert ist —
  der Container war dauerhaft `unhealthy`, obwohl der Sync lief. Ersetzt
  durch `bridge doctor` (validiert Config/DB/Tokens und erkennt einen
  verklemmten Sync-Loop über die Staleness-Prüfung).

### Hinzugefügt
- **Periodischer `full_sync`-Scheduler-Job.** Bisher lief `full_sync` (mit
  Store-vs-Snapshot-Löscherkennung) nur einmal beim Start; physisch gelöschte
  Termine (ohne `deleted=true`-Flag) verschwanden erst beim nächsten Neustart
  aus Google. Neuer Job läuft standardmäßig alle 24 h.
- Config-Knopf `full_sync_interval_hours` (Default `24`; `0` schaltet den
  periodischen Job ab → Legacy-Verhalten, full_sync nur beim Start).
- Tests: Config-Fenster im incremental Poll, override via kwargs, full_sync-Job
  geplant bzw. via `=0` abgeschaltet.

## [1.2.0] - 2026-05-14

**Behebt §3.1** — aus einer Serie gelöschte Einzel-Termine bleiben nicht
mehr in Google hängen.

### Hinzugefügt
- **EXDATE-Synthese für gelöschte Serien-Instanzen.** Wenn der User auf
  dem Tablet eine einzelne Instanz einer wiederkehrenden Serie löscht,
  lässt Daely sie lautlos aus der Expansion weg — `RRULE` bleibt
  unverändert, kein `deleted`-Flag, kein `EXDATE` (per Live-Read
  bestätigt). Die Bridge spiegelte bisher nur den Master + `RRULE` →
  Google expandierte die Serie voll → die gelöschte Instanz blieb
  sichtbar.

  Neu: `mapper.compute_series_exdates()` expandiert die `RRULE` über den
  beobachteten Bereich, difft gegen die tatsächlich gelieferten Instanzen
  und synthetisiert `EXDATE;TZID=…`-Zeilen für die Lücken. Diese werden
  ans `recurrence`-Feld des Google-Master-Events gehängt. DST-sicher über
  Wall-Clock-Zeit-Arithmetik. 17 neue Tests.
- `mapper.exdates_by_recurring_id()` — gruppiert Events nach Serie und
  liefert `{recurringId: [EXDATE…]}` nur für Serien mit erkannten Lücken.
- `daely_event_to_google()` akzeptiert `recurrence_exdates`-kwarg.
- Neue Dependency: `python-dateutil>=2.9` (für RRULE-Expansion).
- `findings/10`-Style Live-Read-Befund in `findings/06_BRIDGE_ARCHITECTURE.md`
  dokumentiert (Probe 4 — Daely lässt gelöschte Instanzen weg, Hypothese A2).

### Geändert
- Realtime-Client (weiterhin experimentell) schickt den Access-Token jetzt
  zusätzlich als `?access_token=`-Query-Parameter — die kanonische
  SignalR-über-SSE-Konvention. Hat das Notification-Problem im Single-
  Account-Setup **nicht** gelöst (siehe v1.1.0-Caveat), ist aber
  protokoll-korrekter und schadet nicht.

### Bekannte Grenzen
- **Verschobene** (statt gelöschte) Instanzen werden noch nicht behandelt —
  separates Feature (modified-instance-exceptions). In den Live-Daten nicht
  beobachtet.
- Eine gelöschte **erste oder letzte** Instanz einer Serie ist nicht
  erkennbar (kein Nachbar zum Diffen).

### ⚠️ Nach dem Update: einmal `bridge resync`
Bestands-Serien bekommen die EXDATEs **nicht** automatisch — die Bridge
patcht nur, wenn sich Daely's `event.updated` ändert (Patch-Trigger-Gotcha).
Nach dem Update auf v1.2.0 einmalig:

```bash
docker compose exec bridge bridge resync
docker compose restart bridge
```

→ forciert beim nächsten Sync-Cycle ein Re-Patch aller Mappings, die
gelöschten Serien-Instanzen verschwinden dann aus Google.

## [1.1.0] - 2026-05-08

**Stable.** Schließt das Realtime-Kapitel mit ehrlicher Bestandsaufnahme
ab und positioniert die Bridge wieder klar am primären Use-Case
(Polling-basierter Sync) als Default.

### Geändert
- **Realtime-Push als experimentelles Feature markiert.** Trotz
  vollständiger SignalR-Protokoll-Implementierung (negotiate, SSE,
  handshake, SetFilter) und allen drei getesteten Filter-Formaten
  (`[]`, `[<uuid>]`, `null`) wurde in keinem Live-Test je eine echte
  `ReceiveNotification` vom Server empfangen — selbst bei aktiven
  Tablet-Edits.

  Wahrscheinlichste Ursache (siehe `findings/10_REALTIME_API.md`):
  Daelys Realtime-Server fired Push-Notifications nicht zurück an
  weitere Connections desselben User-Accounts. Im Single-Daely-Account-
  Setup ist die Bridge dadurch strukturell ohne Realtime — Polling
  alle 15 Min bleibt der primäre Sync-Mechanismus.

  Code-seitig ändert sich nichts: `realtime.enabled` ist weiterhin
  Default `false`. Wer testen will (z. B. Multi-Account-Setup), kann
  opt-in. Realtime-Implementation bleibt im Code als solides Fundament,
  falls je ein Multi-Account-Setup auftaucht oder ein zukünftiger
  Investigations-Pass via mitmproxy die offizielle App-Filter-Form
  extrahiert.

- README + `findings/10_REALTIME_API.md` mit klarem Caveat-Abschnitt
  und Open-Hypothesen.
- Status-Tabelle: Realtime-Phase ist `⚗️` (experimentell), nicht `✅`.

### Bewertung
- **Polling (alle Phasen ✅):** funktioniert seit v0.1.0 produktiv,
  zuverlässig, getestet.
- **Realtime (Phase ⚗️):** Protokoll vollständig dokumentiert, Code
  produktions-reif gebaut, aber im einzigen verfügbaren Test-Setup
  ohne Wirkung. Kein Block für den primären Use-Case.

## [1.0.3] - 2026-05-08

Patch — testet die dritte Hypothese zum `calendars`-Filter-Feld
(`null` statt `[]` oder konkreter Liste) + macht das gesendete
Filter-JSON sichtbar im Log.

### Geändert
- **Default-Filter ist jetzt `calendars: null`** (Dart-Default-Verhalten:
  "kein Whitelist, subscribe nach den Boolean-Toggles"). Wenn das die
  korrekte Form ist, sollten auf einmal Notifications fließen.
- Neuer Config-Knopf `realtime.calendar_filter_mode`:
  - `"auto"` (default) — sendet `null`
  - `"internal-only"` — wie v1.0.2, fetcht User-Kalender und whitelistet sie
  - `"explicit"` — nutzt `realtime.calendar_uuids` als Liste verbatim
- `realtime.set_filter_sent` loggt jetzt das **vollständige Filter-JSON**
  (mit UUIDs auf 8 Zeichen gekürzt für Log-Übersicht). Damit kann ein
  Operator/Debugger den exakten Wire-Inhalt sehen ohne mit tcpdump zu
  hantieren.
- 1 neuer Test (auto-mode skippt get_calendars-Call), 1 neuer Test
  (internal-only-mode triggert es), 1 angepasster Test (calendars
  default ist None nicht []).

## [1.0.2] - 2026-05-08

Patch — fixt Realtime-Filter (Server fired keine Notifications mit
leerem `calendars`-Feld) + macht Connection-Liveness im Log sichtbar.

### Behoben
- **Empty-`calendars`-Filter-Bug**: bei v1.0.0/v1.0.1 sendete der
  Bridge-Filter `calendars: []`. Empirisch (User-Test): selbst mit
  `subscribeUserCalendars=true` und `subscribeGroupCalendars=true` fired
  der Server **keine** ReceiveNotifications, wenn `calendars`-Liste leer
  ist. Bridge ruft jetzt vor dem SetFilter `get_calendars(group.id)` auf
  und passt die UUIDs der internen Calendars (`calendarType=0`) als
  `calendars`-Liste in den Filter ein. Externe Calendars (Google-/Apple-/
  MS-synced, `calendarType != 0`) werden nicht mit-subscribed — das ist
  konsistent mit der Mapper-Filter-Regel.

### Geändert
- **Liveness-Logging im Realtime-Client**:
  - Pings (alle 15s) loggen jetzt jeden 4. Ping als `realtime.ping`
    (≈ minütlich) — Operator sieht `docker compose logs` weiß-und-grün
    dass die Verbindung lebt.
  - SetFilter-Completion ist jetzt `info`-Level (war `debug`), damit der
    Erfolg des Subscribe sofort sichtbar ist.
  - Unbekannte Invocation-Targets loggen jetzt `info` mit Payload-Preview,
    damit Server-seitige Protokoll-Drift nicht silent passiert.

## [1.0.1] - 2026-05-08

Patch — fixt Delete-Propagation bei Realtime-Triggern.

### Behoben
- **Realtime-Trigger machte `incremental_sync` statt `full_sync`** —
  `incremental_sync` hat `detect_missing_as_deleted=False`, das heißt
  physische Deletes (Daely entfernt das Event ganz aus der API, statt es
  mit `deleted=true` zu flaggen) wurden silently übersprungen. Effekt
  beim User: Termin auf dem Tablet gelöscht → blieb in Google.
  Jetzt: Realtime-Trigger ruft `full_sync` mit Deletion-Detection.
  Polling-Loop (alle 15min) bleibt auf `incremental_sync` (cheap), weil
  ein nicht-realtime Daemon-Restart sowieso periodisch `full_sync` macht.
- 1 neuer Regression-Test der explizit prüft dass der Realtime-Trigger
  `full_sync_fn`, nicht `incremental_sync_fn` verwendet.

## [1.0.0] - 2026-05-08

**Stable release.** Die Bridge ist feature-complete für ihren primären
Use-Case (Daely-Events sichtbar in Google-Calendar-Widgets/Home-Assistant)
und hat seit Tagen produktiv ohne Probleme gelaufen. Zusätzlich liefert
1.0 das letzte große UX-Feature: Sub-Sekunden-Latenz statt 15-Minuten-
Polling über SignalR-Realtime.

### Hinzugefügt
- **§1.1 Realtime-Push (SignalR über SSE)** — opt-in via `realtime.enabled:
  true`. Bridge öffnet eine persistente SSE-Connection zu Daelys
  `/realtime`-Hub und triggert bei jedem Calendar-Push einen
  debounced incremental_sync. Effekt: Calendar-Änderungen propagieren
  in Sekunden statt 15 Minuten.
  - Reverse-Engineered SignalR JSON Hub Protocol (negotiate → handshake →
    SetFilter → ReceiveNotification-Stream)
  - Auto-reconnect mit exponential backoff (1s → 5min cap)
  - Token-Rotation mid-stream (über `acquireNewToken`-Pattern)
  - Liveness-Detection: kein Ping in 45s → Reconnect
  - Polling bleibt aktiv als Safety-Net — wenn Realtime-Connection
    wiederholt droppt, läuft der 15-min-Cycle weiter
  - 20 neue Offline-Tests gegen einen Fake-SSE-Stream
- `RealtimeClient`-Klasse in `realtime_client.py` (eigenständig nutzbar)
- `RealtimeFilter`/`RealtimeEvent`-Modelle mit `extra="ignore"` für
  zukünftige Server-Schema-Änderungen
- Config-Sektion `realtime:` mit Toggle + Debounce + per-Topic-Subscribes
- `findings/10_REALTIME_API.md` — vollständige Dokumentation des Daely-
  Realtime-Protokolls inkl. Live-validierter Frame-Formate

### Geändert
- Bridge-Daemon (`bridge run`) startet jetzt zwei parallel laufende
  Schleifen wenn beide enabled: Polling-Scheduler (15min default) und
  Realtime-Push-Listener. Ein realtime-getriggerter Sync läuft als
  scheduler-managed One-Shot-Job (idempotent mit `replace_existing=True`).

## [0.1.1] - 2026-05-08

Patch-Release. Härtet die SQLite-WAL-Sichtbarkeit zwischen dem Daemon-
Schreibprozess und parallelen Reader-Prozessen (z. B. `bridge doctor` via
`docker compose exec`).

### Behoben
- **WAL-Sichtbarkeits-Race**: nach jedem Sync-Cycle stellt `sync._finalize`
  jetzt einen expliziten `PRAGMA wal_checkpoint(PASSIVE)` aus, sobald die
  `sync_history`-Zeile geschrieben ist. Damit sehen frisch geöffnete
  Reader-Connections (zweiter Prozess, separate `sqlite3.connect`) die Zeile
  sofort, ohne auf SQLites Auto-Checkpoint zu warten.
- **`bridge doctor` macht eigenen Checkpoint**: ruft `Store.checkpoint()`
  direkt nach Init auf, damit der Read alles inkl. ungefachecktes WAL-
  Material sieht — Belt-and-Suspenders zur ersten Maßnahme.
- **`bridge doctor` zeigt nicht mehr `[WARN]` direkt nach Container-Start**:
  ein leeres `sync_history` ist ein normaler Post-Restart-Zustand, kein
  Fehler. Der Output ist jetzt `[OK] last sync: pending — first cycle
  hasn't completed yet`.

### Hinzugefügt
- `Store.checkpoint(mode="PASSIVE"|"FULL"|"RESTART"|"TRUNCATE")` als
  öffentliche API. Returns SQLites `(busy, log_frames, checkpointed)`-Tripel
  oder `None` bei Non-WAL-DBs.

## [0.1.0] - 2026-05-08

Erstes getaggtes Release. Konsolidiert alles vom RE-Abschluss über die
3e/3f-Mapping-Erweiterungen bis zu Operations-Tools (Migrations, Retry-Loop,
Sync-History, CLI-Commands, Health-Endpoint, CI).

### Sync-Kern

- Einseitiger Sync **Daely → Google Calendar** mit Polling (default alle
  15 min, `lookback_days=30`, `lookahead_days=365`).
- ROPC-Auth gegen Daely-Keycloak (`sso.daely-connect.com`, Realm `daely`,
  Client `mobile-app`). Refresh-Token-Rotation transparent.
- Google-OAuth via InstalledAppFlow, `redirect_uri http://localhost:<port>`,
  konfigurierbarer `oauth_local_port`.
- Bootstrap-CLI: Daely-Login → Google-OAuth → automatisches Anlegen pro Profil
  einer Google-Sub-Calendar → Persistenz der Mapping-Tabelle.
- Sync-Engine:
  - Polling-Loop (apscheduler).
  - `full_sync` mit Deletion-Detection per Snapshot-Diff.
  - `incremental_sync` (kürzeres Window, nur `deleted=true`-Events).
  - Per-Event-Error-Isolation: ein Failure stoppt den Cycle nicht.

### Mapper

- `daely_event_to_google()` – pure function, alle 17 Daely-Event-Felder
  auf Google-Body abgebildet.
- Recurring: master-only Strategie (`deduplicate_recurring()`), RRULE wird
  durchgereicht, Google expandiert.
- Filter: Events aus externen Daely-Kalendern (`calendarType != 0`)
  werden übersprungen, um Sync-Loops mit Daelys eigener Google-Integration
  zu vermeiden.
- **Profil-Footer** (Phase 3e): `👥 Beteiligt: …` an die Description, Namen
  case-insensitive sortiert.
- **Profil-Color-Mapping** (Phase 3f): Daely-`colorCode` → eine der 11
  Google-`colorId`s per nearest-RGB-Match. `profile_overrides` in der
  Config zum Pinnen einzelner Profile.
- **Multi-Participant-Title-Prefix** (Phase 3f): bei ≥2 Beteiligten
  farbige Punkt-Emojis vor dem Titel, Reihenfolge identisch zum Footer.
- `customColorCode` und Daely-only-Felder (`recurringId`, `profileId`, …)
  werden in `extendedProperties.private` gespiegelt für Diagnose.

### Persistenz & Migrationen

- **SQLite-Store** (`bridge.db`), idempotente UPSERTs.
- **Schema-Migration-Framework**: forward-only Migration-Liste in `store.py`,
  beim `Store()`-Init automatisch angewendet. Pre-framework-DBs werden via
  `event_mapping`-Tabellen-Detection als v1 erkannt, ohne dass die Initial-
  Migration neu läuft. Vor jedem echten Upgrade Best-Effort-Backup unter
  `bridge.db.bak.v<N>-<timestamp>`.
- **Schema v1**: `event_mapping`, `sync_state`, `tokens`.
- **Schema v2**: drei neue Spalten auf `event_mapping`
  (`retry_after`, `retry_count`, `last_error`) + neue Tabelle `sync_history`
  als Audit-Log abgeschlossener Sync-Cycles.
- `Store.schema_version`, `Store.migrated_from_version`, `Store.last_backup_path`
  Properties zur Diagnose.

### Reliability

- **Retry-Loop** für `failed=true`-Mappings: nach einem Patch-/Delete-Fehler
  wird der nächste Versuch exponentiell zurückgehalten (Default: 60s
  verdoppelnd, gedeckelt auf 1h). In der Cooldown-Zeit wird das Event
  übersprungen statt erneut gegen Google zu hämmern. Erfolgreiche Re-Patches
  setzen den Retry-Zustand automatisch zurück. Skip-Counter im `SyncReport`:
  `skipped_retry_cooldown`.
- **Sync-History-Persistenz**: jeder Sync-Cycle (auch aborted-at-top runs)
  schreibt eine Zeile in die `sync_history`-Tabelle. Auto-Pruning hält die
  letzten 500 Einträge.

### CLI

- `bridge bootstrap` — interaktives Setup.
- `bridge run` / `--once` — Daemon mit initialem Full-Sync, dann periodisches
  `incremental_sync` über apscheduler.
- `bridge status` — Quick-Look auf Tokens, Mappings, letzten Sync.
- **`bridge doctor`** — Health-Diagnose mit `[OK]`/`[WARN]`/`[FAIL]`-Markern:
  Config, DB-Schema, Tokens, Mappings, Sync-Alter, Error-Trend, Profile-
  Mapping-Konsistenz. Exit-Code 0/1/2 für Cron-Integration.
- **`bridge doctor --live`** — zusätzlich Daely-Refresh + Google-`list_calendars`-
  Ping (braucht Netzwerk).
- **`bridge resync [--calendar <id>] [--dry-run]`** — setzt
  `last_seen_updated=NULL` auf den passenden Mapping-Rows, damit der nächste
  Sync sie mit aktueller Mapper-Logik (z. B. neuen Farben) re-patcht.
- **`bridge re-color [--dry-run]`** — Convenience-Alias für `bridge resync`
  über alle Calendars.

### Health-Endpoint (opt-in)

- Tiny stdlib-`http.server` als Daemon-Thread parallel zum Sync-Loop.
- `GET /healthz` — 200 wenn letzter Sync innerhalb von `poll_interval × 2 + 60s`,
  sonst 503.
- `GET /readyz` — 200 wenn beide Refresh-Tokens (Daely + Google) im Store.
- `GET /status` — JSON mit Schema-Version, Mapping-Counts, letzten 10
  History-Einträgen.
- Bind default `127.0.0.1:8090`, in Config konfigurierbar (`health_server`-
  Sektion). Default off, opt-in via `enabled: true`.

### Operations

- Multi-Arch-Docker-Image (amd64+arm64) via GHA, published auf `ghcr.io/twobeass/daely-google-bridge`.
- **`tests.yml`** GitHub-Action: `pytest` auf jedem Push und PR gegen `main`.
- **`release.yml`** GitHub-Action: bei Push eines `v*`-Tags automatisches
  GitHub-Release; Body wird aus dem passenden CHANGELOG-Abschnitt extrahiert.
- 260 Offline-Tests (`respx` + `unittest.mock`), keine Live-Calls in CI.

### Findings & Doku

- Vollständige sanitisierte API-Dokumentation in `findings/00–08`.
- `findings/09_FUTURE_ROADMAP.md` als Erweiterungs-Katalog mit
  Wert-/Komplexitäts-/Risiko-Bewertung pro Idee.
- README mit Bedienungs-/Command-Übersicht, Update-Anleitung,
  Health-Endpoint-Setup.

### Out-of-Scope

- **Reverse-Sync (Google → Daely)** — bewusst nicht implementiert; Daelys
  eigene Google-/CalDAV-Integration deckt das ab.
- **Photo-Upload** — ursprüngliches Mission-Ziel, zurückgestellt; Widget-
  Use-Case (das eigentliche Pain-Point) ist gelöst.

[Unreleased]: https://github.com/twobeass/daely-google-bridge/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.6.0
[1.5.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.5.0
[1.4.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.4.0
[1.3.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.3.0
[1.2.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.2.0
[1.1.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.1.0
[1.0.3]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.3
[1.0.2]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.2
[1.0.1]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.1
[1.0.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.0
[0.1.1]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.1
[0.1.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.0
