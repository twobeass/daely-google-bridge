#!/usr/bin/env python3
"""
Anonymize fixtures_private/*.json into fixtures_anonymized/*.json.

Strategy:
- Walk all JSON values recursively.
- UUIDs in known field roles (id, ownerId, profileId, recurringId, accountId, etc.) get replaced
  with a category-prefixed deterministic test UUID.
- The composite recurring-instance ID format `<masterUUID>_<UTC>` is preserved by mapping
  the master prefix and keeping the timestamp suffix.
- Free-text fields (firstName, lastName, name, title, description, location) get replaced
  with category-indexed placeholders ("Test Event 1", etc).
- Emails replaced with user{N}@example.com.
- All replacements consistent (same source → same target every time).
- Mapping is saved to fixtures_private/anonymization_map.json (gitignored).
"""
from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_DIR = PROJECT_ROOT / "tests" / "fixtures_private"
ANON_DIR = PROJECT_ROOT / "tests" / "fixtures_anonymized"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
COMPOSITE_ID_RE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(_\d{8}T\d{6}Z)$",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")

# Field-name → replacement category. Determines the target UUID prefix and freetext label.
UUID_FIELD_CATEGORIES = {
    "id": "id",
    "userId": "user",
    "ownerId": "user",
    "profileId": "profile",
    "recurringId": "event_master",
    "groupId": "group",
    "calendarId": "calendar",
    "externalId": "external",
    "accountId": "account",
    "additionalParticipants": "profile",  # list of profile UUIDs
}
TEXT_FIELD_CATEGORIES = {
    "firstName": "first_name",
    "lastName": "last_name",
    "name": "name",
    "title": "title",
    "description": "description",
    "location": "location",
    "accountName": "account_name",
    "email": "email",
    "imageUrl": "image_url",
}

CATEGORY_UUID_PREFIX = {
    "id": "00000000-0000-0000-0000-",
    "user":           "00000000-0000-0000-0001-",
    "group":          "00000000-0000-0000-0002-",
    "calendar":       "00000000-0000-0000-0003-",
    "profile":        "00000000-0000-0000-0004-",
    "event_master":   "00000000-0000-0000-0005-",
    "external":       "00000000-0000-0000-0006-",
    "account":        "00000000-0000-0000-0007-",
}


