# 05 – CalendarEvent-Modell und Mapping nach Google Calendar

## TL;DR
Daelys `CalendarEvent` hat **16 Top-Level-Felder** und ist erkennbar an Google Calendar v3 angelehnt (`start.date`/`start.dateTime`-Pattern, ISO-`recurrence`-Liste, `created`/`updated`-Timestamps). Die Übertragung ist **fast 1:1 möglich**, mit zwei Lücken: `additionalParticipants` und `customColorCode/colorCode` brauchen bewusste Entscheidungen, weil Google-Events einen anderen Color-/Attendee-Mechanismus haben. **Profile-Assignment wird per Sub-Calendar gelöst** (ein Google-Kalender pro Daely-Profil), nicht über Felder im Event selbst.

## CalendarEvent – vollständige Feldliste

Quelle: `findings/blutter_out/asm/common/models/calendar/calendar_event.dart` (toJson + FromJson disassembliert). Class id 4941, size 0x48 (= 64 Byte für 16 compressed-pointer Felder, +0x8 Header = 16 Felder bestätigt).

Reihenfolge wie im JSON-toJson-Output (Wire-Order):

| # | Feld | Wire-Typ | Dart-Typ | Optional | Beschreibung | Source / Beweis |
|---|---|---|---|---|---|---|
| 1 | `id` | string | String | nein | UUID des Events. Server-generiert beim POST | toJson 0x547ad0; pp+0x85f8 |
| 2 | `recurringId` | string? | String? | ja | ID des Series-Master, wenn das Event eine Recurrence-Instance ist | toJson 0x547aec; pp+0xd2c8 |
| 3 | `customColorCode` | string? | String? | ja | Override-Farbcode (vermutlich `#RRGGBB` oder Hex-Int). Wird nur gesetzt, wenn vom User explizit gewählt | toJson 0x547b04; pp+0xde18 |
| 4 | `deleted` | bool | bool | nein | Soft-Delete-Flag (Tombstone-Pattern für inkrementellen Sync) | toJson 0x547b1c |
| 5 | `editable` | bool | bool | nein | Server-managed: zeigt UI, ob User dieses Event editieren darf (z. B. read-only für synchronisierte Externe) | toJson 0x547b34 |
| 6 | `hasError` | bool | bool | nein | Sync-Fehler-Indikator (z. B. wenn der externe Provider den Event nicht annahm) | toJson 0x547b4c |
| 7 | `title` | string | String | nein? | Event-Titel | toJson 0x547b64; pp+0xd6c8 |
| 8 | `description` | string? | String? | ja | Notizen/Beschreibung | toJson 0x547b7c |
| 9 | `location` | string? | String? | ja | Ortsangabe (frei) | toJson 0x547b94; pp+0x19b0 |
| 10 | `start` | object | StartEnd | nein | Startzeitpunkt (siehe StartEnd unten) | toJson 0x547ba8 → `_$StartEndToJson` |
| 11 | `end` | object | StartEnd | nein | Endzeitpunkt | toJson 0x547be8 → `_$StartEndToJson` |
| 12 | `created` | string (ISO) | DateTime | nein | Server-set bei POST | toJson 0x547c2c → `DateTimeConverter::toJson` |
| 13 | `updated` | string (ISO) | DateTime | nein | Server-set bei jedem PUT | toJson 0x547c7c → `DateTimeConverter::toJson` |
| 14 | `recurrence` | array of strings? | List<String>? | ja | RFC-5545-RRULE-Strings (z. B. `["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"]`). **Confidence: medium** – Liste-Typ aus PP-Refs auf `<String>`-TypeArgs ableitbar, RFC-Format hochgradig wahrscheinlich (Google-Pattern-Match) | toJson 0x547ccc; pp+0xcc70 |
| 15 | `reminders` | array of ints | List<int> | ja (kann leer/null sein) | Minuten-vor-Event-Werte (z. B. `[10, 60, 1440]`). Bestätigt durch `TypeArguments: <int>` an Index in der FromJson-Closure | toJson 0x547d0c |
| 16 | `additionalParticipants` | array of strings | List<String> | ja | Vermutlich Profile-IDs der Familie, die zusätzlich zum primären Profil zugeordnet sind. **Confidence: low** für genaue Semantik – kann auch Email-Adressen oder Display-Namen sein | toJson 0x547d48 |

### Was nicht im JSON ist, aber im Event-Kontext steht

