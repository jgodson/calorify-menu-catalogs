"""Shared helpers for building calorify-catalog files."""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.request

USER_AGENT = "calorify-menu-catalogs/1.0 (personal nutrition catalogue; low volume; +https://github.com/jgodson/calorify-menu-catalogs)"
REQUEST_PAUSE_SECONDS = 0.5

# Calorify nutrient ids and the number of decimals kept for each.
NUTRIENT_PRECISION = {
    "total_fat": 1, "saturated_fat": 1, "trans_fat": 1, "cholesterol": 0, "sodium": 0,
    "carbohydrate": 1, "fibre": 1, "sugars": 1, "protein": 1,
    "potassium": 0, "calcium": 0, "iron": 1, "vitamin_a": 0, "vitamin_d": 1,
}


class CatalogError(Exception):
    """A source couldn't be turned into a catalogue; the message is safe to show to the person."""


def http_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    time.sleep(REQUEST_PAUSE_SECONDS)
    return data


def clean_text(text: str | None) -> str:
    """Collapses whitespace and removes zero-width characters some menus include."""
    text = re.sub(r"[​‌‍﻿]", "", text or "")
    return " ".join(text.split())


def rounded(value: float, decimals: int) -> float | int:
    number = round(float(value), decimals)
    return int(number) if decimals == 0 or number == int(number) else number


def catalog_document(catalog_id: str, name: str, publisher: str, source_url: str, foods: list[dict]) -> dict:
    today = dt.date.today().isoformat()
    return {
        "format": "calorify-catalog",
        "formatVersion": 1,
        "catalog": {
            "id": catalog_id,
            "name": name,
            "publisher": publisher,
            "region": "CA",
            "sourceUrl": source_url,
            "publishedAt": today,
            "version": today,
        },
        "foods": sorted(foods, key=lambda food: food["name"].lower()),
    }


def validate(catalog: dict) -> list[str]:
    """Mirrors the Calorify app's import rules so problems show up before publishing."""
    problems = []
    ids = set()
    for food in catalog["foods"]:
        label = food.get("name") or food.get("id")
        if food["id"] in ids:
            problems.append(f"{label}: duplicate id")
        ids.add(food["id"])
        if not food.get("name"):
            problems.append(f"{food['id']}: missing name")
        if not (food.get("serving") or {}).get("description"):
            problems.append(f"{label}: missing serving description")
        if not 0 <= food["calories"] < 10_000:
            problems.append(f"{label}: calories {food['calories']} out of range")
        for key, value in food["nutrients"].items():
            if key not in NUTRIENT_PRECISION:
                problems.append(f"{label}: unknown nutrient {key}")
            if value < 0:
                problems.append(f"{label}: negative {key}")
    if not catalog["foods"]:
        problems.append("no foods found")
    return problems
