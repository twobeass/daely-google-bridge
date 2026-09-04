"""Offline contracts for legacy checklist/meal-plan and v2 meal endpoints."""

import json
from datetime import date
from uuid import UUID

import httpx
import pytest
import respx

from daely_google_bridge.daely_client import DEFAULT_API_BASE, DaelyClient
from daely_google_bridge.models import (
    Checklist,
    ChecklistCreateRequest,
    ChecklistSyncListRequest,
    ChecklistSyncRequest,
    DeleteRecurrenceType,
    Meal,
    MealCategory,
    MealCategoryV2,
    MealDetail,
    MealIngredient,
    MealInstruction,
    MealPlanEntry,
)

GROUP_ID = "group-test"
CHECKLIST_ID = "checklist-test"
ITEM_ID = "item-test"
CATEGORY_ID = "category-test"
MEAL_ID = "meal-test"
ENTRY_ID = "entry-test"

ITEM_JSON = {
    "id": ITEM_ID,
    "title": "Oat milk",
    "completed": False,
    "sortOrder": 0,
}
CHECKLIST_JSON = {
    "id": CHECKLIST_ID,
    "groupId": GROUP_ID,
    "name": "Groceries",
    "items": [ITEM_JSON],
    "sortOrder": 0,
    "itemSortDirection": "asc",
    "itemSortMode": "orderIndex",
}
CHECKLIST_V2_JSON = {
    **CHECKLIST_JSON,
    "changeToken": 4,
    "itemsIncluded": True,
    "hideOnDevice": False,
    "profileIds": ["profile-test"],
    "createdAt": "2026-09-04T10:00:00Z",
    "updatedAt": "2026-09-04T10:05:00Z",
    "progress": {
        "totalItemsCount": 1,
        "completedItemsCount": 0,
    },
}
CHECKLIST_OVERVIEW_V2_JSON = {
    "groupChangeToken": 8,
    "lists": [CHECKLIST_V2_JSON],
    "config": {
        "maxNumberOfChecklists": 20,
        "maxNumberOfItemsPerList": 100,
    },
}
CHECKLIST_MUTATION_V2_JSON = {
    "groupChangeToken": 8,
    "listChangeToken": 4,
    "list": CHECKLIST_V2_JSON,
}
CHECKLIST_ITEM_MUTATION_V2_JSON = {
    "groupChangeToken": 8,
    "listChangeToken": 5,
    "item": ITEM_JSON,
    "groupId": GROUP_ID,
    "checklistId": CHECKLIST_ID,
    "previousCompleted": False,
}
CHECKLIST_ITEMS_MUTATION_V2_JSON = {
    "groupId": GROUP_ID,
    "checklistId": CHECKLIST_ID,
    "groupChangeToken": 8,
    "listChangeToken": 6,
    "items": [ITEM_JSON],
}
CATEGORY_JSON = {
    "id": CATEGORY_ID,
    "name": "Weeknight",
    "createdAt": "2026-09-04T10:00:00Z",
    "updatedAt": "2026-09-04T10:00:00Z",
}
MEAL_JSON = {
    "id": MEAL_ID,
    "categoryIds": [CATEGORY_ID],
    "name": "Vegetable curry",
    "description": "Serve with rice",
    "emoji": "🍛",
    "createdAt": "2026-09-04T10:00:00Z",
    "updatedAt": "2026-09-04T10:00:00Z",
}
ENTRY_JSON = {
    "id": ENTRY_ID,
    "mealId": MEAL_ID,
    "section": "evening",
    "date": "2026-09-05",
    "recurrence": [],
    "createdAt": "2026-09-04T10:00:00Z",
    "updatedAt": "2026-09-04T10:00:00Z",
}
OVERVIEW_JSON = {
    "meals": [MEAL_JSON],
    "categories": [CATEGORY_JSON],
    "entries": [ENTRY_JSON],
    "mealPlanConfig": {
        "maxNumberOfCategories": 20,
        "maxNumberOfMeals": 100,
    },
}

