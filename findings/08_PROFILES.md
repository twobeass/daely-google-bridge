# 08 – Profile-Endpoint (Phase 3e/A)

> ⚠️ **Datenschutz**: konkrete Familiendaten leben ausschließlich in
> `tests/fixtures_private/` (gitignored). Alle Beispiele hier sind anonymisiert.

## TL;DR
**`GET /api/groups/<gid>/profiles`** liefert die Profil-Liste (Variante a aus
dem Probe-Set). Erster Treffer, kein Fallback nötig. Schema sauber, 7 Felder
inkl. `userId` (nullable), `colorCode`, `imageUrl`, `sortOrder`. Daten
ausreichend für die Description-Footer-Logik der Bridge.

## Probe-Ergebnis

| # | Endpoint | HTTP | Bytes | Notiz |
|---|---|---|---|---|
| (a) | `GET /api/groups/<gid>/profiles` | **200** | 1 581 | jackpot — Probe abgebrochen |
| (b) | `GET /api/profiles` | nicht getestet | – | – |
| (c) | `GET /api/groups/<gid>` | nicht getestet | – | – |
| (d) | `GET /api/groups/<gid>/members` | nicht getestet | – | – |
| (e) | `GET /api/groups/me` Re-Check | nicht getestet | – | – |

Refresh-Token aus `bridge.db` geladen (war von einem früheren Probelauf
ausgelegt), proaktiv erneuert, Bearer-Token an alle Probes.

## Schema

Die Response ist eine flache Liste von Profil-Objekten.

```json
[
  {
    "id":         "00000000-0000-0000-0004-000000000001",
    "name":       "Test Name 2",
    "colorCode":  "#cd812d",
    "groupId":    "00000000-0000-0000-0002-000000000001",
    "userId":     "00000000-0000-0000-0001-000000000001",
    "imageUrl":   "https://daely-connect.com/api/profiles/pictures/<uuid>.jpg",
    "sortOrder":  0
  },
  …
]
```

| Feld | Typ | Bedeutung | Mission-Relevanz |
|---|---|---|---|
| `id` | UUID | Profil-Primärschlüssel. Matcht die UUIDs in `CalendarEvent.additionalParticipants` und `Calendar.profileId`. | **kritisch** für die Auflösung |
| `name` | string | Anzeigename des Profils (z. B. „Anna", „Bob") | **kritisch** für den Footer |
| `colorCode` | string `#RRGGBB` | UI-Farbe pro Profil | optional, evtl. später für `colorId`-Mapping |
| `groupId` | UUID | Group, in der das Profil lebt | redundant mit URL — ignorieren |
| `userId` | UUID \| null | Verknüpfter User-Account, **null** für Familie-Mitglieder ohne eigenen Login (z. B. Kinder) | nicht für Footer relevant |
| `imageUrl` | URL | Avatar-Bild-URL (`daely-connect.com/api/profiles/pictures/<uuid>.jpg`) | optional, nicht für MVP |
| `sortOrder` | int | UI-Reihenfolge. Account-Owner=0 zuerst | optional |

## Beobachtungen / Edge-Cases

1. **Nicht-account-Profile** (z. B. Kinder einer Familie): `userId == null`.
   Kommt im Sample 4× vor — Family-Member ohne eigenen Daely-Account. Der
   primäre Account (Account-Owner) ist das einzige Profil mit gesetztem `userId`.
2. **`name` ist nicht garantiert eindeutig**. Server erlaubt vermutlich
   gleichnamige Profile. Für den Footer ist das akzeptabel — bei zwei
   gleichen Namen tauchen sie eben zweimal nebeneinander auf.
3. **`name` ist nicht garantiert nicht-leer**. Statisch nicht verifizierbar;
   praktisch sehr unwahrscheinlich (UI verlangt einen Namen). Footer-Logik
   wird trotzdem defensiv mit „leer" umgehen — ein Profil mit leerem Namen
   würde wie ein „unbekanntes" behandelt und einfach weggelassen.
4. **Avatars** sind eigene URL-Asset-IDs unter
   `/api/profiles/pictures/<assetUuid>.jpg`. Die UUID hier hat keine
   offensichtliche Beziehung zur `Profile.id`. Falls je relevant: separater
   GET-Call. Für die Footer-Mission nicht nötig.
5. **`colorCode`** ist `#RRGGBB` (CSS-Hex). Identisch zum Format von
   `Calendar.colorCode` und `CalendarEvent.customColorCode`. Würde sich für
   eine spätere Variante anbieten, in der pro Profil ein eigener
   Google-Sub-Calendar mit dieser Farbe angelegt wird (Variante B aus dem
   ursprünglichen Architektur-Brainstorm).
6. **Pagination**: Response ist plain Array, keine Wrapper-Felder. Bei
   großen Familien wäre interessant, ob ein Cap kommt — bei 5 Profilen
   liefert der Server keinen Hinweis.

## Bridge-Konsequenzen

- **Pydantic-Modell** (`Profile`): id, name, colorCode (optional), groupId
  (optional), userId (nullable), imageUrl (optional), sortOrder (optional).
  Mit `extra="ignore"` schwer-tolerant gegen Server-Schemata-Veränderungen.
- **DaelyClient.get_profiles(group_id: str) → list[Profile]** — Signatur mit
  Group-ID, weil die URL ein `<gid>`-Segment hat.
- **Sync-Layer**: einmal pro Cycle `get_profiles(group.id)` aufrufen, in
  `profiles_map: dict[str, str]` (id → name) umwandeln, an Mapper
  durchreichen. Bei Fehler: leere dict + warning, Sync läuft weiter ohne
  Footer.
- **Mapper** baut den Footer „👥 Beteiligt: …" (siehe Phase-3e-Spec).

## Confidence

**high** auf: Endpoint-Pfad, Schema-Felder, `name`/`id` als zuverlässige
Footer-Datenquelle, fehlende Pagination.

**low** auf: Verhalten bei sehr großen Profil-Listen (Pagination triggert?)
und bei gelöschten Profilen (Tombstones in der Liste? oder einfach raus?).
Beides irrelevant für MVP.

## Datenschutz-Notiz für die Bridge

Profile-Namen werden via Footer **in jedes synchronisierte Google-Event**
geschrieben. Das ist explizit Teil der Mission (UX-Verbesserung), aber
relevant zu wissen:

- Die Daten erhalten nur Personen, die Lese-Rechte auf den jeweiligen
  Google-Sub-Calendar haben (per Google's eigene Sharing-Konfiguration).
- Bei `privateEvent: true` wird der Footer trotzdem geschrieben — das
  Visibility-Setting auf Google-Seite versteckt aber Titel und Beschreibung
  vor anderen Teilnehmern.
- Falls jemand seine Kalender breit teilt, würde er auch die Familien-
  Profilnamen mitteilen. Im README dokumentieren.
