# 01 – API-Endpoints

## TL;DR
Die App nutzt 9 Resource-Services gegen `https://daely-connect.com`, alle über `dio` mit `AuthTokenInterceptor` (Bearer). Endpoint-Paths sind aus dem blutter-Output zu 100 % statisch extrahiert. HTTP-Methoden sind über `Dio::get/post/put/delete`-Aufrufstellen identifizierbar (Detail-Mapping pro Endpoint folgt nur für mission-relevante Services in 03/04).

## Beweise

### Base-URL
- Aus `flutter_assets/.env` (im APK direkt unter `assets/flutter_assets/.env`):
  ```
  API_URL=https://daely-connect.com
  ```
- Bestätigung im disassemblierten Code:
  - `decompiled/apktool/assets/flutter_assets/.env` (siehe Inhalt unten)
  - `findings/blutter_out/asm/common/service/authentication/authentication_rest_service.dart` ruft `dotenv.get("API_URL", fallback: "https://daely-connect.com")` auf
  - `findings/blutter_out/asm/familyplannerapp/screens/settings/service/external_calendar/external_calendar_rest_service.dart` analog

### `.env`-Datei (komplett, aus `flutter_assets/.env`)
```
IS_DEVICE=false
API_URL=https://daely-connect.com
OPENID_ENDPOINT=https://sso.daely-connect.com/realms/daely/protocol/openid-connect
OPENID_CLIENT=mobile-app
GOOGLE_SERVER_CLIENT_ID=450424079033-dd33oj06neglc163dpcu6np3mb3apu4j.apps.googleusercontent.com
GOOGLE_IOS_CLIENT_ID=450424079033-hlq365p0slhrv4k9qj7ppdomj3vvg8pn.apps.googleusercontent.com
MICROSOFT_CLIENT_ID=5e1ddef9-2465-4156-b9b5-2ab5e88637e1
MICROSOFT_REDIRECT_URL=https://daely-connect.com/oauth2callback
APP_STORE_ID=6743496543
```
**Wichtig**: `IS_DEVICE=false` zeigt: gleiche Codebase läuft auf Tablet (`true`) und Companion-App (`false`) – wir untersuchen die Companion-Variante. Manche Code-Pfade (z. B. `Device auto-login is only supported on device builds`) sind also für unsere Mission irrelevant.

### Service-Klassen mit `dio` als Backend
Alle erben von `AuthenticatedService` aus `package:common/base/authenticated_service.dart`. Der Auth-Interceptor injiziert `Authorization: Bearer <token>` und macht 401-Refresh.

| Service-Klasse | Datei (im blutter-Output) |
|---|---|
| `AuthenticationRestService` | `common/service/authentication/authentication_rest_service.dart` |
| `AccountRestService` | `common/service/account/account_rest_service.dart` |
| `GroupRestService` | `common/service/group/group_rest_service.dart` |
| `CalendarRestService` | `common/service/calendar/calendar_rest_service.dart` |
| `ListRestService` | `common/service/list/list_rest_service.dart` |
| `ChoreRestService` | `common/service/chore/chore_rest_service.dart` |
| `MealPlanRestService` | `common/service/meal_plan/meal_plan_rest_service.dart` |
| `GalleryRestService` | `common/service/screen_saver/gallery_rest_service.dart` |
| `ExternalCalendarRestService` | `familyplannerapp/screens/settings/service/external_calendar/external_calendar_rest_service.dart` |
| `DeviceSetupApiImpl` | `familyplannerapp/featured/home/data/device_setup_api_impl.dart` |
| `UpdateCheckRestService` | `familyplannerapp/core/data/service/update/update_check_rest_service.dart` |

### Endpoint-Map

Pfade sind aus `dio.<method>("...")`-Aufrufen extrahiert. Wenn ein Service mehrere Pfade zusammensetzt (z. B. `"$base/$id"`), ist die Basis hier vermerkt. **Volle Method-/Body-Mapping nur für Gallery & Calendar (siehe 03/04)**.