- `profileId` → **NICHT auf CalendarEvent**. Das Event hat keinen `profileId`-Feld in sich selbst. Stattdessen hängen Events am Calendar (`/api/groups/<gid>/calendars/<calendarId>/events`), und der **Calendar** hat `profileId` (`Calendar.profileId`, siehe 04_CALENDAR_SYNC.md). Konsequenz: Ein Event gehört einem Profil über seinen Trägerkalender, nicht direkt.
- `additionalParticipants` ist die einzige Stelle, an der pro-Event mehrere Profile sichtbar werden – aber **vermutlich** nicht der primäre Profil-Mechanismus.

## StartEnd – inneres Modell

Quelle: gleiche Datei, class id 4942, size 0x14 (= 12 Byte / 3 Compressed-Pointer Felder).

| Feld | Wire-Typ | Dart-Typ | Optional | Beschreibung | Beweis |
|---|---|---|---|---|---|
| `dateTime` | string (ISO 8601) | DateTime? | exklusiv-or mit `date` | Vollwert-Zeitstempel mit Zeit. Z. B. `"2026-05-07T14:30:00.000Z"` oder mit Offset | toJson 0x548138 → `DateTimeConverter::toJson`; pp+0xd2d0 |
| `timeZone` | string? | String? | ja | IANA-Zone wie `"Europe/Berlin"` | toJson 0x548188; pp+0xdce0 |
| `date` | string (ISO 8601 date) | DateOnly? | exklusiv-or mit `dateTime` | All-Day-Datum, vermutlich `"2026-05-07"` (siehe DateOnly unten) | toJson 0x5481c8 |

Genau wie bei Google Calendar: ein Event ist entweder timed (`dateTime` gesetzt, `date` null) oder all-day (`date` gesetzt, `dateTime` null). `timeZone` gilt nur bei timed-Events.

## DateOnly – Wire-Format der `date`-Felder

Quelle: `findings/blutter_out/asm/common/models/calendar/date_only.dart`. Class `_$DateOnlyImpl` (class id 4934), size 0x20.

- 3 Felder: `year` (int), `month` (int), `day` (int).
- Class `DateOnlyIsoConverter implements JsonConverter<X0, X1>` ist im File deklariert. Das ist das `freezed`/`json_serializable`-Pattern: der **Converter** wandelt zwischen Dart-Modell und Wire-Format.
- **Confidence medium**: Höchstwahrscheinlich serialisiert der Converter nach ISO-String (`"YYYY-MM-DD"`), entsprechend Google. **Aber**: die `toJson`-Methode auf `_$DateOnlyImpl` schreibt direkt `{year, month, day}` als Map. Welche Form wirklich aufs Backend geht, ist statisch nicht 100 % entscheidbar (hängt davon ab, ob StartEnd den Converter explizit oder das Default `toJson` benutzt).
- **Konsequenz für Bridge**: Im Mapper auf beide Formen prepared sein. Erster echter Live-Read klärt's binnen 5 Sekunden.

## Zugehörige Modelle

### CalendarEventRequest (POST/PUT-Body)

Quelle: `findings/blutter_out/asm/common/models/calendar/calendar_event_request.dart`, class id 4940, size 0x34.

11 Felder (= 16 CalendarEvent-Felder minus server-managed plus `updateCalendarEventType`):

```
- title, description, location               (User-Edit)
- start, end                                  (StartEnd)
- recurrence, recurringId                     (RFC RRULE / Series-Anchor)
- reminders, additionalParticipants           (Listen)
- customColorCode                             (optionaler Farb-Override)
- updateCalendarEventType                     (Enum, nur relevant für PUT auf Recurrence-Series)
```

**Nicht im Request**: `id` (URL-Pfad), `created`/`updated` (server-managed), `editable`/`hasError`/`deleted` (server-managed).

### UpdateCalendarEventType (Enum)

Quelle: `findings/blutter_out/asm/common/models/calendar/update_calendar_event_type.dart` + `objs.txt`.

| Index | Name | Google-Equivalent (PATCH-Semantik) |
|---|---|---|
| 0 | `updateAll` | wenn man die Recurrence-Master-ID patched ⇒ alle Instanzen geändert |
| 1 | `updateOne` | Single-Instance-Override (Google: GET on `events/<eventId>_<recurringInstance>`) |
| 2 | `updateFuture` | „This and Following" – in Google klassisch durch Ende der alten RRULE + neue Series ab dem Datum |

Das matched 1:1 die Google Calendar-Semantik, lediglich anders genannt.

### DeleteRecurrenceType (Enum)

Quelle: `objs.txt`. **Wichtig**: enum-Index ist NICHT der Wire-Wert. Es gibt ein extra Feld `off_14` mit dem tatsächlichen Server-Code:

| Dart-Index | Name | Wire-Code |
|---|---|---|
| 0 | `deleteOne` | 1 |
| 1 | `deleteFuture` | 2 |
| 2 | `deleteAll` | 0 |