MEAL_DETAIL_JSON = {
    "id": MEAL_ID,
    "name": "Vegetable curry",
    "description": "Serve with rice",
    "emoji": "🍛",
    "calories": 475,
    "time": 35,
    "portions": 4,
    "imageUrl": None,
    "websiteLink": "https://example.invalid/recipe",
    "ingredients": [
        {
            "groceryItemId": "grocery-item-test",
            "ingredientName": None,
            "amount": "2",
            "ignoredForGroceryList": False,
        },
        {
            "groceryItemId": None,
            "ingredientName": "Curry powder",
            "amount": "2 tbsp",
            "ignoredForGroceryList": True,
        },
    ],
    "instructions": [
        {"id": "instruction-a", "position": 0, "text": "Chop vegetables"},
        {"id": "instruction-b", "position": 1, "text": "Simmer"},
    ],
    "categoryIds": [CATEGORY_ID],
    "likedByProfileIds": ["profile-test"],
    "isDefault": False,
    "createdAt": "2026-09-04T10:00:00Z",
    "updatedAt": "2026-09-04T10:05:00Z",
}
MEAL_MUTATION_JSON = {
    "groupId": GROUP_ID,
    "groupMealChangeToken": 12,
    "meal": MEAL_DETAIL_JSON,
}
MEAL_HEADER_JSON = {
    key: value
    for key, value in MEAL_DETAIL_JSON.items()
    if key not in {"portions", "websiteLink", "ingredients", "instructions"}
}
PAGINATED_MEALS_JSON = {
    "groupMealChangeToken": 12,
    "meals": {
        "items": [MEAL_HEADER_JSON],
        "page": 1,
        "pageSize": 20,
        "totalCount": 1,
    },
    "userMealCount": 1,
}
MEALS_OVERVIEW_V2_JSON = {
    "categories": {
        "groupCategoryChangeToken": 7,
        "categories": [{**CATEGORY_JSON, "isDefault": False}],
    },
    "meals": PAGINATED_MEALS_JSON,
    "config": {
        "maxNumberOfCategories": 30,
        "maxNumberOfMeals": 100,
    },
}
CATEGORY_MUTATION_JSON = {
    "groupId": GROUP_ID,
    "groupCategoryChangeToken": 7,
    "category": {**CATEGORY_JSON, "isDefault": False},
}


@pytest.fixture()
def client():
    c = DaelyClient(min_pause_seconds=0.0, max_retries=1)
    c.set_tokens(access_token="AT", refresh_token="RT")
    yield c
    c.close()


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


@respx.mock
def test_checklist_read_and_create(client):
    collection_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/checklists"
    respx.get(collection_url).mock(return_value=httpx.Response(200, json=[CHECKLIST_JSON]))
    create_route = respx.post(collection_url).mock(
        return_value=httpx.Response(200, json=CHECKLIST_JSON)
    )

    checklists = client.get_checklists(GROUP_ID)
    created = client.create_checklist(GROUP_ID, name="Groceries")

    assert checklists[0].items[0].title == "Oat milk"
    assert created.itemSortMode == "orderIndex"
    assert _body(create_route.calls.last.request) == {"name": "Groceries"}


@respx.mock
def test_checklist_update_delete_and_reorder(client):
    item_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/checklists/{CHECKLIST_ID}"
    update_route = respx.put(item_url).mock(return_value=httpx.Response(204))
    delete_route = respx.delete(item_url).mock(return_value=httpx.Response(204))
    reorder_route = respx.put(f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/checklists/reorder").mock(
        return_value=httpx.Response(204)
    )

    client.update_checklist(
        GROUP_ID,
        CHECKLIST_ID,
        name="Market",
        item_sort_mode="alphabetical",
        item_sort_direction="desc",
    )
    client.reorder_checklists(GROUP_ID, ["list-b", "list-a"])
    client.delete_checklist(GROUP_ID, CHECKLIST_ID)

    assert _body(update_route.calls.last.request) == {
        "name": "Market",
        "itemSortMode": "alphabetical",
        "itemSortDirection": "desc",
    }
    assert _body(reorder_route.calls.last.request) == {"orderedIds": ["list-b", "list-a"]}
    assert delete_route.called


