# daely-google-bridge (Python package)

> 📌 **Setup, Konfiguration und Betrieb** stehen in der
> [Top-Level-README](../README.md). Dort findest du den Docker-Quickstart,
> die Google-Cloud-Console-Anleitung und die Port-Thematik.
>
> Dieses Dokument richtet sich nur an Leute, die den **Code selbst
> ändern, testen oder erweitern** wollen.

## Dev-Setup

```bash
git clone https://github.com/twobeass/daely-google-bridge.git
cd daely-google-bridge/daely-google-bridge

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pytest -q          # 363 Tests, läuft vollständig offline
```

Die Bridge selbst aus dem Source ohne Docker laufen lassen geht auch — du
brauchst dann nur eine `config.yaml` (siehe `config.example.yaml` als
Vorlage) und kannst direkt `bridge bootstrap` / `bridge run` aufrufen.

## Eigenes Image bauen statt das von ghcr.io zu pullen

```bash
# aus dem Repo-Root:
docker build -t local/daely-google-bridge:dev daely-google-bridge/

# im Deploy-Ordner stattdessen das lokale Image nutzen:
BRIDGE_IMAGE=local/daely-google-bridge:dev docker compose up -d
```

## Datei-Layout

```
src/daely_google_bridge/
├── __main__.py        # python -m daely_google_bridge ...
├── cli.py             # argparse + login-daely + bootstrap/run/status/resync
├── config.py          # BridgeConfig (pydantic) + YAML I/O
├── daely_client.py    # Typed Daely client: calendar, recipes, groceries, auth
├── google_client.py   # Google Calendar v3 Wrapper (OAuth + Events-CRUD)
├── mapper.py          # pure Funktion: Daely-Event → Google-Event-Body
├── models.py          # pydantic-Modelle für Daely-Wire-Format
├── store.py           # SQLite (3 Tabellen, idempotente UPSERTs)
└── sync.py            # Orchestrator: full_sync + incremental_sync
```

## Tests

Alle Tests laufen **komplett offline**:

- `test_daely_client.py` — HTTPS gegen Daely via [respx](https://github.com/lundberg/respx) gemockt
- `test_list_meal_client.py` — Legacy- und aktuelle v2-Checklisten,
  v2-Mahlzeitenplan und vollständige v2-Rezepte
- `test_grocery_client.py` — v2-Einkaufsliste und Kundenkarten inklusive
  exakter JSON-Wrapper
- `test_google_client.py` / `test_google_oauth_flow.py` — `googleapiclient`-Resource via `unittest.mock`
- `test_mapper.py` / `test_sync.py` — laufen gegen anonymisierte Live-Read-
  Fixtures aus `../tests/fixtures_anonymized/`
- `test_store.py` — In-Memory-SQLite
- `test_config.py` — `tmp_path`-Files

Wer das Projekt erweitert: **bitte keine echten Live-Calls in der
Test-Suite**, und bei neuen Fixtures vorher `../scripts/anonymize_fixtures.py`
laufen lassen.

## Mapping-Entscheidungen (kurz)

Detail-Doku in [`../findings/05_EVENT_MODEL.md`](../findings/05_EVENT_MODEL.md)
und [`../findings/06_BRIDGE_ARCHITECTURE.md`](../findings/06_BRIDGE_ARCHITECTURE.md).
Kompakt:

- **Filter**: nur Events aus Daely-Kalendern mit `calendarType=0` (interne)
  werden gespiegelt. Externe (Google/MS/Apple, die schon via Daelys
  Integration laufen) werden übersprungen.
- **Recurrence**: master-only. Die früheste server-expandierte Instanz pro
  Series wird mit der ursprünglichen `RRULE` gespiegelt; Google macht den
  Rest.
- **Profile-Footer**: `additionalParticipants`-UUIDs werden via
  `/api/groups/<gid>/profiles` aufgelöst und als
  *„👥 Beteiligt: …"* an die Description gehängt.
- **Daely-Felder** (id, recurringId, calendar id, profile id,
  additionalParticipants, customColorCode, privateEvent, hasError) landen
  in `extendedProperties.private.daely_*`.
- **Sync-Trigger**: Patch nur wenn `event.updated != mapping.last_seen_updated`.
- **Deletion-Detection**: in `full_sync` per Snapshot-vs-Store-Diff,
  in `incremental_sync` nur über das `deleted`-Flag.
