# 11 - Checklist, shopping-item, and meal-plan API

## Result

Static Flutter AOT output from the original tablet build and the newer official
smartphone app v1.5.2 is sufficient to reconstruct the method, path, payload,
and response-model contracts for legacy APIs plus the current v2 checklists,
meal-plan entries, full recipes, grocery list, and loyalty cards. This
reconstruction did not require production Daely requests.

The contracts are implemented in the typed Python client and covered by mocked,
offline HTTP tests. On 2026-09-04, three explicitly approved production reads -
the checklist collection, one bounded legacy meal-plan overview, and the known
temporary recipe through the v2 detail endpoint - returned successfully and
validated against the typed models. One separately approved v2 `DELETE` then
removed exactly that known temporary recipe, and its local state file was
removed. Raw responses, identifiers, and user-entered text were neither printed
nor archived. The current v2 checklist, meal-plan, grocery, and loyalty-card
contracts remain statically recovered and offline-tested only. Every future
production `POST`, `PUT`, or `DELETE` still requires an individual user
sign-off.

## Sources and method

Primary static sources:

- the original tablet/companion Flutter AOT output under the ignored local
  reverse-engineering workspace;
- the official smartphone app v1.5.2, signer-verified against the earlier app
  before static analysis and never installed on a real device;
- `findings/blutter_out/asm/common/service/list/list_rest_service.dart`
- `findings/blutter_out/asm/common/service/meal_plan/meal_plan_rest_service.dart`
- `findings/blutter_out/asm/common/models/list/list_model.dart`
- `findings/blutter_out/asm/common/models/meal_plan/*.dart`
- the corresponding v1.5.2 `common/service/{list,meal_plan,meal,grocery}`
  services and generated models in the ignored local analysis workspace.

Paths and payload keys come from object-pool strings assembled at the relevant
service method. HTTP verbs come from the adjacent `DioMixin::get/post/put/delete`
call sites. Response shapes come from the generated `FromJson`/`ToJson`
implementations. This avoids inferring write contracts from REST naming alone.

## Legacy checklists

All paths are relative to the authenticated API base and start with
`/api/groups/{groupId}`.

| Method | Path | JSON body | Result |
|---|---|---|---|
| `GET` | `/checklists` | - | `List<Checklist>` |
| `POST` | `/checklists` | `{"name": string}` | `Checklist` |
| `PUT` | `/checklists/{checklistId}` | `{"name": string, "itemSortMode": "orderIndex" | "alphabetical", "itemSortDirection": "asc" | "desc"}` | no parsed body |
| `DELETE` | `/checklists/{checklistId}` | - | no parsed body |
| `PUT` | `/checklists/reorder` | `{"orderedIds": string[]}` | no parsed body |
| `POST` | `/checklists/{checklistId}/items` | `{"title": string, "completed": false}` | `ChecklistItem` |
| `PUT` | `/checklists/{checklistId}/items/{itemId}` | `{"title": string}` | no parsed body |
| `PUT` | `/checklists/{checklistId}/items/{itemId}` | `{"completed": boolean}` | `ChecklistItem` |
| `DELETE` | `/checklists/{checklistId}/items/{itemId}` | - | no parsed body |
| `PUT` | `/checklists/{checklistId}/items/reorder` | `{"orderedIds": string[]}` | no parsed body |

The same item endpoint intentionally has two update variants: editing a title
does not parse a response, while toggling completion parses and returns the
updated item.

### Response models

`Checklist` contains:

- `id: string`
- `groupId: string`
- `name: string`
- `items: ChecklistItem[]`
- `sortOrder: integer`
- `itemSortDirection: "asc" | "desc"`
- `itemSortMode: "orderIndex" | "alphabetical"`

`ChecklistItem` contains `id`, `title`, `completed`, and `sortOrder`.

This was the only list API exposed by the older build. The smartphone app v1.5.2
has both the current v2 checklist service and the independent v2 grocery API
below; therefore a checklist must not be treated as the grocery list.

## Smartphone v1.5.2 checklist API

All paths start with `/api/v2/groups/{groupId}/checklists`.