⚠️ Bei DELETE-Calls **muss der Bridge-Mapper genau diese Wire-Codes senden**, nicht den Enum-Index. (Anderes Mapping als UpdateCalendarEventType, wo Index = Wire = 0/1/2 war.)

### ShareType (Enum) – Calendar-Sharing

| Index | Name |
|---|---|
| 0 | `none` |
| 1 | `oneWay` |
| 2 | `twoWay` |
| 3 | `private` |

Relevanz für Bridge: Read-Side, um zu wissen, ob ein Calendar überhaupt Events ausliefert oder geblockt wird.

### CalendarType (Enum) – tatsächlich zwei Versionen
- Variante A (4 Werte, wahrscheinlich `Calendar.calendarType`): `google`, `apple`, `microsoft`, `internal`
- Variante B (5 Werte, wahrscheinlich UI/Filter): `google`, `apple`, `microsoft`, `url`, `internal`

Confidence medium für die Trennung – im Code-Pfad nicht 100 % nachverfolgt. Für die Bridge in der Praxis nicht kritisch.

### CalendarPresentationType (Enum)
- 0: `allEvents`
- 1: `timeWindow`

Eigentlich Tablet-UI-Display-Hint, für Bridge irrelevant.

### AccountType (Enum) – externe Accounts
- 0: `google`, 1: `apple`, 2: `microsoft`

Für Bridge irrelevant – wir schreiben nicht zu `external-accounts`.

### AppWeekday (Enum)
Monday=0 → Sunday=6. Verwendet in RRULE-Konversion für Recurrences. Bridge braucht nur die englischen Strings (`monday` etc.) im RRULE, nicht die Enum-Indices.

### CheckCalendarsUpdateResponse

Quelle: `findings/blutter_out/asm/common/models/calendar/check_calendars_update_response.dart`. 4 Felder:

