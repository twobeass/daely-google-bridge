# 01 – API-Endpoints

## TL;DR
Die ältere App nutzt Resource-Services gegen `https://daely-connect.com`, alle
über `dio` mit `AuthTokenInterceptor` (Bearer). Die offizielle Smartphone-App
v1.5.2 verwendet die Basis `/api/v2/groups` inzwischen auch für Checklisten,
Mahlzeitenplan-Einträge, vollständige Rezepte, Einkauf/Kundenkarten, Chores und
den gebündelten Sync. Endpoint-Pfade, HTTP-Methoden, Bodies und Response-Modelle
der Listen-/Meal-Bereiche sind statisch aus den jeweiligen Flutter-AOT-Builds
rekonstruiert. Das vollständige Mapping steht in `11_LIST_MEAL_API.md`.

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
| `MealsRestService` | `common/service/meal/meals_rest_service.dart` |
| `GroceryRestService` | `common/service/grocery/grocery_rest_service.dart` |
| `RewardRestService` | `common/service/rewards/reward_rest_service.dart` |
| `CoinRestService` | `common/service/coin/coin_rest_service.dart` |
| `SyncRestService` | `common/service/sync/sync_rest_service.dart` |
| `AssetRestService` | `common/service/assets/asset_rest_service.dart` |
| `HolidayRestService` | `common/service/holiday/holiday_rest_service.dart` |
| `DeviceRestService` | `common/service/device/device_rest_service.dart` |
| `GalleryRestService` | `common/service/screen_saver/gallery_rest_service.dart` |
| `ExternalCalendarRestService` | `familyplannerapp/screens/settings/service/external_calendar/external_calendar_rest_service.dart` |
| `DeviceSetupApiImpl` | `familyplannerapp/featured/home/data/device_setup_api_impl.dart` |
| `UpdateCheckRestService` | `familyplannerapp/core/data/service/update/update_check_rest_service.dart` |

### Endpoint-Map

Pfade sind aus `dio.<method>("...")`-Aufrufen extrahiert. Wenn ein Service mehrere Pfade zusammensetzt (z. B. `"$base/$id"`), ist die Basis hier vermerkt. Vollständige Detail-Mappings liegen für Gallery, Calendar sowie List/Meal Plan vor (siehe 03/04/11).

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

#### Legacy List (Bearer) – siehe 11_LIST_MEAL_API.md
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/groups/<groupId>/checklists`, `/checklists/<id>`, `/checklists/reorder`, `/items`, `/items/<id>`, `/items/reorder` | Alte generische Checklisten + Items |

#### Checklists v2 (Smartphone-App v1.5.2, Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/v2/groups/<groupId>/checklists`, `/<checklistId>`, `/items`, `/items/<itemId>`, `/items/reorder`, `/uncheck-all`, `/sync` | Aktuelle Checklisten mit Profilzuordnung, Sichtbarkeit, Fortschritt und Change-Tokens |

#### Grocery v2 (Smartphone-App v1.5.2, Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/v2/groups/<groupId>/grocery/items`, `/overview`, `/lists/default/list-items`, `/list-items/batch`, `/list-items/<id>`, `/list-items/<id>/check` | Separate Einkaufsliste mit Katalog-/Freitextartikeln, Menge, Notiz, Kategorien und Check-Status |
| GET/POST/PUT/DELETE | `/api/v2/groups/<groupId>/grocery/loyalty-cards`, `/loyalty-cards/<id>`, `/loyalty-cards/reorder` | Kundenkarten samt Barcode-Typ, Farbe und Reihenfolge |

#### Chore (Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| - | `/api/groups/<groupId>/...` (älterer Build), `/api/v2/groups/<groupId>/...` (Smartphone v1.5.2) mit `/chores`, `/chores/overview`, `/mark-completion`, `/history/`, `/coins`, `/rewards`, `/packages/` | Aufgaben/Reward-System; die vollständigen aktuellen Body-Schemata sind nicht Teil dieses Abgleichs |

#### Legacy Meal Plan (Bearer) – siehe 11_LIST_MEAL_API.md
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/groups/<groupId>/meal-plan/categories`, `/categories/<id>`, `/entries`, `/entries/<id>`, `/entries/replace`, `/meal`, `/meal/<id>`, `/overview` | Legacy-Rezeptzusammenfassungen und Wochen-Mahlzeit-Planung |

#### Meal Plan v2 (Smartphone-App v1.5.2, Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/v2/groups/<groupId>/meal-plan/entries`, `/entries/replace`, `/entries/<entryId>`, `/entries/<entryId>/<date>` | Aktuelle Wochenabfrage und Mutationen für Mahlzeitenplan-Einträge mit Change-Token-Wrappern |

#### Meals v2 (Smartphone-App v1.5.2, Bearer)
| Methode | Pfad-Bestandteile | Zweck |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/v2/groups/<groupId>/meals`, `/overview`, `/<mealId>`, `/categories`, `/likes`, `/picture` | Vollständige strukturierte Rezepte mit kcal, Dauer, Portionen, Zutaten, Schritten, Kategorien und Likes |

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
**high**: Base-URL, Endpoint-Pfade für die beschriebenen Services,
Cert-Pinning-Negativbefund, Auth-Header-Format sowie v2-Verträge für
Checklisten, Meal-Plan-Einträge, Rezepte, Grocery und Kundenkarten.

**medium**: HTTP-Methoden-Mapping pro Endpoint außerhalb von
Gallery/Calendar/List/Meal Plan/Meals v2/Grocery v2. Die v2-Basis für
Chore/Sync ist belegt, deren vollständiges Methoden-/Body-Mapping aber nicht.

**low**: Body-Schemata der noch nicht im Detail analysierten Services.
Die aktuellen v2-Checklisten-, Meal-Plan- und Grocery-/Kundenkarten-Verträge
sind statisch rekonstruiert und offline getestet, aber noch nicht gegen das
Produktions-Backend geprüft. Rezeptdetail und Rezept-DELETE sind zusätzlich
produktiv bestätigt.

## Offene Punkte
1. Komplettes Methoden-Mapping für Group/Chore-Endpoints.
2. Kontrollierte Produktions-Verifikation der aktuellen v2-Checklist-,
   Meal-Plan- und Grocery-Reads sowie noch nicht bestätigter Writes, jeweils nur
   mit separater Freigabe.
3. Gibt es weitere WebSocket-/SSE-Topics außerhalb des bereits implementierten
   Kalender-Realtime-Pfads?