| Method | Path | Input | Result |
|---|---|---|---|
| `GET` | `/` | `includeAllItems=false`, optional repeated `includeItemsFor`, `includeProgress=true` | `ChecklistsOverview` |
| `GET` | `/{checklistId}` | - | `ChecklistMutationResult` |
| `POST` | `/` | `ChecklistCreateRequest` | `ChecklistMutationResult` |
| `PUT` | `/{checklistId}` | full `Checklist` | `ChecklistMutationResult` |
| `DELETE` | `/{checklistId}` | - | `ChecklistMutationResult` |
| `POST` | `/sync` | `ChecklistSyncRequest` | `ChecklistSyncResponse` |
| `POST` | `/{checklistId}/items` | `{"title": string, "completed": false}` | `ChecklistItemMutationResult` |
| `PUT` | `/{checklistId}/items/{itemId}` | `{"title": string}` or `{"completed": boolean}` | `ChecklistItemMutationResult` |
| `DELETE` | `/{checklistId}/items/{itemId}` | - | `ChecklistItemMutationResult` |
| `PUT` | `/{checklistId}/items/reorder` | `{"orderedIds": string[]}` | `ChecklistItemReorderResult` |
| `PUT` | `/{checklistId}/uncheck-all` | - | `ChecklistItemsMutationResult` |
| `DELETE` | `/{checklistId}/items?completedOnly=true` | query flag | `ChecklistItemsMutationResult` |

`ChecklistCreateRequest` contains `name`, `hideOnDevice`, and
`profileIds`. The current `Checklist` extends the legacy fields with
`changeToken`, `itemsIncluded`, `hideOnDevice`, `profileIds`,
`createdAt`, `updatedAt`, and an optional `progress` object containing
`totalItemsCount` and `completedItemsCount`.

`ChecklistsOverview` wraps `groupChangeToken`, `lists`, and limits
`maxNumberOfChecklists` / `maxNumberOfItemsPerList`. Mutations return group
and list change tokens instead of the legacy direct bodies. The item-reorder
wrapper really uses the distinct key `checklistChangeToken`; this apparent
naming inconsistency is present in the generated app model and is preserved.
The current service exposes no whole-list reorder call even though an unused
result model exists in the app.

## Smartphone v1.5.2 grocery API

All paths start with `/api/v2/groups/{groupId}/grocery`.

| Method | Path | Input | Result |
|---|---|---|---|
| `GET` | `/items?includeDefault={boolean}` | query flag | `GroceryItemOverview` |
| `GET` | `/lists/default/list-items` | - | `GroceryListOverview` |
| `GET` | `/overview` | five `include*` query flags | `GroceryOverview` |
| `PUT` | `/items/{itemId}` | full `GroceryItem` | `GroceryItemMutationResult` |
| `DELETE` | `/items/{itemId}` | - | `GroceryItemMutationResult` |
| `POST` | `/lists/default/list-items` | `CreateGroceryListItemRequest` | `GroceryListItemMutationResult` |
| `POST` | `/lists/default/list-items/batch` | `CreateGroceryListItemsRequest` | `GroceryListItemsMutationResult` |
| `PUT` | `/lists/default/list-items/{listItemId}` | full `GroceryListItem` | `GroceryListItemMutationResult` |
| `PUT` | `/lists/default/list-items/{listItemId}/check` | `{"isChecked": boolean}` | `GroceryListItemCheckResult` |
| `GET` | `/loyalty-cards` | - | `LoyaltyCardOverview` |
| `POST` | `/loyalty-cards` | full `LoyaltyCard` | `LoyaltyCardMutationResult` |
| `PUT` | `/loyalty-cards/{cardId}` | full `LoyaltyCard` | `LoyaltyCardMutationResult` |
| `DELETE` | `/loyalty-cards/{cardId}` | - | `LoyaltyCardMutationResult` |
| `PUT` | `/loyalty-cards/reorder` | `{"orderedIds": string[]}` | `LoyaltyCardReorderResult` |

The overview flags are `includeListItems`, `includeCategories`,
`includeGroupItems`, `includeDefaultItems`, and `includeLoyaltyCards`.

### Grocery request and response models

`CreateGroceryListItemRequest` carries:

- `groceryItemId`: existing catalog/group item, nullable;
- `newItemName`: free-form item to create, nullable;
- `note`, `amount`, and `language`: nullable strings.

The app uses either `groceryItemId` or `newItemName`. A batch request wraps an
`items` array plus an optional top-level `language`.

`GroceryListItem` contains nullable `id`, required `groceryItemId`, nullable
`note` and `amount`, `isChecked`, `createdAt`, and `updatedAt`. `GroceryItem`
contains `id`, nullable `categoryId`, `name`, nullable `iconImageKey`,
`isDefault`, `isTemporary`, and timestamps. `GroceryCategory` adds `sortOrder`.

`GroceryConfig` defaults recovered from the app are:

| Field | Default |
|---|---:|
| `maxNumberOfGroceryLists` | 20 |
| `maxNumberOfListItems` | 200 |
| `maxNumberOfCustomItems` | 200 |
| `maxNumberOfCheckedGroceryListItems` | 24 |