| Feld | Typ | Bedeutung |
|---|---|---|
| `requiresUpdate` | bool | Es gibt seit dem letzten Sync Änderungen |
| `upToDate` | bool | Komplement-Flag, ggf. redundant zu requiresUpdate |
| `updateCheckIntervalMinutes` | int | Server schlägt Polling-Intervall vor (Bridge **soll das respektieren**) |
| `errors` | array | Liste von Sync-Fehlern (z. B. „Token bei Google abgelaufen") |

Endpoint: `GET /api/groups/<gid>/calendars/check-update?syncToken=...`

Das ist DAS Modell für inkrementellen Sync in der Bridge.

### SyncTokenPair

Quelle: `findings/blutter_out/asm/common/models/calendar/sync_token_pair.dart`. 2 Felder: `internal` (string), `external` (string).

Auf jedem Calendar (`Calendar.internalSyncToken` und `Calendar.externalSyncToken`). Beim ersten Sync sind sie null. Nach jedem Sync schickt der Server neue Token zurück, die in den nächsten `check-update`-Call gehen.

## Mapping CalendarEvent → Google Calendar Event

Google Calendar Event-Body (Doku: `https://developers.google.com/calendar/api/v3/reference/events`):

| Daely-Feld | Google-Feld | Mapping-Regel | Confidence |
|---|---|---|---|
| `id` | `extendedProperties.private.daely_id` | Daely-UUID. Niemals als Google-`id` nutzen, weil Google-IDs eigene Constraints haben | high |
| `id` | (zusätzlich) `iCalUID` (optional) | Falls man später Re-Bind machen will: Daely-UUID als iCalUID setzen, damit Re-Sync ohne Mapping-DB möglich | medium |
| `recurringId` | `recurringEventId` | wenn gesetzt, Daelys recurringId muss auf das gemappte Google-recurringEventId zeigen. Bridge muss Series-Master ZUERST schreiben, dann Instances | high |
| `title` | `summary` | direkt | high |
| `description` | `description` | direkt | high |
| `location` | `location` | direkt | high |
| `start.dateTime` + `start.timeZone` | `start.dateTime` + `start.timeZone` | direkt, IANA-Zone | high |
| `start.date` | `start.date` | direkt (beide ISO `YYYY-MM-DD` – nach DateOnly-Verifikation) | medium |
| analog `end` | `end` | wie `start` | high |
| `recurrence` | `recurrence` | direkt – beide nutzen RRULE-Liste (RFC 5545) | medium-high (RFC-Format zu verifizieren) |
| `reminders` (List<int> Minuten) | `reminders.overrides[].minutes` mit `method: "popup"` | Mapper transformiert. `useDefault: false` setzen, weil wir eigene Liste haben | high |
| `additionalParticipants` | `extendedProperties.private.daely_additional_participants` (JSON-encoded) | **NICHT** auf `attendees` mappen – Google-`attendees` impliziert Email-Einladungen. Daely-Profile sind keine Google-Konten. Statt dessen als Diagnose-String mitführen | high |
| `customColorCode` | `colorId` ODER `extendedProperties.private.daely_custom_color` | Google hat eine fixe Farbpalette mit IDs 1–11. Bridge müsste Daelys Hex auf nächste Google-Farbe runden, ODER nur extProp setzen | medium |
| `editable` | – | nur Daely-intern | – |
| `hasError` | `extendedProperties.private.daely_has_error` (bool) | nur Diagnose | low |
| `deleted` | (führt zu DELETE in Google) | im Sync-Loop: wenn Daely sagt `deleted=true`, in Google `events.delete()` aufrufen | high |
| `created` / `updated` | – | nur Daely-intern. Google verwaltet eigene Timestamps | – |

### Profile-Assignment via Sub-Calendars (Mission-Update)

Anstatt `additionalParticipants` oder einen `profileId`-Field auf Google zu mappen, geht das Profile-Routing über **separate Google-Sub-Calendars**:

```
Daely (1 Family-Group, 4 Profile)
   ├─ Profile "Mama"  ┐
   ├─ Profile "Papa"  ├──► je 1 Google Sub-Calendar
   ├─ Profile "Kind"  │       (Bridge-Config-Mapping
   └─ Profile "Allg." ┘        daely_profile_id → google_calendar_id)
```

Bridge-Logik beim Mapping:
1. Event aus Daely lesen → Calendar des Events bestimmen → über `Calendar.profileId` das Profil ermitteln.
2. In der Bridge-Config nachschlagen: welcher Google-Calendar gehört zu diesem Profil?
3. Event in den entsprechenden Google-Calendar schreiben.

**Bootstrap-Anforderung**: Vor erstem Run muss der User einmal `bridge bootstrap` ausführen, das pro gefundenem Daely-Profil einen Google-Subkalender anlegt (oder das Mapping aus einer pre-erstellten Liste aus `config.yaml` liest).

### Volle extendedProperties.private (für Diagnose, nicht zur Rück-Lese-Verwendung)

```json
{
  "extendedProperties": {
    "private": {
      "daely_id": "<uuid>",
      "daely_calendar_id": "<calendarId>",
      "daely_profile_id": "<profileId>",
      "daely_recurring_id": "<masterId-or-null>",
      "daely_synced_at": "2026-05-07T18:00:00Z",
      "daely_additional_participants": "[\"profile1\",\"profile2\"]",
      "daely_has_error": false,
      "daely_custom_color": "#ff8800-or-null"
    }
  }
}
```

Diese Felder helfen bei manueller Diagnose (Im Google-Kalender-Web sieht man die nicht direkt, aber `events.get` zeigt sie). Bridge schreibt sie, liest sie aber im aktuellen Read-Only-Modus nicht.

## Lücken & offene Punkte (vor Implementation klären)

1. **DateOnly-Wire-Format** – Object-Form vs. ISO-String. Statisch ambivalent (siehe oben). Klärt sich beim ersten Live-Read in 1 Aufruf.
2. **`additionalParticipants` Inhalt** – Profile-IDs vs. Display-Namen vs. Emails. Statisch nur als `List<String>` belegt. Live-Read mit Test-Account klärt.
3. **`recurrence` exaktes Format** – Liste mit RRULE-Strings ist Google-Standard und sehr wahrscheinlich. Aber: könnten auch zusätzliche `EXDATE`-Lines drin sein, oder eigenes Format. Live-Read klärt.
4. **`customColorCode` Format** – Hex-String? CSS-Color-Name? Int? Live-Read klärt.
5. **Was passiert auf Server-Seite, wenn das Mapping eines RRULE-Strings ungültig ist** – beim Bridge-PUSH (irrelevant, wir schreiben nur in Google), aber bei der Daely→Bridge-Read-Verarbeitung muss der Mapper defensive Fehlerbehandlung haben.

## Confidence Summary

- **high**: 16 Top-Level-Felder existieren, Reihenfolge im JSON, StartEnd-Struktur, alle 6 Enum-Wertelisten (UpdateCalendarEventType, DeleteRecurrenceType, ShareType, CalendarType, CalendarPresentationType, AccountType, AppWeekday).
- **medium**: Feldtypen für `recurrence`, `additionalParticipants`, `customColorCode` (Liste/String, aber genaue Inhalts-Semantik nicht statisch entscheidbar).
- **low**: Wire-Format für `start.date` (Object vs. ISO-String), Optionalität einzelner Felder ohne Schema-Doku.

Eine einzige Read-Sequenz `GET /api/groups/<gid>/calendars/with-events?...` mit echtem Test-Account würde alle Medium-/Low-Punkte in <5 Min klären.
