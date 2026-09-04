"""Offline contract tests for the Daely v2 grocery endpoints."""

import json

import httpx
import pytest
import respx

from daely_google_bridge.daely_client import DEFAULT_API_BASE, DaelyClient
from daely_google_bridge.models import (
    CreateGroceryListItemRequest,
    CreateGroceryListItemsRequest,
    GroceryItem,
    GroceryListItem,
    LoyaltyCard,
)

GROUP_ID = "group-test"
GROCERY_ITEM_ID = "grocery-item-test"
TEMPORARY_ITEM_ID = "temporary-item-test"
LIST_ITEM_ID = "list-item-test"
CATEGORY_ID = "grocery-category-test"
TIMESTAMP = "2026-09-04T10:00:00Z"

GROCERY_ITEM_JSON = {
    "id": GROCERY_ITEM_ID,
    "categoryId": CATEGORY_ID,
    "name": "Oat milk",
    "iconImageKey": "milk",
    "isDefault": False,
    "isTemporary": False,
    "createdAt": TIMESTAMP,
    "updatedAt": TIMESTAMP,
}
TEMPORARY_ITEM_JSON = {
    "id": TEMPORARY_ITEM_ID,
    "categoryId": None,
    "name": "Market special",
    "iconImageKey": None,
    "isDefault": False,
    "isTemporary": True,
    "createdAt": TIMESTAMP,
    "updatedAt": TIMESTAMP,
}
LIST_ITEM_JSON = {
    "id": LIST_ITEM_ID,
    "groceryItemId": GROCERY_ITEM_ID,
    "note": "Unsweetened",
    "amount": "2 cartons",
    "isChecked": False,
    "createdAt": TIMESTAMP,
    "updatedAt": TIMESTAMP,
}
CATEGORY_JSON = {
    "id": CATEGORY_ID,
    "name": "Dairy alternatives",
    "sortOrder": 4,
    "iconImageKey": "milk",
    "createdAt": TIMESTAMP,
    "updatedAt": TIMESTAMP,
}
CONFIG_JSON = {
    "maxNumberOfGroceryLists": 20,
    "maxNumberOfListItems": 200,
    "maxNumberOfCustomItems": 200,
    "maxNumberOfCheckedGroceryListItems": 24,
}
LOYALTY_CARD_ID = "loyalty-card-test"
LOYALTY_CARD_JSON = {
    "id": LOYALTY_CARD_ID,
    "groupId": GROUP_ID,
    "name": "Local market",
    "data": "000000000000",
    "barcodeType": "code128",
    "color": "#123456",
    "sortOrder": 0,
}
LOYALTY_CARD_MUTATION_JSON = {
    "groupId": GROUP_ID,
    "groupLoyaltyCardChangeToken": 3,
    "card": LOYALTY_CARD_JSON,
}


@pytest.fixture()
def client():
    instance = DaelyClient(min_pause_seconds=0.0, max_retries=1)
    instance.set_tokens(access_token="AT", refresh_token="RT")
    yield instance
    instance.close()


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


@respx.mock
def test_grocery_v2_reads_items_list_and_overview(client):
    base = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery"
    items_route = respx.get(f"{base}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "groupGroceryItemChangeToken": 3,
                "items": [GROCERY_ITEM_JSON],
            },
        )
    )
    respx.get(f"{base}/lists/default/list-items").mock(
        return_value=httpx.Response(
            200,
            json={
                "groupGroceryListChangeToken": 5,
                "items": [LIST_ITEM_JSON],
            },
        )
    )
    overview_route = respx.get(f"{base}/overview").mock(
        return_value=httpx.Response(
            200,
            json={
                "groupGroceryListChangeToken": 5,
                "groupGroceryItemChangeToken": 3,
                "groceryItems": [GROCERY_ITEM_JSON],
                "groceryCategories": [CATEGORY_JSON],
                "groceryList": [LIST_ITEM_JSON],
                "config": CONFIG_JSON,
                "loyaltyCards": [],
                "loyaltyCardChangeToken": None,
            },
        )
    )

    items = client.get_grocery_items_v2(GROUP_ID, include_default=False)
    grocery_list = client.get_grocery_list_v2(GROUP_ID)
    overview = client.get_grocery_overview_v2(
        GROUP_ID,
        include_list_items=True,
        include_categories=True,
        include_group_items=True,
        include_default_items=False,
        include_loyalty_cards=False,
    )

    assert items.items[0].name == "Oat milk"
    assert grocery_list.items[0].amount == "2 cartons"
    assert overview.groceryCategories[0].sortOrder == 4
    assert overview.config.maxNumberOfCheckedGroceryListItems == 24
    assert dict(items_route.calls.last.request.url.params) == {"includeDefault": "false"}
    assert dict(overview_route.calls.last.request.url.params) == {
        "includeListItems": "true",
        "includeCategories": "true",
        "includeGroupItems": "true",
        "includeDefaultItems": "false",
        "includeLoyaltyCards": "false",
    }