@respx.mock
def test_checklist_item_crud_and_reorder(client):
    collection_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/checklists/{CHECKLIST_ID}/items"
    item_url = f"{collection_url}/{ITEM_ID}"
    create_route = respx.post(collection_url).mock(return_value=httpx.Response(200, json=ITEM_JSON))
    update_route = respx.put(item_url)
    update_route.side_effect = [
        httpx.Response(204),
        httpx.Response(200, json={**ITEM_JSON, "completed": True}),
    ]
    reorder_route = respx.put(f"{collection_url}/reorder").mock(return_value=httpx.Response(204))
    delete_route = respx.delete(item_url).mock(return_value=httpx.Response(204))

    created = client.create_checklist_item(GROUP_ID, CHECKLIST_ID, title="Oat milk")
    client.update_checklist_item(GROUP_ID, CHECKLIST_ID, ITEM_ID, title="Two oat milks")
    completed = client.set_checklist_item_completed(GROUP_ID, CHECKLIST_ID, ITEM_ID, completed=True)
    client.reorder_checklist_items(GROUP_ID, CHECKLIST_ID, ["item-b", ITEM_ID])
    client.delete_checklist_item(GROUP_ID, CHECKLIST_ID, ITEM_ID)

    assert created.id == ITEM_ID
    assert completed.completed is True
    assert _body(create_route.calls.last.request) == {
        "title": "Oat milk",
        "completed": False,
    }
    assert [_body(call.request) for call in update_route.calls] == [
        {"title": "Two oat milks"},
        {"completed": True},
    ]
    assert _body(reorder_route.calls.last.request) == {"orderedIds": ["item-b", ITEM_ID]}
    assert delete_route.called


@respx.mock
def test_checklist_v2_overview_detail_and_sync_contracts(client):
    base = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/checklists"
    overview_route = respx.get(base).mock(
        return_value=httpx.Response(200, json=CHECKLIST_OVERVIEW_V2_JSON)
    )
    respx.get(f"{base}/{CHECKLIST_ID}").mock(
        return_value=httpx.Response(200, json=CHECKLIST_MUTATION_V2_JSON)
    )
    sync_route = respx.post(f"{base}/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "groupChangeToken": 8,
                "lists": [
                    {
                        "id": CHECKLIST_ID,
                        "unchanged": False,
                        "data": CHECKLIST_V2_JSON,
                    }
                ],
            },
        )
    )

    overview = client.get_checklists_v2(
        GROUP_ID,
        include_items_for=[CHECKLIST_ID, "checklist-secondary"],
    )
    detail = client.get_checklist_v2(GROUP_ID, CHECKLIST_ID)
    synced = client.sync_checklists_v2(
        GROUP_ID,
        ChecklistSyncRequest(
            lists=[
                ChecklistSyncListRequest(
                    token=4,
                    includeItems=True,
                )
            ],
            includeAllItems=False,
            includeProgress=True,
        ),
    )

    assert overview.lists[0].progress is not None
    assert overview.lists[0].progress.totalItemsCount == 1
    assert overview.config.maxNumberOfItemsPerList == 100
    assert detail.checklist is not None
    assert detail.checklist.profileIds == ["profile-test"]
    assert synced.lists[0].data is not None
    assert synced.lists[0].data.itemsIncluded is True
    params = overview_route.calls.last.request.url.params
    assert params["includeAllItems"] == "false"
    assert params.get_list("includeItemsFor") == [CHECKLIST_ID, "checklist-secondary"]
    assert params["includeProgress"] == "true"
    assert _body(sync_route.calls.last.request) == {
        "lists": [{"token": 4, "includeItems": True}],
        "includeAllItems": False,
        "includeProgress": True,
    }