Mutation responses carry group/list change tokens and optional affected models.
Checking an item may return `updatedItem`, `deletedItem`, and
`deletedTemporaryGroceryItem`, because the backend may remove old checked items
and temporary catalog records according to its retention policy. The smartphone
service contains no direct `DELETE` method for a grocery-list entry; checking is
the app-supported lifecycle operation. Custom grocery catalog items do have a
separate `DELETE /items/{itemId}` method.

A `LoyaltyCard` contains nullable `id` and `groupId`, required `name`,
`data`, and `barcodeType`, optional `color`, and integer `sortOrder`.
Overview and mutation wrappers carry `groupLoyaltyCardChangeToken`; the
grocery overview itself uses the shorter wire key
`loyaltyCardChangeToken`. Concrete barcode enum strings were not inferred
beyond their generated string converter and therefore remain an unrestricted
string in the Python model.

## Legacy meal plan and recipe summaries

Daely's wire model calls a saved recipe a `Meal`. All paths below start with
`/api/groups/{groupId}/meal-plan`.

| Method | Path | Input | Result |
|---|---|---|---|
| `GET` | `/overview?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | query dates | `MealPlanOverview` |
| `POST` | `/categories` | full `MealCategory` JSON | `MealCategory` |
| `PUT` | `/categories/{categoryId}` | full `MealCategory` JSON | `MealCategory` |
| `DELETE` | `/categories/{categoryId}` | - | no parsed body |
| `POST` | `/meal` | full `Meal` JSON | `Meal` |
| `PUT` | `/meal/{mealId}` | full `Meal` JSON | `Meal` |
| `DELETE` | `/meal/{mealId}` | - | no parsed body |
| `POST` | `/entries` | full `MealPlanEntry` JSON | `MealPlanEntry` |
| `POST` | `/entries/replace` | full `MealPlanEntry` JSON | `MealPlanEntry` |
| `PUT` | `/entries/{entryId}` | full `MealPlanEntry` JSON | `MealPlanEntry` |
| `DELETE` | `/entries/{entryId}/{date}?deleteType={wireCode}` | path date plus recurrence-delete code | no parsed body |

### Models

- `MealCategory`: nullable `id`, `name`, nullable `createdAt`, nullable `updatedAt`.
- `Meal`: nullable `id`, `categoryIds`, `name`, nullable `description`, nullable
  `emoji`, nullable `createdAt`, nullable `updatedAt`.
- `MealPlanEntry`: nullable `id`, `mealId`, `section`, `date`, `recurrence`,
  nullable `createdAt`, nullable `updatedAt`.
- `MealPlanOverview`: `meals`, `categories`, `entries`, `mealPlanConfig`.
- `MealPlanConfig`: `maxNumberOfCategories`, `maxNumberOfMeals`.
- `section`: `morning`, `noon`, or `evening`.

Dates use the ISO calendar-date wire form `YYYY-MM-DD`. Generated `ToJson`
methods serialize the full model for category, meal, and entry writes, including
nullable fields.

## Smartphone v1.5.2 meal-plan entry API

The current app moves dated assignments to
`/api/v2/groups/{groupId}/meal-plan/entries`. Recipe/category CRUD is no
longer part of this service; it lives under the v2 `/meals` resource below.

| Method | Path | Input | Result |
|---|---|---|---|
| `GET` | `/meal-plan/entries?week=YYYY-MM-DD&includeMeals={boolean}` | query date and flag | `MealPlanEntries` |
| `POST` | `/meal-plan/entries` | full `MealPlanEntry` | `MealPlanEntryMutationResult` |
| `POST` | `/meal-plan/entries/replace` | full `MealPlanEntry` | `MealPlanEntryMutationResult` |
| `PUT` | `/meal-plan/entries/{entryId}` | `{"recurrence": string[]}` | `MealPlanEntryMutationResult` |
| `DELETE` | `/meal-plan/entries/{entryId}/{date}?deleteType={wireCode}` | path date and recurrence-delete code | `MealPlanEntryMutationResult` |

`MealPlanEntries` contains `groupMealPlanChangeToken`,
`groupMealChangeToken`, the requested `week`, `entries`, and optionally
included compact `MealHeader` records. Mutation results wrap `groupId`,
`groupMealPlanChangeToken`, and a nullable `entry` (for example after
deletion). Unlike the legacy update, the current `PUT` sends only recurrence
rules.

## Smartphone v1.5.2 full-recipe API

The newer app loads and edits complete recipes through
`/api/v2/groups/{groupId}/meals`.

| Method | Path | Input | Result |
|---|---|---|---|
| `GET` | `/meals` | pagination and optional filters | `PaginatedMeals` |
| `GET` | `/meals/overview` | pagination and optional default language | `MealsOverview` |
| `GET` | `/meals/{mealId}` | - | `MealMutationResult` |
| `POST` | `/meals` | full `MealDetail` | `MealMutationResult` |
| `PUT` | `/meals/{mealId}` | full `MealDetail` | `MealMutationResult` |
| `DELETE` | `/meals/{mealId}` | - | `MealMutationResult` |
| `POST` | `/meals/categories` | full `MealCategoryV2` | `MealCategoryMutationResult` |
| `PUT` | `/meals/categories/{categoryId}` | full `MealCategoryV2` | `MealCategoryMutationResult` |
| `DELETE` | `/meals/categories/{categoryId}` | - | `MealCategoryMutationResult` |
| `PUT` | `/meals/{mealId}/likes` | `{"profileIds": string[]}` | `MealMutationResult` |
| `PUT` | `/meals/{mealId}/picture` | multipart image | picture mutation |
| `DELETE` | `/meals/{mealId}/picture` | - | picture mutation |

The collection query uses `page`, `pageSize`, `Filter.CategoryId`,
`Filter.Name`, `Filter.DefaultsForLanguage`, and `Filter.LikedByProfileId`.
The overview uses `mealsPage`, `mealsPageSize`, and `defaultsForLanguage`.

### Complete structured recipe fields

`MealDetail` contains:

- identity/display: `id`, `name`, `description`, `emoji`, `imageUrl`,
  and `websiteLink`;
- nutrition/preparation: nullable integer `calories`, nullable integer `time`,
  and integer `portions` (default 1);
- structured `ingredients` and ordered `instructions`;
- `categoryIds`, `likedByProfileIds`, `isDefault`, and timestamps.

Each ingredient carries nullable `groceryItemId`, nullable `ingredientName`,
required string `amount`, and `ignoredForGroceryList`. The string amount
preserves units such as `2 tbsp`. Each instruction carries a client-generated
UUID-v4 `id`, integer `position`, and `text`.

Thus calories, duration, portions, ingredients, and preparation steps are not
embedded into one description field. They are separate wire fields. The older
`/meal-plan/overview` only returns compact recipe summaries, which explains why
those manually populated native fields were invisible in the earlier read.

Picture upload uses multipart field `imageFile`, filename
`meal_image.webp`, and media type `image/webp`; both upload and delete return
`MealMutationResult`. These routes and all structured recipe fields, categories,
and likes are implemented and offline-tested.

### Recurring-entry deletion

`DeleteRecurrenceType` has a stored wire value that is not its Dart enum ordinal:

| UI meaning | Dart name | Dart ordinal | `deleteType` wire code |
|---|---|---:|---:|
| this occurrence | `deleteOne` | 0 | 1 |
| this and following | `deleteFuture` | 1 | 2 |
| the complete series | `deleteAll` | 2 | 0 |

Sending the enum ordinal would delete the wrong scope for two of the three
choices. The Python client therefore exposes a dedicated integer enum with the
wire codes `DELETE_ALL=0`, `DELETE_ONE=1`, and `DELETE_FUTURE=2`.

## Implementation and verification status

Implemented in:

- `daely-google-bridge/src/daely_google_bridge/models.py`
- `daely-google-bridge/src/daely_google_bridge/daely_client.py`
- `daely-google-bridge/tests/test_list_meal_client.py`
- `daely-google-bridge/tests/test_grocery_client.py`

The offline tests assert exact paths, query parameters, JSON bodies, response
conversion, current checklist/meal-plan change-token wrappers, structured
recipe fields, grocery/loyalty-card wrappers, and recurrence-delete wire values
against mocked HTTP responses. The two legacy read contracts and the v2
recipe-detail contract are additionally confirmed against the current
production backend. The live v2 recipe contained correctly typed nutrition,
duration, portion, ingredient, instruction, and category fields. Its v2 delete
contract and identity-bearing response are also production-confirmed. Current
v2 checklist, meal-plan, grocery, and loyalty-card contracts remain statically
recovered and offline-tested only.

## Remaining controlled verification

The v2 recipe-detail read is complete. Possible next non-mutating verifications,
if desired, are explicitly approved current v2 checklist, meal-plan, or grocery
overview reads with the same sanitization rules.

The temporary recipe cleanup is complete. Further write verification must
proceed only as isolated, user-approved operations on explicit test records.
Each future `POST`, `PUT`, or `DELETE`, including picture changes and
cleanup, requires its own approval.