#### Auth (kein Bearer-Token nötig)
| Methode | Pfad | Zweck |
|---|---|---|
| POST | `${OPENID_ENDPOINT}/token` | Token-Endpoint (Keycloak Standard, siehe 02_AUTH.md) |
| POST (Body URL-encoded) | `${OPENID_ENDPOINT}/logout` | Logout / Token-Revoke |
| POST | `${API_URL}/api/users/register` | Registrierung. Body: `firstName`, `lastName`, `email`, `password`, `locale` |
| POST | `${API_URL}/api/users/forgot-password` | Passwort-Reset |
| POST | `${API_URL}/api/users/send-verification` | E-Mail-Verifikation triggern |

#### Account (Bearer)
| Methode | Pfad | Zweck |
|---|---|---|
| GET? | `${API_URL}/api/users/me` | Eigenes Profil (vermutet, nicht 100 % verifiziert) |
| PUT | `${API_URL}/api/users/me/change-locale` | Locale ändern |
| PUT | `${API_URL}/api/users/me/change-name` | Name ändern |

#### Group (Bearer)
| Methode | Pfad-Bestandteile (joinen) | Zweck |
|---|---|---|
| - | `/api/groups` (Basis), `/me`, `/picture`, `/profiles`, `/profiles/`, `/profiles/reorder`, `/settings`, `/update-name`, `/join` | Familien-CRUD, Profile pro Familie, Profile-Reorder, Group-Picture, Group-Settings, Beitritt per Code |

#### Calendar (Bearer) – siehe 04_CALENDAR_SYNC.md für Details
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET | `/api/groups/<groupId>/calendars` | Liste der Kalender |
| GET | `/api/groups/<groupId>/calendars/with-events?...` | Kalender + Events in einem Aufruf |
| GET | `/api/groups/<groupId>/calendars/check-update?...` | Inkrementeller Sync-Check (mit `SyncTokenPair`) |
| POST/PUT/DELETE | `/api/groups/<groupId>/calendars/...` | Kalender-CRUD |
| GET/POST/PUT/DELETE | `/api/groups/<groupId>/calendars/<calendarId>/events`, `/events/<eventId>` | Event-CRUD |
| POST | `/api/groups/<groupId>/calendars/<calendarId>/share` | Calendar-Sharing |

#### External Calendar (Bearer) – siehe 04_CALENDAR_SYNC.md
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET | `/api/url-calendars` | Liste eigene URL-Kalender (CalDAV/ICS) |
| POST | `/api/url-calendars` | URL-Kalender hinzufügen. Body: `url`, `title`, `timezone`, `writable`, `calendarType`, evtl. `groupId` |
| DELETE | `/api/url-calendars/<id>` | URL-Kalender löschen |
| GET | `/api/external-accounts` | Liste verknüpfte OAuth-Accounts (Google, MS, Apple) |
| POST | `/api/external-accounts/connect` | OAuth-Account verbinden |
| POST | `/api/external-accounts/disconnect/<accountId>` | OAuth-Account trennen |
| - | `/add-calendar-to-application`, `/remove-calendar-from-application`, `/calendars` | Per-Account-Calendar-Toggles (welche Kalender eines verbundenen Accounts sollen synchronisiert werden) |

#### List (Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| - | `/api/groups/<groupId>/checklists`, `/checklists/<id>`, `/checklists/reorder`, `/items`, `/items/<id>`, `/items/reorder` | Einkaufslisten/Checklisten + Items |

#### Chore (Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| - | `/api/groups/<groupId>/chores`, `/chores/overview`, `/chores/<id>`, `/mark-completion`, `/unmark-completion`, `/revert`, `/history/`, `/coins`, `/cost`, `/rewards`, `/rewards/<id>`, `/rewards/overview`, `/rewards/profiles/`, `/packages/` | Aufgaben/Reward-System (Familienplanung mit Münz-Belohnung) |

#### Meal Plan (Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| - | `/api/groups/<groupId>/meal-plan/categories`, `/categories/<id>`, `/entries`, `/entries/<id>`, `/entries/replace`, `/meal`, `/meal/<id>`, `/overview` | Wochen-Mahlzeit-Planung |

#### Gallery (Bearer) – siehe 03_PHOTO_LIMIT.md
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET | `/api/gallery/groups/<groupId>/overview` | Liefert `GalleryOverview` (= Liste von `GalleryImage` + `GalleryConfig` mit `maxImages`) |
| GET | `/api/gallery/groups/<groupId>/config` | `GalleryConfig` (`maxImages`, `transitionSeconds`) |
| PUT | `/api/gallery/groups/<groupId>/config` | Config ändern |
| POST | `/api/gallery/groups/<groupId>/upload` | Foto hochladen (multipart) |
| DELETE | `/api/gallery/groups/<groupId>/images/<imageId>` | Foto löschen |
| GET | `/api/gallery/groups/<groupId>/screen-savers` | Screensaver-relevant (vermutlich Tablet-Pfad) |