@respx.mock
def test_checklist_v2_list_mutations_use_wrappers_and_full_update(client):
    base = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/checklists"
    item_url = f"{base}/{CHECKLIST_ID}"
    create_route = respx.post(base).mock(
        return_value=httpx.Response(200, json=CHECKLIST_MUTATION_V2_JSON)
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json=CHECKLIST_MUTATION_V2_JSON)
    )
    delete_route = respx.delete(item_url).mock(
        return_value=httpx.Response(
            200,
            json={**CHECKLIST_MUTATION_V2_JSON, "list": None},
        )
    )
    checklist = Checklist.model_validate(CHECKLIST_V2_JSON)

    created = client.create_checklist_v2(
        GROUP_ID,
        ChecklistCreateRequest(
            name="Groceries",
            hideOnDevice=False,
            profileIds=["profile-test"],
        ),
    )
    updated = client.update_checklist_v2(GROUP_ID, checklist)
    deleted = client.delete_checklist_v2(GROUP_ID, CHECKLIST_ID)

    assert created.checklist is not None
    assert updated.listChangeToken == 4
    assert deleted.checklist is None
    assert _body(create_route.calls.last.request) == {
        "name": "Groceries",
        "hideOnDevice": False,
        "profileIds": ["profile-test"],
    }
    assert _body(update_route.calls.last.request) == CHECKLIST_V2_JSON
    assert delete_route.called


@respx.mock
def test_checklist_v2_item_mutations_and_bulk_routes(client):
    collection = (
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/checklists/"
        f"{CHECKLIST_ID}/items"
    )
    item_url = f"{collection}/{ITEM_ID}"
    create_route = respx.post(collection).mock(
        return_value=httpx.Response(200, json=CHECKLIST_ITEM_MUTATION_V2_JSON)
    )
    update_route = respx.put(item_url)
    update_route.side_effect = [
        httpx.Response(200, json=CHECKLIST_ITEM_MUTATION_V2_JSON),
        httpx.Response(
            200,
            json={
                **CHECKLIST_ITEM_MUTATION_V2_JSON,
                "item": {**ITEM_JSON, "completed": True},
            },
        ),
    ]
    delete_route = respx.delete(item_url).mock(
        return_value=httpx.Response(
            200,
            json={**CHECKLIST_ITEM_MUTATION_V2_JSON, "item": None},
        )
    )
    reorder_route = respx.put(f"{collection}/reorder").mock(
        return_value=httpx.Response(
            200,
            json={
                **CHECKLIST_ITEMS_MUTATION_V2_JSON,
                "checklistChangeToken": 6,
            },
        )
    )
    uncheck_route = respx.put(
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/checklists/"
        f"{CHECKLIST_ID}/uncheck-all"
    ).mock(return_value=httpx.Response(200, json=CHECKLIST_ITEMS_MUTATION_V2_JSON))
    bulk_delete_route = respx.delete(collection).mock(
        return_value=httpx.Response(200, json=CHECKLIST_ITEMS_MUTATION_V2_JSON)
    )

    created = client.create_checklist_item_v2(
        GROUP_ID,
        CHECKLIST_ID,
        title="Oat milk",
    )
    renamed = client.update_checklist_item_v2(
        GROUP_ID,
        CHECKLIST_ID,
        ITEM_ID,
        title="Two oat milks",
    )
    completed = client.set_checklist_item_completed_v2(
        GROUP_ID,
        CHECKLIST_ID,
        ITEM_ID,
        completed=True,
    )
    reordered = client.reorder_checklist_items_v2(
        GROUP_ID,
        CHECKLIST_ID,
        ["item-secondary", ITEM_ID],
    )
    unchecked = client.uncheck_all_checklist_items_v2(GROUP_ID, CHECKLIST_ID)
    bulk_deleted = client.delete_checklist_items_v2(GROUP_ID, CHECKLIST_ID)
    deleted = client.delete_checklist_item_v2(GROUP_ID, CHECKLIST_ID, ITEM_ID)

    assert created.item is not None
    assert renamed.previousCompleted is False
    assert completed.item is not None
    assert completed.item.completed is True
    assert reordered.checklistChangeToken == 6
    assert unchecked.items[0].id == ITEM_ID
    assert bulk_deleted.listChangeToken == 6
    assert deleted.item is None
    assert _body(create_route.calls.last.request) == {
        "title": "Oat milk",
        "completed": False,
    }
    assert [_body(call.request) for call in update_route.calls] == [
        {"title": "Two oat milks"},
        {"completed": True},
    ]
    assert _body(reorder_route.calls.last.request) == {
        "orderedIds": ["item-secondary", ITEM_ID]
    }
    assert uncheck_route.calls.last.request.content == b""
    assert dict(bulk_delete_route.calls.last.request.url.params) == {
        "completedOnly": "true"
    }
    assert delete_route.called


