"""Tim Hortons (Canada): reads the public Sanity dataset timhortons.ca loads its menu from."""

from __future__ import annotations

import json
import re
import urllib.parse

from .common import NUTRIENT_PRECISION, CatalogError, catalog_document, clean_text, http_json, rounded

CATALOG_ID = "tim-hortons-ca"
NAME = "Tim Hortons (Canada)"
SOURCE_URL = "https://www.timhortons.ca/menu"

PROJECT = "czqk28jt"
DATASET = "prod_th_ca"
MENU_ID = "2905970d-fa8d-4d25-9c85-6fbea4670a29"
SKIPPED_SECTIONS = {"Merchandise", "Tims® at Home", "Condiments"}
REUSABLE_SUFFIX = " - Reusable Cup"
# Multi-serving packs and catering bundles, e.g. "12 Assorted Muffins" or "Family Bundle (serves 12+)".
PACK_PATTERN = re.compile(r"^\d+ Assorted |bundle|\(serves ", re.IGNORECASE)
NUTRIENT_KEYS = {
    "fat": "total_fat", "saturatedFat": "saturated_fat", "transFat": "trans_fat",
    "cholesterol": "cholesterol", "sodium": "sodium", "carbohydrates": "carbohydrate",
    "fiber": "fibre", "sugar": "sugars", "proteins": "protein",
}
# Classic orders built from a brewed coffee's own add-on nutrition: (id suffix, label, creams, sugars).
COFFEE_ORDERS = [
    ("regular", "Regular", 1, 1),
    ("double-double", "Double Double", 2, 2),
    ("double-cream", "Double Cream", 2, 0),
]
MODIFIER_MULTIPLIER = re.compile(r"^modifierMultiplier_(\d+)-(\d+)-")


def query(groq: str) -> object:
    url = f"https://{PROJECT}.apicdn.sanity.io/v1/data/query/{DATASET}?" + urllib.parse.urlencode({"query": groq})
    return http_json(url)["result"]


def fetch_documents(ids: list[str]) -> dict[str, dict]:
    documents: dict[str, dict] = {}
    for start in range(0, len(ids), 40):
        for document in query(f"*[_id in {json.dumps(ids[start:start + 40])}]"):
            documents[document["_id"]] = document
    return documents


def add_on_nutrition(item: dict, option_name: str, amount: int) -> dict | None:
    """Nutrition for `amount` of a named add-on (e.g. 2 Cream) from the item's option list."""
    if amount == 0:
        return {}
    for option in item.get("options") or []:
        if ((option.get("name") or {}).get("en") or "").strip() != option_name:
            continue
        for choice in option.get("options") or []:
            match = MODIFIER_MULTIPLIER.match((choice.get("modifierMultiplier") or {}).get("_ref", ""))
            if match and float(f"{match.group(1)}.{match.group(2)}") == amount and choice.get("nutrition"):
                return choice["nutrition"]
    return None


def make_food(food_id: str, name: str, category: str, nutrition: dict, item: dict, french_suffix: str | None = None) -> dict:
    nutrients = {}
    for source_key, calorify_key in NUTRIENT_KEYS.items():
        if nutrition.get(source_key) is not None:
            nutrients[calorify_key] = rounded(nutrition[source_key], NUTRIENT_PRECISION[calorify_key])
    food = {
        "id": food_id,
        "name": name,
        "brand": "Tim Hortons",
        "category": category,
        "serving": {"description": "1 item, as served"},
        "calories": rounded(nutrition["calories"], 0),
        "nutrients": nutrients,
    }
    french = clean_text((item.get("name") or {}).get("fr"))
    if french.startswith("Remplir "):
        french = french[len("Remplir "):]
    if french:
        french = f"{french}, {french_suffix}" if french_suffix else french
        if french != name:
            food["nameFr"] = french
    if french_suffix:
        food["aliases"] = [french_suffix.lower()]
    return food


def coffee_variants(item_id: str, name: str, item: dict, category: str) -> list[dict]:
    if category != "Hot Drinks" or "Coffee" not in name or "Latte" in name:
        return []
    base = item.get("nutritionWithModifiers") or item.get("nutrition") or {}
    variants = []
    for suffix, label, creams, sugars in COFFEE_ORDERS:
        cream = add_on_nutrition(item, "Cream", creams)
        sugar = add_on_nutrition(item, "Sugar", sugars)
        if cream is None or sugar is None:
            continue
        combined = {key: (base.get(key) or 0) + (cream.get(key) or 0) + (sugar.get(key) or 0)
                    for key in ["calories", *NUTRIENT_KEYS]}
        variants.append(make_food(f"{item_id}-{suffix}", f"{name}, {label}", category, combined, item, label))
    return variants


def menu_items() -> tuple[dict[str, str], dict[str, dict]]:
    """Walks the main menu's sections and size pickers; returns item categories and item documents."""
    menu = query(f'*[_id=="{MENU_ID}"][0]')
    if not menu:
        raise CatalogError("Tim Hortons menu document not found; the site may have changed.")

    found: dict[str, str] = {}  # item id -> top-level section name
    frontier = [(ref["_ref"], None) for ref in menu.get("options", []) if ref.get("_ref")]
    seen: set[str] = set()
    while frontier:
        ids = [ref for ref, _ in frontier if ref not in seen]
        categories = {ref: category for ref, category in frontier}
        seen.update(ids)
        documents = fetch_documents(ids)
        frontier = []
        for doc_id in ids:
            document = documents.get(doc_id)
            if not document:
                continue
            kind = document["_type"]
            category = categories.get(doc_id)
            if kind == "section":
                section_name = (document.get("name") or {}).get("en", "").strip()
                if section_name in SKIPPED_SECTIONS:
                    continue
                child_category = category or section_name
                frontier += [(o["_ref"], child_category) for o in document.get("options") or [] if o.get("_ref")]
            elif kind == "picker":
                for option in document.get("options") or []:
                    ref = (option.get("option") or {}).get("_ref")
                    if ref:
                        frontier.append((ref, category))
            elif kind == "item":
                found.setdefault(doc_id, category or "Menu")
            # Combos and other document types are skipped: their parts are listed as items.
    return found, fetch_documents(sorted(found))


def foods_from_items(found: dict[str, str], items: dict[str, dict]) -> list[dict]:
    candidates: dict[str, tuple[str, dict]] = {}  # name -> (item id, item); newest edit wins
    for item_id in sorted(found):
        item = items.get(item_id)
        if not item or item.get("hideCalories"):
            continue
        name = clean_text((item.get("name") or {}).get("en"))
        nutrition = item.get("nutritionWithModifiers") or item.get("nutrition") or {}
        if not name or nutrition.get("calories") is None or PACK_PATTERN.search(name):
            continue
        if name.endswith(REUSABLE_SUFFIX):
            # Same drink in the customer's own cup; keep it only when no regular version exists.
            name = name[: -len(REUSABLE_SUFFIX)]
            if name in candidates:
                continue
        existing = candidates.get(name)
        if existing and existing[1].get("_updatedAt", "") >= item.get("_updatedAt", ""):
            continue
        candidates[name] = (item_id, item)

    foods = []
    for name, (item_id, item) in candidates.items():
        nutrition = item.get("nutritionWithModifiers") or item.get("nutrition") or {}
        category = found[item_id]
        foods.append(make_food(item_id, name, category, nutrition, item))
        foods += coffee_variants(item_id, name, item, category)
    return foods


def build() -> dict:
    found, items = menu_items()
    return catalog_document(CATALOG_ID, NAME, "Tim Hortons", SOURCE_URL, foods_from_items(found, items))
