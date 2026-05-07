# 07 – Live-Read Ergebnisse (Phase 3a)

> ⚠️ **Datenschutz**: Diese Datei beschreibt **Strukturen**, nicht konkrete Familiendaten. Echte Werte aus dem Test-Account liegen ausschließlich in `tests/fixtures_private/` (gitignored). Beispiele in diesem Dokument verwenden anonymisierte Test-Werte.

## TL;DR
ROPC-Login funktioniert direkt mit Email + Passwort gegen Keycloak (`mobile-app`). 13 Live-Calls (3 davon scheitern mit aufschlussreichen 400-Fehlern, 1 mit 404 wegen falschem Pfad), 5 erfolgreiche Hauptfixtures. **Alle 5 offenen Punkte aus 05_EVENT_MODEL.md sind geklärt** und es gibt **eine wichtige Strukturerkenntnis**, die nicht im statischen Modell stand: Recurring Events haben **composite IDs** im Format `<masterUuid>_<startUTC>`, der `recurringId` zeigt auf den Master. Außerdem: Server-side Wire-Werte aller Enums weichen vom Dart-Index ab — überall.

## Ablauf (chronologisch)

| # | Endpoint | HTTP | Bytes | Notiz |
|---|---|---|---|---|
| 1 | `POST sso.daely-connect.com/realms/daely/protocol/openid-connect/token` (ROPC) | 200 | – | AT 1800s, RT mit `offline_access`-Scope |
| 2 | `GET /api/users/me` | 200 | 172 | 6 Felder |
| 3 | `GET /api/groups` | **404** | 0 | Endpoint existiert nicht so – richtige Form ist `/api/groups/me` |
| 4 | `GET /api/external-accounts` | 200 | 275 | 1 Google-Account verbunden |
| 5 | `GET /api/url-calendars` | 200 | 2 | leeres Array |
| 6 | `GET /api/groups/me` (Korrektur zu #3) | 200 | 141 | 1 Group |
| 7 | `GET /api/groups/<gid>/calendars` | 200 | 1407 | 3 Kalender |
| 8 | `GET /api/groups/<gid>/calendars/with-events?from=ISO&to=ISO` | **400** | 99 | „StartDate and EndDate must be valid." → Param-Namen falsch |
| 9 | `GET … with-events?start=…&end=…` | **400** | 99 | dito |
| 10 | `GET … with-events?timeMin=…&timeMax=…` | **400** | 99 | dito |
| 11 | `GET … with-events` (ohne Params) | **400** | 99 | „StartDate and EndDate must be valid." (sind required) |
| 12 | `GET … calendars/check-update?internal=&external=` | **400** | 165 | Routing matched 'check-update' als calendarId → Pfad ist anders |
| 13 | `GET … with-events?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | 200 | **75 954** | jackpot |
| 14-16 | `GET … calendars/<calId>/check-update?...` | 404 | 0 | Pfad ist auch nicht das. Endpoint braucht weitere Recherche, **für die Bridge nicht kritisch** weil with-events bereits alles liefert |

Total: **5 erfolgreiche Hauptfixtures + 7 4xx (alle mit aufschlussreichen Bodies persistiert)**.

## Auflösung der 5 offenen Punkte aus 05_EVENT_MODEL.md

### 1. DateOnly-Wire-Format → ✅ ISO-String
All-day events haben `start.date = "2026-05-26"` (plain ISO date, kein `{year, month, day}`-Object). `start.dateTime` ist dann `null`, `start.timeZone` bleibt gesetzt. Das matched Google Calendar 1:1.

```json
"start": {
  "dateTime": null,
  "timeZone": "Europe/Berlin",
  "date": "2026-05-26"
}
```

### 2. additionalParticipants-Inhalt → ✅ Profile-UUIDs
Liste von Profil-UUIDs (nicht Emails, nicht Display-Namen). Ein Element pro zusätzlich zugeordnetem Profil. Das primäre Profil ergibt sich weiterhin aus dem Calendar des Events (`Calendar.profileId`).

```json
"additionalParticipants": ["00000000-0000-0000-0004-000000000002"]
```

### 3. recurrence-Format → ✅ RFC-5545 RRULE-Strings
Liste von RRULE-Strings, jeder mit `RRULE:`-Präfix. Matched Google's `recurrence`-Field exakt (Google verwendet die gleiche RFC-5545-Syntax).

Beispiele aus den Live-Daten:
```
['RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=FR']
['RRULE:FREQ=WEEKLY;UNTIL=20260625T235900Z;INTERVAL=1;BYDAY=TH']
```

**Beobachtungen**:
- `INTERVAL=1` ist immer explizit gesetzt (auch wenn Default).
- `UNTIL` verwendet kompakten ISO-Format (`20260625T235900Z`), nicht den Doppelpunkt-getrennten.
- `BYDAY` mit 2-Letter-Code (`MO`, `TU`, ..., `SU`).
- Im untersuchten Window keine `EXDATE`-Lines beobachtet, aber das Format-Schema sieht das vor.

### 4. customColorCode-Format → ✅ Hex-String CSS-Stil
Format `#RRGGBB` (Hash-Prefix, 6 Hex-Zeichen):
```
"customColorCode": "#C4CDD9"
```
Im Sample war exakt 1 von 80 Events damit gesetzt — Override-Mechanik greift selten.

### 5. RRULE Edge-Cases → ✅ Standard-konform
Sample enthielt:
- WEEKLY ohne UNTIL (unendliche Wiederholung)
- WEEKLY mit UNTIL
- BYDAY mit einzelnem Wochentag

Nicht im Sample (aber RRULE-Spec-konform vermutet):
- DAILY/MONTHLY/YEARLY
- BYMONTH, BYMONTHDAY, BYSETPOS
- COUNT statt UNTIL
- EXDATE als separater Eintrag in der Liste

## Strukturelle Überraschungen (statisch nicht entdeckt)

### A. Composite Event-IDs für Recurring-Instanzen ⚠️ **Wichtig für Mapper**
Recurring Events liefern nicht eine Master-Entry, sondern **eine Entry pro Vorkommen im Datums-Window**, jede mit einer composite ID:

```
id          = <masterUuid>_<startUTC>     (z. B. "5ba9ac4f...e73d_20260508T130000Z")
recurringId = <masterUuid>                (z. B. "5ba9ac4f...e73d")
```

Das heißt:
- Server expandiert die RRULE bereits server-seitig.
- Jede Instanz hat eine eindeutige composite-ID — perfekter Primary-Key für die Bridge-Mapping-Tabelle.
- Alle Instanzen tragen die gleiche `recurrence`-Liste (redundant, aber praktisch).
- Felder wie `title`, `description`, `additionalParticipants` werden auf jeder Instanz wiederholt (kein Override-Modell sichtbar im Sample).

**Konsequenz für die Bridge**:
- Der Mapper muss bei nicht-rekurrenten Events `id` 1:1 als Mapping-Key benutzen.
- Bei rekurrenten Events sind 2 Strategien sinnvoll:
  1. **Master-only**: Nach `recurringId` deduplizieren, nur die erste Instanz als Google-Event mit `recurrence` schreiben. Google expandiert dann selbst. Spart Storage und Calls. **Empfohlen für MVP.**
  2. **Per-Instanz**: Jede Instanz als Google-Single-Event schreiben (ohne `recurrence`). Erlaubt Overrides 1:1. Aber: bei großen Datums-Windows extrem viele Calls.
- Daely scheint keine Override-Daten in Instanzen zu führen (in 80 untersuchten Events keine inkonsistenten Felder zwischen Instanzen einer Series). Das spricht klar für Master-only.

### B. Neues Feld `privateEvent`
Nicht im blutter-Output, aber in der API-Response. `bool`, in unserem Sample immer `false`.

```json
"privateEvent": false
```
Vermutung: schaltet die Sichtbarkeit der Event-Details (Titel/Beschreibung) für andere Family-Profile aus. Auf Google würde das auf `visibility: "private"` (statt `"default"`) mappen. Wenn `true` → in Google `visibility: "private"`, sonst Default.

Bridge-Implikation: pydantic-Model muss das Feld kennen, sonst bricht Validation. Mapper: kann auf Google's `visibility` mappen.

### C. Calendar-Modell hat MEHR Felder als blutter zeigte
In `/calendars` (lite) waren folgende Felder vorhanden:
- `id`, `externalId`, `title`, `description`, `url`, `timeZone`, `colorCode`, `ownerId`, `calendarType`, `shareType`, `profileId`, `isClassSchedule`, `writeable`, `internalSyncToken`, `externalSyncToken`

In `/calendars/with-events` (full) zusätzlich:
- `events: List[CalendarEvent]`
- `hasError: bool`
- `eventsIncluded: bool`
- `presentationType: int`
- `startDate: ISO datetime`
- `endDate: ISO datetime`

**Typo-Warnung**: Field-Name ist `writeable` (mit 'e'), nicht `writable`. blutter hatte `writable` — Wire-Format weicht ab.

Felder, die blutter zeigte aber Live-Response NICHT zeigt:
- `customColorCode` (war wohl Verwechslung mit Event.customColorCode)
- `external` als bool (wird offenbar implizit aus calendarType abgeleitet)

### D. Wire-Werte aller Enums sind ANDERE als die Dart-Index-Werte
Beobachtungen aus den Live-Daten vs. blutter-Disassembly:

| Enum | Dart-Index → Name (blutter) | Live Wire-Wert | Schluss |
|---|---|---|---|
| AccountType | 0=google, 1=apple, 2=microsoft | `accountType: 1` für Google-Account | Wire ≠ Dart-Index |
| CalendarType | 0=google, 1=apple, 2=microsoft, 3=internal (var. A) | `calendarType: 0` für interne Kalender, `calendarType: 1` für Google-synced | Wire-Mapping ist neu zu lernen |
| ShareType | 0=none, 1=oneWay, 2=twoWay, 3=private | `shareType: 2` für aktiv-synced, `null` für interne Familie | `2 = twoWay` plausibel, andere Werte ungetestet |

**Hypothese für die Wire-Werte** (basierend auf den Live-Daten):
- AccountType wire: `0 = unknown/other`, `1 = google`, `2 = apple`, `3 = microsoft` (typisches Server-Enum-Pattern: 0 für "ungesetzt")
- CalendarType wire: `0 = internal`, `1 = google`, `2 = apple`, `3 = microsoft`, `4 = url`

**Aktion für die Bridge**:
- Pydantic-Models mit `Literal` oder Enum-Definitionen, die explizit den Wire-Wert verwenden, NICHT den blutter-Dart-Index.
- Live-Daten sind die Wahrheit. Diese Tabelle wird verfeinert, wenn weitere Werte beobachtet werden.

### E. `presentationType: 1` für alle Kalender im Sample
blutter zeigte: 0=allEvents, 1=timeWindow. `1` matcht "timeWindow" (Default-Anzeige der Daely-App-UI auf dem Tablet ist eine Time-Window-Ansicht). Bridge-Implikation: keine, das ist ein UI-Display-Hint.

### F. SyncTokens sind plausibel server-monoton
Sind nicht-base64 numerische Strings: `"100000000000000001"`, `"100000000000000002"`. Aussehen wie ein 18-stelliger Integer (möglicherweise .NET-Ticks oder eine globale Server-Sequenz). Format aus Bridge-Sicht egal — nur als opaque String round-trippen.

### G. `connectedCalendars: null` in External-Account
Das `ExternalAccount`-Modell hat ein zusätzliches Feld `connectedCalendars` (im blutter-Output stand das nicht so explizit), das im Sample `null` ist. Vermutung: Wenn der User in der Daely-App spezifische Kalender des Google-Accounts ausgewählt hat, würde hier eine Liste auftauchen. Hier nicht relevant für die Bridge.

## Updated Confidence-Bewertung für 05_EVENT_MODEL.md

| Feld | 05-Confidence | Live-Status | Update |
|---|---|---|---|
| Top-Level 16 Felder | high | bestätigt + 1 zusätzliches (`privateEvent`) | **17 Felder total** |
| `id` als String | high | confirmed; **composite für Recurring**: `<uuid>_<utc>` | wichtig dokumentiert |
| `recurringId` | high | confirmed: zeigt auf masterUuid | unverändert |
| `customColorCode` Format | medium | `#RRGGBB` Hex-String | **high** |
| `recurrence` Listenformat | medium-high | List<String> mit `RRULE:`-Präfix | **high** |
| `reminders` Liste<int> | high | confirmed: Minuten-vor-Event | unverändert |
| `additionalParticipants` Inhalt | low | Profile-UUIDs als Strings | **high** |
| `start.date` Wire-Format | medium (Object vs. ISO ungewiss) | ISO-String `"YYYY-MM-DD"` | **high** |
| `start.dateTime` Format | high | ISO 8601 mit Offset (`...+02:00`) oder Z | unverändert |
| `start.timeZone` | high | IANA-Zone, immer gesetzt | unverändert |
| `created` / `updated` | high | ISO 8601 mit Mikrosekunden + UTC-Offset | unverändert |

**Neu in 05.1** (folgt als Patch in 05_EVENT_MODEL.md):
- `privateEvent: bool` als 17. Top-Level-Feld

## Anonymisierung

Anonymisierte Variante in `tests/fixtures_anonymized/`. Mapping:
- 39 UUIDs → kategorie-präfixierte Test-UUIDs (`00000000-0000-0000-000X-...`)
- 1 Email → `user1@example.com`
- 42 Free-Text-Werte → kategorisierte Labels (`Test Event N`, `FirstName N`, etc.)
- Numerische `accountId` → `99999999999999999999N`

**Erhalten** (für die Bridge-Tests gleichermaßen relevant):
- IANA-Zonen (z. B. `Europe/Berlin`)
- Hex-Color-Codes (`#C4CDD9`)
- RRULE-Strings (`RRULE:FREQ=WEEKLY;...`)
- ISO-Timestamps (`2026-05-08T15:00:00+02:00`)
- Sync-Tokens (`100000000000000001`)
- Composite-ID-Format (`<anonUuid>_<UTC>`)
- Boolean-Werte, Enum-Wire-Werte, Strukturen.

Mapping-Datei `fixtures_private/anonymization_map.json` enthält die Rückübersetzung – **gitignored**, nur lokal.

## Verbleibende offene Punkte (out-of-scope für 3a)

1. **`/calendars/check-update` korrekter Pfad** — 4 Pfad-Varianten getestet, alle 4xx. Bridge-MVP verzichtet auf inkrementellen Sync (mit `with-events?startDate=...&endDate=...` reicht ein Full-Window-Read pro Polling-Cycle).
2. **POST-Body-Format** für `with-events`/Calendar-CRUD — irrelevant (Bridge ist read-only).
3. **Token-TTL-Verhalten** unter Refresh — wird in Implementation gemessen (struct-log).
4. **EXDATE-Format** in `recurrence` — wird sich zeigen, wenn der Test-User mal eine Serie-Exception erstellt.

## Bridge-Implementations-Konsequenzen

Mit den Live-Fixtures kann die Phase-3-Implementation jetzt offline starten:

- `models.py`: 17-Feld-`CalendarEvent` mit `privateEvent`. Calendar-Modell erweitert um `presentationType`, `eventsIncluded`, `events`, `startDate`, `endDate`, `hasError`. **Tippfehler**: `writeable` (nicht `writable`).
- `mapper.py`: Master-only-Strategie für Recurring (siehe Punkt A). RRULE 1:1 durchreichen. customColorCode → entweder `colorId` per Hex→Palette-Mapping oder als `extendedProperties.private.daely_custom_color`. additionalParticipants → `extendedProperties.private.daely_additional_participants` als JSON-Array.
- `daely_client.py`: ROPC-Login (verifiziert). Endpoints: `GET /api/users/me`, `GET /api/groups/me`, `GET /api/groups/<gid>/calendars`, `GET /api/groups/<gid>/calendars/with-events?startDate=&endDate=`. Auth via `Authorization: Bearer <at>`, Refresh per `grant_type=refresh_token` gegen Keycloak.
- `tests/test_mapper.py` kann direkt gegen `tests/fixtures_anonymized/group0_calendars_with_events_v2_attempt0.json` laufen.
- Initial-Sync-Strategie wird leicht angepasst: weil `with-events` ein Date-Window braucht, ist „Initial" = ein großer Window-Call (z. B. -90/+365 Tage), und Polling = kleinerer rolling Window.