@respx.mock
def test_get_meal_plan_overview(client):
    route = respx.get(f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/meal-plan/overview").mock(
        return_value=httpx.Response(200, json=OVERVIEW_JSON)
    )

    overview = client.get_meal_plan_overview(
        GROUP_ID,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
    )

    assert overview.meals[0].name == "Vegetable curry"
    assert overview.entries[0].section == "evening"
    assert overview.entries[0].date == date(2026, 9, 5)
    assert overview.mealPlanConfig.maxNumberOfMeals == 100
    assert dict(route.calls.last.request.url.params) == {
        "startDate": "2026-09-01",
        "endDate": "2026-09-07",
    }


@respx.mock
def test_meal_category_crud(client):
    collection_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/meal-plan/categories"
    item_url = f"{collection_url}/{CATEGORY_ID}"
    create_route = respx.post(collection_url).mock(
        return_value=httpx.Response(200, json=CATEGORY_JSON)
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json={**CATEGORY_JSON, "name": "Quick"})
    )
    delete_route = respx.delete(item_url).mock(return_value=httpx.Response(204))

    created = client.create_meal_category(GROUP_ID, MealCategory(name="Weeknight"))
    updated = client.update_meal_category(
        GROUP_ID,
        MealCategory.model_validate({**CATEGORY_JSON, "name": "Quick"}),
    )
    client.delete_meal_category(GROUP_ID, CATEGORY_ID)

    assert created.id == CATEGORY_ID
    assert updated.name == "Quick"
    assert _body(create_route.calls.last.request) == {
        "id": None,
        "name": "Weeknight",
        "createdAt": None,
        "updatedAt": None,
    }
    assert _body(update_route.calls.last.request)["id"] == CATEGORY_ID
    assert delete_route.called


@respx.mock
def test_meal_crud(client):
    collection_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/meal-plan/meal"
    item_url = f"{collection_url}/{MEAL_ID}"
    create_route = respx.post(collection_url).mock(return_value=httpx.Response(200, json=MEAL_JSON))
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json={**MEAL_JSON, "description": "Updated"})
    )
    delete_route = respx.delete(item_url).mock(return_value=httpx.Response(204))

    created = client.create_meal(
        GROUP_ID,
        Meal(
            categoryIds=[CATEGORY_ID],
            name="Vegetable curry",
            description="Serve with rice",
            emoji="🍛",
        ),
    )
    updated = client.update_meal(
        GROUP_ID,
        Meal.model_validate({**MEAL_JSON, "description": "Updated"}),
    )
    client.delete_meal(GROUP_ID, MEAL_ID)

    assert created.id == MEAL_ID
    assert updated.description == "Updated"
    assert _body(create_route.calls.last.request)["id"] is None
    assert _body(create_route.calls.last.request)["categoryIds"] == [CATEGORY_ID]
    assert _body(update_route.calls.last.request)["id"] == MEAL_ID
    assert delete_route.called


