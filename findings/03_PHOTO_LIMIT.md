# 03 – Foto-Limit (15 Bilder)

## TL;DR
Das 15-Bilder-Limit ist **nicht hardcoded** im Client. `GalleryConfig.maxImages` ist ein **server-geliefertes Feld** – der Client lädt es per `GET /api/gallery/groups/<groupId>/config` und nutzt es nur, um den UI-Button zu disablen. **Ob der Server beim 16. Upload zusätzlich validiert, ist statisch nicht entscheidbar – das ist die einzige offene Frage**, die einen einzelnen, kontrollierten Upload-Test erfordert.

## Beweise

### `maxImages` ist ein Server-Feld

**Datei**: `findings/blutter_out/asm/common/models/gallery_image/gallery_config.dart` (231 Zeilen Disassembly)

Klassen-Layout (aus blutter):
```
class _$GalleryConfigImpl extends Object   // class id: 4844, size: 0x18, field offset: 0x8
abstract class GalleryConfig extends _GalleryConfig&Object&_$GalleryConfig
```

Feld-Strings (toString-Generator):
```
"GalleryConfig(transitionSeconds: "
", maxImages: "
")"
```

Das ist ein Standard-`freezed`-Pattern: `transitionSeconds` und `maxImages` sind die einzigen Felder. Sie kommen aus `_$GalleryConfigFromJson()` – also aus einem JSON-Response.

### Wo wird `GalleryConfig` geladen?

**Datei**: `findings/blutter_out/asm/common/service/screen_saver/gallery_rest_service.dart`

```
0x80d494: InitAsync() -> Future<GalleryConfig>          // Method-Return-Typ
0x80d644: r0 = _$GalleryConfigFromJson()                // JSON-Parsing
```

Das ist eine Service-Methode `getConfig()`, die `Future<GalleryConfig>` zurückliefert. Die Endpoint-Strings im selben File:
- `/api/gallery`
- `/groups/`
- `/config`

**Pfad-Konstruktion**: `${API_URL}/api/gallery/groups/<groupId>/config`. Das Limit kommt also vom Server, nicht vom Client.

Außerdem: `gallery_overview.dart` parst `GalleryConfig` als Teil eines `GalleryOverview`-Wrappers (`r0 = _$GalleryConfigFromJson()` in dieser Datei) – d. h. der `/api/gallery/groups/<groupId>/overview`-Call liefert auch sofort die Config mit.

### Wer liest `maxImages` clientseitig?

```
familyplannerapp/screens/settings/pages/screen_saver/screen_saver_screen.dart
familyplannerapp/services/screensaver/screen_saver_cubit.dart
```

Beide nutzen `GalleryConfig` als Parameter in Closures, die UI-Components (z. B. `SettingsButton`) bauen. Konkret: Die Screen-Saver-Settings-Seite zeigt die Foto-Liste an und vermutlich einen "Bild hinzufügen"-Button, der disabled wird, wenn `images.length >= config.maxImages`.

Eine String- oder Konstanten-Suche nach `"15"`, `"MAX_PHOTOS"`, `"PHOTO_LIMIT"` o. ä. **findet nichts** in `common/` oder `familyplannerapp/`. Bestätigt: Kein hardcoded Client-Limit.

### Kann der Client `maxImages` einfach ignorieren?

Ja – Dart-Clients können beliebig oft `POST /upload` aufrufen, da der Limit-Check nur in der UI sitzt. Ob der Server den 16. Upload trotzdem akzeptiert, ist die offene Frage.

### Wie liefert der Server typischerweise `maxImages`?