#### Device-Setup (Bearer) – Companion bindet Tablet
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| - | `/api/device/create-setup-pin`, `/api/device/create-recovery-pin` | Pairing-PIN-Flow (Companion erzeugt PIN, Tablet gibt sie ein) |

#### Mobile-App-Update-Check (Bearer? Vermutlich)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET | `/api/mobile-app/update-available?currentVersion=...` | Liefert `MobileAppVersionInfo` (Update-Verfügbarkeit + Force-Update-Flag) |

### HTTP-Methoden-Identifikation
In den `*_rest_service.dart`-asm-Dateien verweisen Aufrufe auf:
- `[package:dio/src/dio/dio_for_native.dart] _DioForNative&Object&DioMixin::get`
- `... ::post`, `::put`, `::delete`, `::patch`
Beispiel `gallery_rest_service.dart`:
```
0x7ec56c → DioMixin::get
0x7ecfb0 → DioMixin::put
0x7eff44 → DioMixin::post
0x80c538 → DioMixin::delete
0x80d5c0 → DioMixin::put
```
Das erlaubt 1:1-Zuordnung aus dem Disassembly.

### Cert-Pinning
**Kein** Cert-Pinning in der App-Code identifiziert. Greps nach `BadCertificateCallback`, `onHttpClientCreate`, `CertificatePinner`, `setupCert`, `pinning`, `sha256/...`-Patterns kommen leer zurück (im `common`- und `familyplannerapp`-Tree). Die App benutzt also den Default-Platform-TLS-Validator. Für eine spätere Phase 5 (mitmproxy) bedeutet das: Reicht ein systemweites Trusted CA (also ROOT-Ebene oder gepatchte App mit eigenem `network_security_config.xml` für User-CA-Trust). Kein Frida-SSL-Bypass nötig.

## Interpretation
- Die API ist klar resource-orientiert nach Familien (`/api/groups/<groupId>/...`). Die meisten Sub-Resources hängen unter Groups – das ist das zentrale Multi-Tenant-Konzept.
- Auth ist Standard-OIDC, kein Custom-Header, kein API-Key. Das macht den Python-Client überschaubar.
- `dio` ist ohne Cert-Pinning konfiguriert. Live-Capture ist daher mit MITM-CA-Push (System-Level oder Companion-App-Patch) ohne Frida-SSL-Bypass machbar.
- Der `external-calendar`-Endpoint mit `add-calendar-to-application`/`remove-calendar-from-application` legt nahe, dass das Limit „nur 1 Kalender pro Provider" wahrscheinlich serverseitig oder UI-seitig im Toggle-Code liegt. URL-Calendars (CalDAV/ICS) sind separat und scheinen frei mehrfach möglich (siehe 04).

## Confidence
**high**: Base-URL, Service-Klassen-Liste, Endpoint-Pfade, Cert-Pinning-Negativbefund, Auth-Header-Format.

**medium**: HTTP-Methoden-Mapping pro Endpoint außerhalb von Gallery/Calendar (nur sample-weise verifiziert; vollständige Mapping wäre 1-2h zusätzlich).

**low**: Body-Schemata pro Endpoint (Modelle sind in den `_$XxxToJson`-Klassen rekonstruierbar, aber Default-/Optional-Werte unklar ohne Schema-Dump).

## Offene Punkte
1. Komplettes Methoden-Mapping für Group/List/Chore/Meal-Plan-Endpoints (für den finalen Python-Client).
2. Optionale vs. erforderliche Body-Felder (am besten via einem ersten echten Request mit Test-Account).
3. Werden Pagination-Parameter (`page`, `size`, `cursor`) genutzt? Bisher nicht in Strings aufgetaucht. Evtl. Backend ohne Pagination.
4. Gibt es WebSocket-/SSE-Endpoints? `package:sse_channel` ist im blutter-Output aufgetaucht (`asm/sse_channel/io.dart`) → bestätigen, ob für Realtime-Updates genutzt (siehe `common/service/realtime`).