@respx.mock
def test_meal_plan_entry_create_replace_update_delete(client):
    collection_url = f"{DEFAULT_API_BASE}/api/groups/{GROUP_ID}/meal-plan/entries"
    item_url = f"{collection_url}/{ENTRY_ID}"
    create_route = respx.post(collection_url).mock(
        return_value=httpx.Response(200, json=ENTRY_JSON)
    )
    replace_route = respx.post(f"{collection_url}/replace").mock(
        return_value=httpx.Response(200, json=ENTRY_JSON)
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json={**ENTRY_JSON, "section": "noon"})
    )
    delete_route = respx.delete(f"{item_url}/2026-09-05").mock(return_value=httpx.Response(204))
    draft = MealPlanEntry(
        mealId=MEAL_ID,
        section="evening",
        date=date(2026, 9, 5),
    )

    created = client.create_meal_plan_entry(GROUP_ID, draft)
    replaced = client.replace_meal_plan_entry(GROUP_ID, draft)
    updated = client.update_meal_plan_entry(
        GROUP_ID,
        MealPlanEntry.model_validate({**ENTRY_JSON, "section": "noon"}),
    )
    client.delete_meal_plan_entry(
        GROUP_ID,
        ENTRY_ID,
        occurrence_date=date(2026, 9, 5),
        delete_type=DeleteRecurrenceType.DELETE_FUTURE,
    )

    assert created.id == ENTRY_ID
    assert replaced.id == ENTRY_ID
    assert updated.section == "noon"
    expected_draft = {
        "id": None,
        "mealId": MEAL_ID,
        "section": "evening",
        "date": "2026-09-05",
        "recurrence": [],
        "createdAt": None,
        "updatedAt": None,
    }
    assert _body(create_route.calls.last.request) == expected_draft
    assert _body(replace_route.calls.last.request) == expected_draft
    assert _body(update_route.calls.last.request)["id"] == ENTRY_ID
    assert delete_route.calls.last.request.url.params["deleteType"] == "2"


@respx.mock
def test_meal_plan_v2_entries_read_contract(client):
    route = respx.get(
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meal-plan/entries"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupMealPlanChangeToken": 14,
                "groupMealChangeToken": 12,
                "week": "2026-09-01",
                "entries": [ENTRY_JSON],
                "meals": [MEAL_HEADER_JSON],
            },
        )
    )

    entries = client.get_meal_plan_entries_v2(
        GROUP_ID,
        week=date(2026, 9, 1),
        include_meals=True,
    )

    assert entries.week == date(2026, 9, 1)
    assert entries.entries[0].mealId == MEAL_ID
    assert entries.meals[0].calories == 475
    assert dict(route.calls.last.request.url.params) == {
        "week": "2026-09-01",
        "includeMeals": "true",
    }


@respx.mock
def test_meal_plan_v2_entry_mutation_contracts(client):
    collection = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meal-plan/entries"
    item_url = f"{collection}/{ENTRY_ID}"
    result_json = {
        "groupId": GROUP_ID,
        "groupMealPlanChangeToken": 15,
        "entry": ENTRY_JSON,
    }
    create_route = respx.post(collection).mock(
        return_value=httpx.Response(200, json=result_json)
    )
    replace_route = respx.post(f"{collection}/replace").mock(
        return_value=httpx.Response(200, json=result_json)
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json=result_json)
    )
    delete_route = respx.delete(f"{item_url}/2026-09-05").mock(
        return_value=httpx.Response(200, json={**result_json, "entry": None})
    )
    draft = MealPlanEntry(
        mealId=MEAL_ID,
        section="evening",
        date=date(2026, 9, 5),
    )

    created = client.create_meal_plan_entry_v2(GROUP_ID, draft)
    replaced = client.replace_meal_plan_entry_v2(GROUP_ID, draft)
    updated = client.update_meal_plan_entry_v2(
        GROUP_ID,
        ENTRY_ID,
        recurrence=["RRULE:FREQ=WEEKLY"],
    )
    deleted = client.delete_meal_plan_entry_v2(
        GROUP_ID,
        ENTRY_ID,
        occurrence_date=date(2026, 9, 5),
        delete_type=DeleteRecurrenceType.DELETE_ONE,
    )

    assert created.entry is not None
    assert replaced.groupMealPlanChangeToken == 15
    assert updated.entry is not None
    assert deleted.entry is None
    expected_draft = {
        "id": None,
        "mealId": MEAL_ID,
        "section": "evening",
        "date": "2026-09-05",
        "recurrence": [],
        "createdAt": None,
        "updatedAt": None,
    }
    assert _body(create_route.calls.last.request) == expected_draft
    assert _body(replace_route.calls.last.request) == expected_draft
    assert _body(update_route.calls.last.request) == {
        "recurrence": ["RRULE:FREQ=WEEKLY"]
    }
    assert delete_route.calls.last.request.url.params["deleteType"] == "1"