Hypothesen:
1. **Pro Tarif/Plan-konfiguriert**: Server hat ein „Free=15, Premium=unbegrenzt"-Modell, returned 15 für Free-Accounts. ⇒ Server validiert beim Upload mit. **Wahrscheinlichste Hypothese**.
2. **Globale Konstante**: Backend hat ein hardcoded `MAX_GALLERY_IMAGES=15`. ⇒ Server validiert mit. (Unterscheidet sich nicht von #1 für unsere Mission.)
3. **Reine UI-Hilfsangabe**: Server limitiert nicht, gibt `maxImages` nur als Tipp weiter. ⇒ Client-Bypass würde funktionieren.

Statisch ist die Hypothese nicht testbar. Ein einziger, kontrollierter Upload-Test ist die einzige Antwort.

### Upload-Endpoint

Aus `gallery_rest_service.dart`:
- `POST /api/gallery/groups/<groupId>/upload`
- Aufruf-Adresse: `0x7eff44 → DioMixin::post`
- Request ist multipart (es wird `MediaType()` instanziiert, `MultipartFile`-Klassen-Pattern zu sehen)
- Body-Form vermutlich: `FormData` mit File-Field + ggf. Caption/Position-Feldern

Das exakte Multipart-Schema (Field-Name für File, ob `Content-Type` per Field gesetzt wird etc.) wäre entweder:
- aus tieferem blutter-Asm-Lesen rekonstruierbar (1-2h Arbeit)
- oder via 1 Live-Capture sofort sichtbar (Phase 5)
- oder aus einem ersten Test-Client-Aufruf mit Trial-and-Error (Phase 3)

### `GalleryImage`-Modell

`asm/common/models/gallery_image/gallery_image.dart` definiert das Bild-Modell. Felder ergeben sich aus den toString-Strings (analog `GalleryConfig`-Pattern). Das wird relevant, wenn der Python-Client eine vorhandene Liste pflegt (DELETE/REORDER).

## Interpretation

1. **Konkrete Workaround-Architektur**: Falls der Server `maxImages` nicht enforced (Hypothese #3), reicht es, den Python-Client direkt `POST /upload` ohne UI-Check aufrufen zu lassen – das 16. Bild landet dann legitim im `/api/gallery/groups/<groupId>/overview`-Response. Das Tablet würde es dann auch anzeigen, da es ja über die gleiche API liest.

2. **Falls der Server enforced**: Workaround müsste über mehrere `groupId`s gehen (jeweils 15 Bilder pro Familie/Profil) oder über die externen Calendar-/Photo-Provider-Sync-Mechanik – aber für reine Familien-Bilder gibt's keinen "Photos via External Account"-Pfad.

3. **maxImages auf eigenem Account hochsetzen?**: Das `/api/gallery/.../config`-Endpoint hat eine **PUT**-Methode (siehe DioMixin::put-Aufrufe in `gallery_rest_service.dart` bei 0x7ecfb0 und 0x80d5c0). Wenn der Server akzeptiert, dass der Client `maxImages` selbst setzt, wäre das ein einfacher Trick. Sehr unwahrscheinlich, dass das Backend Self-Service-Quotaerhöhung erlaubt – wäre eklatanter Tarif-Bypass. **Aber** im Test schnell zu verifizieren.

4. **Die UI-Logik macht den Limit-Check sicher** im Client. Das spricht eher dafür, dass der Server NICHT enforced (sonst hätte er einfach beim Upload mit 422 geantwortet, kein UI-Vorab-Check nötig). **Wahrscheinlich Hypothese #3** ⇒ Bypass möglich.

## Confidence
**high**: `maxImages` ist Server-Feld, kein hardcoded Client-Limit.

**medium**: PUT-Endpoint existiert, akzeptiert vermutlich `GalleryConfig`-Body.

**low**: Server-seitige Enforcement vs. nicht. Reine Hypothese, trotz starker UI-Logik-Indizien.

## Offene Punkte / Mission-relevante Tests (Phase 4)

In Reihenfolge zunehmender Backend-Belastung; jeweils einzeln durchgehen, nach Abschluss dokumentieren:

1. **GET-Test**: `GET /api/gallery/groups/<groupId>/overview` mit Test-Account → liest aktuelle Bilderzahl + `maxImages`. Erwartung: `maxImages: 15`. **Lesetest, niedrigste Belastung.** Kann der erste Live-Call überhaupt sein.
2. **PUT-Test (defensiv)**: `PUT /api/gallery/groups/<groupId>/config` mit Body `{maxImages: 20, transitionSeconds: <bestehender Wert>}` und Reaktion prüfen. Wenn 200/204 zurückkommt und ein nachfolgendes GET die 20 zeigt → **Workaround #3, sofort fertig**. Wenn 4xx → nicht möglich, weiter mit #3.
3. **Upload-Test**: Wenn aktuell <15 Bilder im Account → harmlos einfach 1 weiteres Bild hochladen, sehen ob's geht. Wenn aktuell genau 15 Bilder → `POST /upload` als 16. Bild, sehen ob 4xx oder 200. Ergebnis = endgültige Antwort.

**Vor jedem dieser Calls Nutzer fragen.**

Falls Server enforced (Hypothese #1/#2) und PUT-Trick scheitert:
- Mehrere Family-Groups anlegen und sich selbst zu allen einladen → 15 Bilder × N Groups. Hässlich, aber funktioniert. UX im Tablet vermutlich schlecht (jede Group ist ein eigener Screensaver-Slot?). Müsste man durchspielen.
- "Jailbreak" via vermutet existierende **Premium-Tier-API**: Falls es Tarife mit höherem Limit gibt, müsste das Backend ein Upgrade-Endpoint haben. Geht aber gegen die Mission (kein Bypass von Bezahlsachen) – nicht weiter verfolgen.