@respx.mock
def test_grocery_v2_custom_item_update_and_delete(client):
    item_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/items/{GROCERY_ITEM_ID}"
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryItemChangeToken": 4,
                "item": {**GROCERY_ITEM_JSON, "name": "Barista oat milk"},
            },
        )
    )
    delete_route = respx.delete(item_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryItemChangeToken": 5,
                "item": None,
            },
        )
    )
    item = GroceryItem.model_validate({**GROCERY_ITEM_JSON, "name": "Barista oat milk"})

    updated = client.update_grocery_item_v2(GROUP_ID, item)
    deleted = client.delete_grocery_item_v2(GROUP_ID, GROCERY_ITEM_ID)

    assert updated.item is not None
    assert updated.item.name == "Barista oat milk"
    assert deleted.item is None
    assert _body(update_route.calls.last.request) == {
        **GROCERY_ITEM_JSON,
        "name": "Barista oat milk",
    }
    assert delete_route.called


@respx.mock
def test_grocery_v2_adds_catalog_and_free_form_list_items(client):
    collection_url = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/lists/default/list-items"
    route = respx.post(collection_url)
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryListChangeToken": 6,
                "item": LIST_ITEM_JSON,
                "updatedItem": None,
                "newlyCreatedItem": None,
            },
        ),
        httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryListChangeToken": 7,
                "item": {**LIST_ITEM_JSON, "groceryItemId": TEMPORARY_ITEM_ID},
                "updatedItem": None,
                "newlyCreatedItem": TEMPORARY_ITEM_JSON,
            },
        ),
    ]

    catalog = client.add_grocery_list_item_v2(
        GROUP_ID,
        CreateGroceryListItemRequest(
            groceryItemId=GROCERY_ITEM_ID,
            note="Unsweetened",
            amount="2 cartons",
            language="en",
        ),
    )
    free_form = client.add_grocery_list_item_v2(
        GROUP_ID,
        CreateGroceryListItemRequest(
            newItemName="Market special",
            amount="1",
            language="en",
        ),
    )

    assert catalog.item is not None
    assert catalog.item.groceryItemId == GROCERY_ITEM_ID
    assert free_form.newlyCreatedItem is not None
    assert free_form.newlyCreatedItem.isTemporary is True
    assert [_body(call.request) for call in route.calls] == [
        {
            "groceryItemId": GROCERY_ITEM_ID,
            "newItemName": None,
            "note": "Unsweetened",
            "amount": "2 cartons",
            "language": "en",
        },
        {
            "groceryItemId": None,
            "newItemName": "Market special",
            "note": None,
            "amount": "1",
            "language": "en",
        },
    ]


@respx.mock
def test_grocery_v2_batch_add_uses_exact_wrapper(client):
    route = respx.post(
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/lists/default/list-items/batch"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryListChangeToken": 8,
                "items": [LIST_ITEM_JSON],
                "newlyCreatedItems": [TEMPORARY_ITEM_JSON],
                "updatedItems": [],
            },
        )
    )
    request = CreateGroceryListItemsRequest(
        items=[
            CreateGroceryListItemRequest(
                groceryItemId=GROCERY_ITEM_ID,
                amount="2 cartons",
            ),
            CreateGroceryListItemRequest(
                newItemName="Market special",
                note="Saturday stall",
            ),
        ],
        language="en",
    )

    result = client.add_grocery_list_items_v2(GROUP_ID, request)

    assert result.groupGroceryListChangeToken == 8
    assert result.newlyCreatedItems[0].name == "Market special"
    assert _body(route.calls.last.request) == {
        "items": [
            {
                "groceryItemId": GROCERY_ITEM_ID,
                "newItemName": None,
                "note": None,
                "amount": "2 cartons",
                "language": None,
            },
            {
                "groceryItemId": None,
                "newItemName": "Market special",
                "note": "Saturday stall",
                "amount": None,
                "language": None,
            },
        ],
        "language": "en",
    }