def test_update_requires_resource_id(client):
    with pytest.raises(ValueError, match=r"meal\.id"):
        client.update_meal(GROUP_ID, Meal(name="No id"))
    with pytest.raises(ValueError, match=r"category\.id"):
        client.update_meal_category(GROUP_ID, MealCategory(name="No id"))
    with pytest.raises(ValueError, match=r"entry\.id"):
        client.update_meal_plan_entry(
            GROUP_ID,
            MealPlanEntry(mealId=MEAL_ID, section="morning", date=date(2026, 9, 5)),
        )


def test_delete_recurrence_wire_values():
    assert int(DeleteRecurrenceType.DELETE_ALL) == 0
    assert int(DeleteRecurrenceType.DELETE_ONE) == 1
    assert int(DeleteRecurrenceType.DELETE_FUTURE) == 2


@respx.mock
def test_meal_v2_detail_and_filtered_list_reads(client):
    collection_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals"
    detail_route = respx.get(f"{collection_url}/{MEAL_ID}").mock(
        return_value=httpx.Response(200, json=MEAL_MUTATION_JSON)
    )
    list_route = respx.get(collection_url).mock(
        return_value=httpx.Response(200, json=PAGINATED_MEALS_JSON)
    )

    detail = client.get_meal_v2(GROUP_ID, MEAL_ID)
    meals = client.get_meals_v2(
        GROUP_ID,
        page=1,
        page_size=20,
        category_id=CATEGORY_ID,
        name="curry",
        defaults_for_language="de",
        liked_by_profile_id="profile-test",
    )

    assert detail.meal.calories == 475
    assert detail.meal.time == 35
    assert detail.meal.portions == 4
    assert detail.meal.ingredients[1].ingredientName == "Curry powder"
    assert detail.meal.instructions[1].position == 1
    assert meals.meals.items[0].name == "Vegetable curry"
    assert meals.meals.totalCount == 1
    assert detail_route.called
    assert dict(list_route.calls.last.request.url.params) == {
        "page": "1",
        "pageSize": "20",
        "Filter.CategoryId": CATEGORY_ID,
        "Filter.Name": "curry",
        "Filter.DefaultsForLanguage": "de",
        "Filter.LikedByProfileId": "profile-test",
    }


@respx.mock
def test_meals_v2_overview_read(client):
    route = respx.get(f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals/overview").mock(
        return_value=httpx.Response(200, json=MEALS_OVERVIEW_V2_JSON)
    )

    overview = client.get_meals_overview_v2(
        GROUP_ID,
        page=1,
        page_size=20,
        defaults_for_language="de",
    )

    assert overview.categories.categories[0].name == "Weeknight"
    assert overview.meals.userMealCount == 1
    assert overview.config.maxNumberOfCategories == 30
    assert dict(route.calls.last.request.url.params) == {
        "mealsPage": "1",
        "mealsPageSize": "20",
        "defaultsForLanguage": "de",
    }


@respx.mock
def test_meal_v2_crud_uses_complete_structured_payload(client):
    collection_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals"
    item_url = f"{collection_url}/{MEAL_ID}"
    create_route = respx.post(collection_url).mock(
        return_value=httpx.Response(200, json=MEAL_MUTATION_JSON)
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json=MEAL_MUTATION_JSON)
    )
    delete_route = respx.delete(item_url).mock(
        return_value=httpx.Response(200, json=MEAL_MUTATION_JSON)
    )
    draft = MealDetail(
        name="Vegetable curry",
        description="Serve with rice",
        emoji="🍛",
        calories=475,
        time=35,
        portions=4,
        websiteLink="https://example.invalid/recipe",
        ingredients=[
            MealIngredient(groceryItemId="grocery-item-test", amount="2"),
            MealIngredient(
                ingredientName="Curry powder",
                amount="2 tbsp",
                ignoredForGroceryList=True,
            ),
        ],
        instructions=[
            MealInstruction(id="instruction-a", position=0, text="Chop vegetables"),
            MealInstruction(id="instruction-b", position=1, text="Simmer"),
        ],
        categoryIds=[CATEGORY_ID],
        likedByProfileIds=["profile-test"],
    )

    created = client.create_meal_v2(GROUP_ID, draft)
    updated = client.update_meal_v2(
        GROUP_ID,
        draft.model_copy(update={"id": MEAL_ID}),
    )
    deleted = client.delete_meal_v2(GROUP_ID, MEAL_ID)

    assert created.meal.id == MEAL_ID
    assert updated.groupMealChangeToken == 12
    assert deleted.meal.id == MEAL_ID
    expected_create = {
        **MEAL_DETAIL_JSON,
        "id": None,
        "imageUrl": None,
        "createdAt": None,
        "updatedAt": None,
    }
    assert _body(create_route.calls.last.request) == expected_create
    assert _body(update_route.calls.last.request)["id"] == MEAL_ID
    assert delete_route.called


