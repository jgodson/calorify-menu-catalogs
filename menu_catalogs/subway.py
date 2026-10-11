"""Subway (Canada): converts the Canada nutrition information PDF.

The PDF is turned into text with poppler's `pdftotext -layout`; only that text is stored.
"""

from __future__ import annotations

import re
import subprocess
import tempfile

from .common import NUTRIENT_PRECISION, CatalogError, catalog_document, clean_text, rounded

CATALOG_ID = "subway-ca"
NAME = "Subway (Canada)"
SOURCE_URL = "https://www.subway.com/en-CA"
# Column order of every row after the item name. Calcium and iron are only published as % DV,
# so they're left out rather than converted with a guessed reference amount.
COLUMNS = ["grams", "calories", "total_fat", "saturated_fat", "trans_fat", "cholesterol", "sodium",
           "carbohydrate", "fibre", "sugars", "protein", "calcium_dv", "iron_dv"]
SKIPPED_COLUMNS = {"calcium_dv", "iron_dv"}

ROW = re.compile(r"^(?P<name>\S.*?)\s+(?P<numbers>(?:\d+(?:\.\d+)?\s+){%d}\d+(?:\.\d+)?)\s*$" % (len(COLUMNS) - 1))
TRAILING_NUMBERS = re.compile(r"(?:\s+\d+(?:\.\d+)?){3,}\s*$")
EDITION = re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4})\b")

# Item names repeat across sections (Turkey Breast is a sub, a wrap, a salad, and a topping),
# so names get the form in brackets. "6-inch" sections also get a doubled footlong, as the
# PDF instructs ("double values for footlong nutrition information").
FORMS = [
    (re.compile(r"fresh fit", re.I), "6-inch Fresh Fit", "1 6-inch sub"),
    (re.compile(r"breakfast sandwiches", re.I), "6-inch Breakfast", "1 6-inch sub"),
    (re.compile(r"breakfast wraps", re.I), "Breakfast Wrap", "1 wrap"),
    (re.compile(r"breakfast ciabatta", re.I), "Breakfast Ciabatta", "1 sandwich"),
    (re.compile(r"\bsubs\b", re.I), "6-inch", "1 6-inch sub"),
    (re.compile(r"snackwiches", re.I), "Snackwich", "1 snackwich"),
    (re.compile(r"\bwraps\b", re.I), "Wrap", "1 wrap"),
    (re.compile(r"\bsalads\b", re.I), "Salad", "1 salad"),
    (re.compile(r"power bowls", re.I), "Power Bowl", "1 bowl"),
]


def pdf_to_text(pdf: bytes) -> str:
    if not pdf.startswith(b"%PDF"):
        raise CatalogError("That isn't a PDF. Upload Subway's Canada nutrition information PDF.")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as file:
        file.write(pdf)
        file.flush()
        try:
            result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", file.name, "-"],
                                    capture_output=True, timeout=60, check=False)
        except FileNotFoundError:
            raise CatalogError("Reading PDFs needs pdftotext (poppler-utils), which isn't installed.") from None
        except subprocess.TimeoutExpired:
            raise CatalogError("Reading the PDF took too long.") from None
    if result.returncode != 0:
        raise CatalogError("pdftotext couldn't read that PDF.")
    return result.stdout.decode("utf-8", errors="replace")


def clean_name(text: str) -> str:
    text = text.replace("*", "")
    text = re.sub(r"(?<=\))\d+$", "", text.strip())  # footnote number after "(as packaged)"
    return clean_text(text)


def category(heading: str) -> str:
    heading = re.sub(r"\(.*?\)", "", heading.replace("*", ""))
    heading = re.sub(r'^\s*6"\s*', "", heading)
    heading = clean_text(re.sub(r"[®™]", "", heading))
    if heading.isupper():
        heading = heading.title()
    return heading[:1].upper() + heading[1:] if heading else "Menu"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("®", "").replace("™", "")).strip("-")


def food(name: str, food_category: str, serving: str, values: dict, factor: int = 1) -> dict:
    grams = values["grams"] * factor
    nutrients = {key: rounded(value * factor, NUTRIENT_PRECISION[key])
                 for key, value in values.items() if key not in SKIPPED_COLUMNS | {"grams", "calories"}}
    return {
        "id": slug(name),
        "name": name,
        "brand": "Subway",
        "category": food_category,
        "serving": {"description": f"{serving} ({rounded(grams, 0)} g)", "grams": rounded(grams, 0)}
                   if grams else {"description": serving},
        "calories": rounded(values["calories"] * factor, 0),
        "nutrients": nutrients,
    }


def parse(text: str) -> tuple[list[dict], list[str], str | None]:
    """Returns the foods, rows that were skipped, and the edition (e.g. "October 2026")."""
    foods: dict[str, dict] = {}
    skipped: list[str] = []
    heading = ""
    edition_match = EDITION.search(text[:2000])
    for line in text.splitlines():
        if not line.strip() or line[0].isspace():
            continue  # notes and column headings are indented
        match = ROW.match(line)
        if not match:
            if TRAILING_NUMBERS.search(line) and re.search(r"[A-Za-z]", line):
                skipped.append(f"{clean_text(line)[:60]}: expected {len(COLUMNS)} numbers")
            elif re.search(r"[A-Za-z]", line):
                heading = re.split(r"\s{2,}", line.strip())[0]
            continue
        values = dict(zip(COLUMNS, (float(number) for number in match.group("numbers").split())))
        item = clean_name(match.group("name"))
        food_category = category(heading)
        form = next(((label, serving) for pattern, label, serving in FORMS if pattern.search(heading)), None)
        if form is None:
            made = [food(item, food_category, "1 serving", values)]
        else:
            label, serving = form
            made = [food(f"{item} ({label})", food_category, serving, values)]
            if label.startswith("6-inch"):
                footlong = label.replace("6-inch", "Footlong")
                made.append(food(f"{item} ({footlong})", food_category, "1 footlong", values, factor=2))
        for entry in made:
            if entry["id"] in foods:
                skipped.append(f"{entry['name']}: listed twice")
            else:
                foods[entry["id"]] = entry
    return list(foods.values()), skipped, edition_match.group(1) if edition_match else None


def build(text: str) -> tuple[dict, list[str]]:
    foods, skipped, edition = parse(text)
    if not foods:
        raise CatalogError("No Subway nutrition rows found. Upload the Canada nutrition information PDF.")
    catalog = catalog_document(CATALOG_ID, NAME, "Subway", SOURCE_URL, foods)
    if edition:
        catalog["catalog"]["version"] = edition
    return catalog, [f"skipped {line}" for line in skipped]