@respx.mock
def test_grocery_v2_updates_and_checks_list_item(client):
    item_url = (
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/lists/default/"
        f"list-items/{LIST_ITEM_ID}"
    )
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryListChangeToken": 9,
                "item": None,
                "updatedItem": {**LIST_ITEM_JSON, "amount": "3 cartons"},
                "newlyCreatedItem": None,
            },
        )
    )
    check_route = respx.put(f"{item_url}/check").mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupGroceryListChangeToken": 10,
                "updatedItem": {**LIST_ITEM_JSON, "isChecked": True},
                "deletedItem": None,
                "deletedTemporaryGroceryItem": None,
                "groupGroceryItemChangeToken": None,
            },
        )
    )
    item = GroceryListItem.model_validate({**LIST_ITEM_JSON, "amount": "3 cartons"})

    updated = client.update_grocery_list_item_v2(GROUP_ID, item)
    checked = client.set_grocery_list_item_checked_v2(
        GROUP_ID,
        LIST_ITEM_ID,
        is_checked=True,
    )

    assert updated.updatedItem is not None
    assert updated.updatedItem.amount == "3 cartons"
    assert checked.updatedItem is not None
    assert checked.updatedItem.isChecked is True
    assert _body(update_route.calls.last.request) == {
        **LIST_ITEM_JSON,
        "amount": "3 cartons",
    }
    assert _body(check_route.calls.last.request) == {"isChecked": True}


def test_grocery_v2_update_list_item_requires_id(client):
    item = GroceryListItem.model_validate({**LIST_ITEM_JSON, "id": None})

    with pytest.raises(ValueError, match=r"grocery list item\.id"):
        client.update_grocery_list_item_v2(GROUP_ID, item)


@respx.mock
def test_loyalty_card_v2_read_and_crud_contracts(client):
    collection = f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/loyalty-cards"
    respx.get(collection).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupLoyaltyCardChangeToken": 2,
                "cards": [LOYALTY_CARD_JSON],
            },
        )
    )
    create_route = respx.post(collection).mock(
        return_value=httpx.Response(200, json=LOYALTY_CARD_MUTATION_JSON)
    )
    item_url = f"{collection}/{LOYALTY_CARD_ID}"
    update_route = respx.put(item_url).mock(
        return_value=httpx.Response(200, json=LOYALTY_CARD_MUTATION_JSON)
    )
    delete_route = respx.delete(item_url).mock(
        return_value=httpx.Response(
            200,
            json={**LOYALTY_CARD_MUTATION_JSON, "card": None},
        )
    )
    draft = LoyaltyCard(
        name="Local market",
        data="000000000000",
        barcodeType="code128",
        color="#123456",
    )

    overview = client.get_loyalty_cards_v2(GROUP_ID)
    created = client.create_loyalty_card_v2(GROUP_ID, draft)
    updated = client.update_loyalty_card_v2(
        GROUP_ID,
        LoyaltyCard.model_validate(LOYALTY_CARD_JSON),
    )
    deleted = client.delete_loyalty_card_v2(GROUP_ID, LOYALTY_CARD_ID)

    assert overview.cards[0].barcodeType == "code128"
    assert created.card is not None
    assert updated.groupLoyaltyCardChangeToken == 3
    assert deleted.card is None
    assert _body(create_route.calls.last.request) == {
        "id": None,
        "groupId": None,
        "name": "Local market",
        "data": "000000000000",
        "barcodeType": "code128",
        "color": "#123456",
        "sortOrder": 0,
    }
    assert _body(update_route.calls.last.request) == LOYALTY_CARD_JSON
    assert delete_route.called


@respx.mock
def test_loyalty_card_v2_reorder_contract(client):
    route = respx.put(
        f"{DEFAULT_API_BASE}/api/v2/groups/{GROUP_ID}/grocery/loyalty-cards/reorder"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "groupId": GROUP_ID,
                "groupLoyaltyCardChangeToken": 4,
                "cards": [LOYALTY_CARD_JSON],
            },
        )
    )

    reordered = client.reorder_loyalty_cards_v2(
        GROUP_ID,
        ["loyalty-card-secondary", LOYALTY_CARD_ID],
    )

    assert reordered.cards[0].name == "Local market"
    assert _body(route.calls.last.request) == {
        "orderedIds": ["loyalty-card-secondary", LOYALTY_CARD_ID]
    }


def test_loyalty_card_v2_update_requires_id(client):
    card = LoyaltyCard(
        name="Local market",
        data="000000000000",
        barcodeType="code128",
    )

    with pytest.raises(ValueError, match=r"loyalty card\.id"):
        client.update_loyalty_card_v2(GROUP_ID, card)
