# 99 – Offene Fragen und Phase-Übergänge

## Zusammenfassung Phase 0 + Phase 1' (Flutter-Anpassung)

| Phase | Status | Kommentar |
|---|---|---|
| 0 – APK-Inventur | ✅ | Älterer Flutter-Build (Dart 3.8.1 AOT) in `00_OVERVIEW.md`; zusätzlich offizielle Smartphone-App v1.5.2 signer-verifiziert und statisch analysiert. |
| 0a – Format-Check | ✅ | XAPK lokal zerlegt; App nie auf dem realen Tablet installiert. |
| 1' – Flutter-Statik via blutter | ✅ | Alter und neuer Build erfolgreich statisch ausgewertet. |
| 1 – API-Endpoints | ✅ | `01_ENDPOINTS.md`; aktuelle v2-Checklisten, Meal-Plan-Einträge, Rezepte, Grocery und Kundenkarten aus Smartphone v1.5.2 ergänzt. |
| 2 – Foto-Limit | ⚠️ teilweise | `03_PHOTO_LIMIT.md`. Statisch: `maxImages` ist Server-Feld, kein Client-Hardcode. Server-Enforcement-Status nur per Live-Test klärbar. |
| 2 – Calendar-Sync | ⚠️ teilweise | `04_CALENDAR_SYNC.md`. Drei Sync-Sourcen, `SyncTokenPair`-Mechanik, `writable`-Feld auf URL-Calendars. Tatsächliches Hersteller-Limit nicht ersichtlich. |
| 2 – Cert-Pinning | ✅ | Negativ. Default-TLS, kein Pinning. |
| List/Meal-API | ✅ Rezept v2 live, übrige v2-Bereiche offline | Legacy-Reads, v2-Rezeptdetail und v2-Rezept-DELETE erfolgreich; Testrezept bereinigt. Aktuelle v2-Checklisten, Meal-Plan, Grocery und Kundenkarten statisch rekonstruiert und offline getestet. |
| Auth-Flow | ✅ | ROPC-Login mit dem korrigierten Konto erfolgreich; Tokens sicher gespeichert. |
| 3 – Test-Client | ✅ | Typisierter Client implementiert; Produktionszugriffe bleiben einzeln freigabepflichtig. |
| 4 – Hypothesen-Verifikation | ▶ | Rezeptdetail und gezielter Rezept-DELETE bestätigt; weitere Schreibtests und Grocery-Live-Read bleiben separat freigabepflichtig. |
| 5 – Live-Capture | ⏸ | Für die aktuelle Mission nicht nötig, da Smartphone-v1.5.2-Verträge statisch rekonstruiert sind. |

## Was statisch nicht beantwortbar ist

### A. Server-seitige Validierung von `maxImages`
**Frage**: Akzeptiert der Backend-Server einen 16. Foto-Upload mit 200, oder antwortet er 4xx?
**Status**: Unbekannt. Statisch nicht entscheidbar.
**Test**: 1 kontrollierter `POST /api/gallery/groups/<gid>/upload` – nach Verifikation mit GET, dass aktuell `images.length == maxImages` ist. Bedarf User-Freigabe (Phase 4).
**Folge wenn nicht enforced**: Direkter Bypass durch Python-Client. Mission gelöst.
**Folge wenn enforced**: Workaround über mehrere Familien-Groups oder Self-Service-PUT auf `/config` testen.

### B. Server-seitiges Limit für URL-Calendar-Anzahl
**Frage**: Wie viele `UrlCalendar`-Einträge erlaubt der Server pro User/Group?
**Status**: Unbekannt. Statisch keine Begrenzung im Modell ersichtlich.
**Test**: 1 zusätzlichen URL-Calendar via `POST /api/url-calendars` hinzufügen, prüfen, ob 200 oder 4xx.
**Folge wenn unbeschränkt**: Eigener CalDAV-Bridge-Server lokal hosten, dort beliebig viele Kalender konsolidieren, eine URL nach Daely melden.

