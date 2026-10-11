"""McDonald's (Canada): converts the nutrition calculator's itemList responses captured in a browser.

Open the nutrition calculator, open each category once with the Network tab recording, and
export a HAR. Saved raw itemList responses are accepted too. Only the itemList response bodies
are kept, so cookies and other session details in a HAR never need to be stored.
"""

from __future__ import annotations

import base64
import collections
import json
import re

from .common import NUTRIENT_PRECISION, CatalogError, catalog_document, clean_text, rounded

CATALOG_ID = "mcdonalds-ca"
NAME = "McDonald's (Canada)"
SOURCE_URL = "https://www.mcdonalds.com/ca/en-ca/about-our-food/nutrition-calculator.html"
NUTRIENT_KEYS = {
    "fat": "total_fat", "saturated_fat": "saturated_fat", "trans_fat": "trans_fat",
    "cholesterol": "cholesterol", "sodium": "sodium", "carbohydrate": "carbohydrate",
    "fibre": "fibre", "sugars": "sugars", "protein": "protein", "potassium": "potassium",
    "calcium": "calcium", "iron": "iron", "vitaminA": "vitamin_a", "VitaminD": "vitamin_d",
}
# Item categories that aren't real menu sections; each response is labelled by its majority real category.
NOISE_CATEGORIES = re.compile(r"^(featured items|do not show|gws-|happy meal)", re.IGNORECASE)
CATEGORY_NAMES = {"McCafé & Bakery": "McCafé", "McPicks": "McValue Snacks"}


def extract_item_lists(raw: bytes) -> list[dict]:
    """Pulls itemList response bodies out of a HAR, or accepts a single saved itemList response."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("That file isn't a HAR or an itemList response.") from error

    if isinstance(data, dict) and "log" in data:
        found = []
        for entry in data["log"].get("entries", []):
            if "/dnaapp/itemList" not in entry.get("request", {}).get("url", ""):
                continue
            content = entry.get("response", {}).get("content") or {}
            text = content.get("text") or ""
            if content.get("encoding") == "base64":
                text = base64.b64decode(text).decode("utf-8", "replace")
            if text:
                found.append(json.loads(text))
        return found
    if isinstance(data, dict) and "items" in data:
        return [data]
    if isinstance(data, list) and all(isinstance(entry, dict) and "items" in entry for entry in data):
        return data  # previously extracted responses
    raise CatalogError("That file isn't a HAR or an itemList response.")


def response_items(response: dict) -> list[dict]:
    items = (response.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def response_category(items: list[dict]) -> str:
    names = collections.Counter()
    for item in items:
        name = ((item.get("default_category") or {}).get("category") or {}).get("name", "").strip()
        if name and not NOISE_CATEGORIES.match(name):
            names[name] += 1
    if not names:
        return "Menu"
    top = names.most_common(1)[0][0]
    return CATEGORY_NAMES.get(top, top)


def number(nutrient: dict | None) -> float | None:
    if not nutrient:
        return None
    try:
        return float(str(nutrient.get("value")).replace(",", ""))
    except ValueError:
        return None


def make_food(item: dict, category: str) -> dict | None:
    name = clean_text(item.get("item_marketing_name") or item.get("item_name"))
    nutrients_raw = {n.get("nutrient_name_id"): n for n in (item.get("nutrient_facts") or {}).get("nutrient") or []}
    calories = number(nutrients_raw.get("calories"))
    if not name or calories is None or item.get("item_id") is None:
        return None

    nutrients = {}
    for source_key, calorify_key in NUTRIENT_KEYS.items():
        value = number(nutrients_raw.get(source_key))
        if value is not None:
            nutrients[calorify_key] = rounded(value, NUTRIENT_PRECISION[calorify_key])

    serving = {"description": "1 item, as served"}
    size = nutrients_raw.get("primary_serving_size") or {}
    amount = number(size)
    unit = size.get("uom").lower() if isinstance(size.get("uom"), str) else ""
    if amount and amount > 0 and unit in ("g", "ml"):
        pretty = rounded(amount, 0)
        serving = {"description": f"1 item ({pretty} {'g' if unit == 'g' else 'mL'})"}
        serving["grams" if unit == "g" else "millilitres"] = pretty

    return {
        "id": str(item["item_id"]),
        "name": name,
        "brand": "McDonald's",
        "category": category,
        "serving": serving,
        "calories": rounded(calories, 0),
        "nutrients": nutrients,
    }


def build(item_lists: list[dict]) -> dict:
    if not item_lists:
        raise CatalogError("No itemList responses found. Open each category in the nutrition calculator "
                           "while the Network tab is recording, then export the HAR.")
    foods: dict[str, dict] = {}
    internal_names: dict[str, str] = {}
    for response in item_lists:
        items = response_items(response)
        food_category = response_category(items)
        for item in items:
            food = make_food(item, food_category)
            if food and food["id"] not in foods:
                foods[food["id"]] = food
                internal_names[food["id"]] = clean_text(item.get("item_name"))

    # Variants such as Grilled and Crispy McWraps share a display name; the internal name tells them apart.
    name_counts = collections.Counter(food["name"] for food in foods.values())
    for food_id, food in foods.items():
        internal = internal_names.get(food_id)
        if name_counts[food["name"]] > 1 and internal and internal != food["name"]:
            food["name"] = internal
    return catalog_document(CATALOG_ID, NAME, "McDonald's", SOURCE_URL, list(foods.values()))