class Anonymizer:
    def __init__(self) -> None:
        self.uuid_map: dict[str, str] = {}      # original UUID → anon UUID (per category)
        self.text_map: dict[str, str] = {}       # original text → anon text (per category)
        self.email_map: dict[str, str] = {}
        self.account_id_map: dict[str, str] = {}
        self.category_counters: dict[str, int] = defaultdict(int)

    def _next_id(self, category: str) -> str:
        self.category_counters[category] += 1
        n = self.category_counters[category]
        prefix = CATEGORY_UUID_PREFIX.get(category, "00000000-0000-0000-0008-")
        return f"{prefix}{n:012x}"

    def map_uuid(self, original: str, category: str) -> str:
        if original in self.uuid_map:
            return self.uuid_map[original]
        anon = self._next_id(category)
        self.uuid_map[original] = anon
        return anon

    def map_email(self, original: str) -> str:
        if original in self.email_map:
            return self.email_map[original]
        n = len(self.email_map) + 1
        anon = f"user{n}@example.com"
        self.email_map[original] = anon
        return anon

    def map_text(self, original: str, category: str) -> str:
        # Empty strings → keep as-is
        if not original:
            return original
        key = f"{category}::{original}"
        if key in self.text_map:
            return self.text_map[key]
        n = self.category_counters[f"text::{category}"] = self.category_counters.get(f"text::{category}", 0) + 1
        anon_label = {
            "first_name": f"FirstName{n}",
            "last_name": f"LastName{n}",
            "name": f"Test Name {n}",
            "title": f"Test Event {n}",
            "description": f"Test description {n}",
            "location": f"Test Location {n}",
            "account_name": f"user{n}@example.com",
            "image_url": None,
        }.get(category, f"anon_{category}_{n}")
        self.text_map[key] = anon_label
        return anon_label

    def map_account_id(self, original: str) -> str:
        if original in self.account_id_map:
            return self.account_id_map[original]
        n = len(self.account_id_map) + 1
        anon = f"99999999999999999999{n:01d}"
        self.account_id_map[original] = anon
        return anon

    def transform_value(self, key: str | None, value):
        # value-type dispatch; key informs category
        if isinstance(value, dict):
            return {k: self.transform_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.transform_value(key, v) for v in value]
        if isinstance(value, str):
            # 1. composite recurring-instance ID
            m = COMPOSITE_ID_RE.match(value)
            if m:
                master, suffix = m.group(1), m.group(2)
                anon_master = self.map_uuid(master, "event_master")
                return f"{anon_master}{suffix}"
            # 2. plain UUID — category from field name
            if UUID_RE.match(value):
                if key == "id":
                    # Disambiguate: "id" can mean event-id, calendar-id, etc., depending on context
                    # We'll use a generic "id" category, callers can analyze surrounding object via key path
                    return self.map_uuid(value, "id")
                cat = UUID_FIELD_CATEGORIES.get(key, "id")
                return self.map_uuid(value, cat)
            # 3. additionalParticipants list elements (parent key was 'additionalParticipants', so 'key' here is the same)
            if key == "additionalParticipants":  # but list children inherit list's key — handled by list case
                return self.map_uuid(value, "profile")
            # 4. email
            if EMAIL_RE.match(value):
                return self.map_email(value)
            # 5. accountId is sometimes a numeric string, not a UUID
            if key == "accountId" and value.isdigit() and len(value) > 6:
                return self.map_account_id(value)
            # 6. text fields
            if key in TEXT_FIELD_CATEGORIES:
                cat = TEXT_FIELD_CATEGORIES[key]
                return self.map_text(value, cat)
            # else: leave as-is (timezone, color codes, RRULE, etc.)
            return value
        # primitives
        return value


def main() -> None:
    if not PRIVATE_DIR.exists():
        raise SystemExit(f"missing {PRIVATE_DIR}")
    ANON_DIR.mkdir(parents=True, exist_ok=True)

    anon = Anonymizer()

    files = sorted(PRIVATE_DIR.glob("*.json"))
    for path in files:
        if path.name == "anonymization_map.json":
            continue
        if path.name.startswith("_"):  # skip _meta.json
            # but copy meta with anonymized status only, not paths (paths contain UUIDs)
            data = json.loads(path.read_text())
            scrubbed = copy.deepcopy(data)
            # scrub group UUID from paths
            for call in scrubbed.get("calls", []):
                if "path" in call and isinstance(call["path"], str):
                    call["path"] = re.sub(
                        UUID_RE.pattern.strip("^$"),
                        lambda m: anon.map_uuid(m.group(0), "group" if "/groups/" in call["path"] else "id"),
                        call["path"],
                    )
            (ANON_DIR / path.name).write_text(json.dumps(scrubbed, indent=2))
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"  [skip] {path.name} not valid JSON")
            continue
        result = anon.transform_value(None, data)
        out = ANON_DIR / path.name
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"  [ok ] {path.name}")

    # Save the mapping (private only — gitignored)
    map_path = PRIVATE_DIR / "anonymization_map.json"
    map_path.write_text(json.dumps({
        "uuids": anon.uuid_map,
        "emails": anon.email_map,
        "text": anon.text_map,
        "account_ids": anon.account_id_map,
    }, indent=2, ensure_ascii=False))
    print(f"\nmapping saved to {map_path} (gitignored)")
    print(f"anonymized fixtures: {ANON_DIR}")
    print(f"  unique UUIDs anonymized: {len(anon.uuid_map)}")
    print(f"  unique emails anonymized: {len(anon.email_map)}")
    print(f"  text replacements: {len(anon.text_map)}")


if __name__ == "__main__":
    main()
