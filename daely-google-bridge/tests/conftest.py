"""Shared test fixtures.

Loads anonymized JSON from the project's tests/fixtures_anonymized/ directory
which lives outside this package (in the parent daely-re repo while bridge is
co-developed). This allows the same fixture set to be used for both the
bridge and any future RE follow-ups.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Path: …/daely-re/daely-google-bridge/tests/conftest.py
# Fixtures: …/daely-re/tests/fixtures_anonymized/
FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures_anonymized"


@pytest.fixture(scope="session")
def with_events_payload() -> list[dict]:
    p = FIXTURE_DIR / "group0_calendars_with_events_v2_attempt0.json"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def calendars_lite() -> list[dict]:
    return json.loads((FIXTURE_DIR / "group0_calendars.json").read_text())


@pytest.fixture(scope="session")
def users_me() -> dict:
    return json.loads((FIXTURE_DIR / "users_me.json").read_text())


@pytest.fixture(scope="session")
def groups_me() -> list[dict]:
    return json.loads((FIXTURE_DIR / "groups_me.json").read_text())


@pytest.fixture(scope="session")
def external_accounts() -> list[dict]:
    return json.loads((FIXTURE_DIR / "external_accounts.json").read_text())