@respx.mock
def test_meal_v2_categories_and_likes(client):
    category_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals/categories"
    category_item_url = f"{category_url}/{CATEGORY_ID}"
    create_route = respx.post(category_url).mock(
        return_value=httpx.Response(200, json=CATEGORY_MUTATION_JSON)
    )
    update_route = respx.put(category_item_url).mock(
        return_value=httpx.Response(200, json=CATEGORY_MUTATION_JSON)
    )
    delete_route = respx.delete(category_item_url).mock(
        return_value=httpx.Response(200, json=CATEGORY_MUTATION_JSON)
    )
    likes_route = respx.put(
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals/{MEAL_ID}/likes"
    ).mock(return_value=httpx.Response(200, json=MEAL_MUTATION_JSON))

    created = client.create_meal_category_v2(GROUP_ID, MealCategoryV2(name="Weeknight"))
    updated = client.update_meal_category_v2(
        GROUP_ID, MealCategoryV2(id=CATEGORY_ID, name="Weeknight")
    )
    deleted = client.delete_meal_category_v2(GROUP_ID, CATEGORY_ID)
    liked = client.set_meal_likes_v2(GROUP_ID, MEAL_ID, profile_ids=["profile-test"])

    assert created.category.name == "Weeknight"
    assert updated.category.id == CATEGORY_ID
    assert deleted.groupCategoryChangeToken == 7
    assert liked.meal.likedByProfileIds == ["profile-test"]
    assert _body(create_route.calls.last.request) == {
        "id": None,
        "name": "Weeknight",
        "isDefault": False,
        "createdAt": None,
        "updatedAt": None,
    }
    assert _body(update_route.calls.last.request)["id"] == CATEGORY_ID
    assert delete_route.called
    assert _body(likes_route.calls.last.request) == {"profileIds": ["profile-test"]}


@respx.mock
def test_meal_v2_picture_upload_and_delete_use_app_contract(client):
    picture_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/meals/{MEAL_ID}/picture"
    upload_route = respx.put(picture_url).mock(
        return_value=httpx.Response(
            200,
            json={
                **MEAL_MUTATION_JSON,
                "meal": {
                    **MEAL_DETAIL_JSON,
                    "imageUrl": "https://example.invalid/meal.webp",
                },
            },
        )
    )
    delete_route = respx.delete(picture_url).mock(
        return_value=httpx.Response(200, json=MEAL_MUTATION_JSON)
    )
    image_webp = b"RIFF\x00\x00\x00\x00WEBP"

    uploaded = client.upload_meal_picture_v2(
        GROUP_ID,
        MEAL_ID,
        image_webp=image_webp,
    )
    deleted = client.delete_meal_picture_v2(GROUP_ID, MEAL_ID)

    assert uploaded.meal.imageUrl == "https://example.invalid/meal.webp"
    assert deleted.meal.imageUrl is None
    request = upload_route.calls.last.request
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="imageFile"' in request.content
    assert b'filename="meal_image.webp"' in request.content
    assert b"Content-Type: image/webp" in request.content
    assert image_webp in request.content
    assert delete_route.called


def test_meal_instruction_generates_uuid_v4():
    instruction = MealInstruction(position=0, text="Mix")

    parsed = UUID(instruction.id)

    assert parsed.version == 4


def test_update_meal_v2_requires_id(client):
    with pytest.raises(ValueError, match=r"meal\.id"):
        client.update_meal_v2(GROUP_ID, MealDetail(name="No id"))