### C. Tatsächliches Verhalten des `PUT /api/gallery/.../config`-Endpoints
**Frage**: Akzeptiert der Server `{maxImages: 99}` als Self-Service-Request?
**Status**: Code-Pfad existiert (DioMixin::put-Aufrufe), aber wahrscheinlich vom Server abgelehnt (sonst wäre der ganze Limit-Mechanismus sinnlos).
**Test**: 1 `PUT`-Call, sehen ob 200 oder 4xx.

### D. HTTP-Methoden / Body-Schemas für Group/Chore-Endpoints
**Frage**: Vollständiges Methoden-Mapping pro verbleibendem Endpoint?
**Status**: Legacy List/Meal Plan sowie aktuelle v2 Checklists, Meal Plan,
Meals, Grocery und Loyalty Cards sind statisch rekonstruiert, im Python-Client
implementiert und offline getestet (`11_LIST_MEAL_API.md`). Die Smartphone-
v2-Basis für Chore und Sync ist belegt; deren vollständige Methoden und
Body-Schemata sowie Group bleiben nur teilweise analysiert.
**Mission-Relevanz**: Die angefragten Listen-, Rezept- und Einkaufsfunktionen
sind auf Client-Ebene abgedeckt; Produktions-Verifikation bleibt getrennt.

### E. Einzelwerte der Enum-Typen
**Frage**: Was sind die konkreten Werte von `ShareType`, `UpdateCalendarEventType`, `AccountType`, `CalendarPresentationType`?
**Status**: Enum-Marker-Strings sichtbar (z. B. `"ShareType."`), aber Einzelwerte nicht ohne tieferes Disassembly.
**Aufwand**: 30 Min für jeden, oder 5 Min via Live-API-Sample.

### F. Pagination
**Status**: Für v2-Rezepte bestätigt: `page`, `pageSize`,
`mealsPage`, `mealsPageSize` sowie ein Wrapper mit `items`, `page`,
`pageSize` und `totalCount`. Für andere Ressourcen weiterhin separat zu
prüfen.

### G. SSE/WebSocket
**Status**: Kalender-Realtime ist analysiert und in der Bridge standardmäßig
aktiviert. Offen bleiben nur mögliche zusätzliche Topics für Meals/Grocery; sie
sind für explizite REST-Zugriffe nicht erforderlich.

## Beobachtete Sicherheits-/Privacy-Aspekte

(Per CLAUDE.md-Regel hier dokumentieren, nicht ausnutzen.)

- **Keycloak Public Client mit ROPC**: Kein Sicherheits-Bug an sich, aber Attack-Surface für Credential-Stuffing. Server hat hoffentlich Brute-Force-Schutz.
- **Refresh-Token in `flutter_secure_storage`**: Standard. Kein lokales Cleartext gefunden.
- **Cleartext-`.env`-Datei in der APK**: Enthält Google-Client-IDs, MS-Client-ID. Das ist normal (Public Clients, IDs sind nicht geheim). Aber: jeder Reverser sieht sie.
- **Keine Anti-Tampering-Mechanik**: Keine SafetyNet/Play-Integrity-Checks, kein App-Attestation. Eine modifizierte APK könnte unerkannt agieren. **Per Mission verboten** (modifizierte APK aufs Tablet).
- **Kein Cert-Pinning**: MITM-Attacks gegen einen ahnungslosen User in einem WLAN möglich – aber das ist eine Schwachstelle, die der Hersteller adressieren sollte, nicht eine, die wir ausnutzen.

Falls beim Live-Test der Backend-Server mit auffälligem Verhalten reagiert (z. B. unauthentifizierte IDOR-Treffer, Privilege-Escalation), wandert das in `findings/SECURITY_RESPONSIBLE_DISCLOSURE.md` (heute noch nicht angelegt, kein Befund).

## Vorschlag für nächste Iteration

1. Optional separat freigegebene und sanitisierte v2-Overview-Reads für
   Checklisten, Meal Plan oder Grocery ausführen.
2. Weitere Produktionsmutationen jeweils einzeln freigeben und prüfen.

Ein Live-Capture oder eine modifizierte APK ist dafür nicht nötig.
