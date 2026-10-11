"""Dairy Queen (Canada): converts the nutrition page saved from a browser (File → Save Page As)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .common import NUTRIENT_PRECISION, CatalogError, catalog_document, clean_text, rounded

CATALOG_ID = "dairy-queen-ca"
NAME = "Dairy Queen (Canada)"
SOURCE_URL = "https://www.dairyqueen.com/en-ca/nutrition/food-treats/"
# Column order of every DQ Canada nutrition table after "Menu Item".
COLUMNS = ["calories", "total_fat", "saturated_fat", "trans_fat", "cholesterol", "sodium",
           "carbohydrate", "fibre", "sugars", "protein"]


class NutritionTableParser(HTMLParser):
    """Collects each <table>'s rows together with the most recent section heading above it."""

    def __init__(self, heading_classes: set[str]):
        super().__init__()
        self.heading_classes = heading_classes
        self.tables: list[dict] = []
        self.heading = ""
        self._heading_text: str | None = None
        self._table: dict | None = None
        self._row: list[str] | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag, attrs):
        classes = set((dict(attrs).get("class") or "").split())
        if tag in ("h2", "h3") and classes & self.heading_classes:
            self._heading_text = ""
        elif tag == "table":
            self._table = {"heading": self.heading, "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_endtag(self, tag):
        if tag in ("h2", "h3") and self._heading_text is not None:
            self.heading = clean_text(self._heading_text)
            self._heading_text = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(clean_text(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._heading_text is not None:
            self._heading_text += data
        if self._cell is not None:
            self._cell += data


def category(heading: str) -> str:
    heading = re.sub(r"\(.*?\)", "", heading)
    heading = re.sub(r"(TM|™|®)", "", heading)
    return clean_text(heading) or "Menu"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("®", "").replace("™", "")).strip("-")


def make_food(row: list[str], food_category: str, skipped: list[str]) -> dict | None:
    name = clean_text(row[0])
    values = []
    for cell in row[1:]:
        try:
            values.append(float(cell.replace(",", "")))
        except ValueError:
            break  # the allergen column (text) ends the numbers
    if not name or len(values) != len(COLUMNS):
        skipped.append(f"{name or row}: expected {len(COLUMNS)} numbers, found {len(values)}")
        return None
    nutrition = dict(zip(COLUMNS, values))
    return {
        "id": slug(name),
        "name": name,
        "brand": "Dairy Queen",
        "category": food_category,
        "serving": {"description": "1 item, as served"},
        "calories": rounded(nutrition.pop("calories"), 0),
        "nutrients": {key: rounded(value, NUTRIENT_PRECISION[key]) for key, value in nutrition.items()},
    }


def build(pages: list[bytes]) -> tuple[dict, list[str]]:
    """Returns the catalogue and a list of rows that were skipped."""
    foods: dict[str, dict] = {}
    skipped: list[str] = []
    source_url = None
    for page in pages:
        html = page.decode("utf-8", errors="replace")
        match = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
        source_url = source_url or (match.group(1) if match else None)
        parser = NutritionTableParser({"table-group-heading", "table-group-sub-heading"})
        parser.feed(html)
        for table in parser.tables:
            for row in table["rows"]:
                if not row or row[0] == "Menu Item":
                    continue
                food = make_food(row, category(table["heading"]), skipped)
                if food and food["id"] not in foods:
                    foods[food["id"]] = food
    if not foods:
        raise CatalogError("No Dairy Queen nutrition tables found. Save the nutrition page itself (File → Save Page As).")
    return catalog_document(CATALOG_ID, NAME, "Dairy Queen", source_url or SOURCE_URL, list(foods.values())), skipped
