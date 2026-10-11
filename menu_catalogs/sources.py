"""The restaurants this project knows how to turn into catalogues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from . import dairy_queen, mcdonalds, tim_hortons
from .common import CatalogError


@dataclass(frozen=True)
class Source:
    key: str
    catalog_id: str
    name: str
    # "fetch" sources are downloaded automatically; "upload" sources come from a file saved in a browser.
    kind: str
    how_to: str = ""
    accept: str = ""
    fetch: Callable[[], dict] | None = None
    # Turns an uploaded file into the input that is stored (stripping anything that shouldn't be kept).
    prepare: Callable[[bytes], bytes] | None = None
    # Builds the catalogue from a stored input; returns (catalog, notes).
    convert: Callable[[bytes], tuple[dict, list[str]]] | None = None


def _mcdonalds_prepare(raw: bytes) -> bytes:
    item_lists = mcdonalds.extract_item_lists(raw)
    if not item_lists:
        raise CatalogError("No itemList responses in that file. Open each category in the nutrition calculator "
                           "while the Network tab is recording, then export the HAR.")
    return json.dumps(item_lists, ensure_ascii=False).encode("utf-8")


def _mcdonalds_convert(stored: bytes) -> tuple[dict, list[str]]:
    item_lists = json.loads(stored.decode("utf-8"))
    return mcdonalds.build(item_lists), [f"{len(item_lists)} calculator categories"]


def _dairy_queen_prepare(raw: bytes) -> bytes:
    dairy_queen.build([raw])  # raises if the page has no nutrition tables
    return raw


def _dairy_queen_convert(stored: bytes) -> tuple[dict, list[str]]:
    catalog, skipped = dairy_queen.build([stored])
    return catalog, [f"skipped {line}" for line in skipped]


SOURCES: dict[str, Source] = {
    "tim-hortons": Source(
        key="tim-hortons",
        catalog_id=tim_hortons.CATALOG_ID,
        name=tim_hortons.NAME,
        kind="fetch",
        how_to="Downloaded automatically from the data timhortons.ca loads its menu from.",
        fetch=tim_hortons.build,
    ),
    "mcdonalds": Source(
        key="mcdonalds",
        catalog_id=mcdonalds.CATALOG_ID,
        name=mcdonalds.NAME,
        kind="upload",
        how_to=("In Chrome, open the McDonald's Canada nutrition calculator, open developer tools, filter the "
                "Network tab to itemList, click each menu category once, then Export HAR and upload it here. "
                "Only the nutrition responses are kept; the rest of the HAR is discarded."),
        accept=".har,.json,application/json",
        prepare=_mcdonalds_prepare,
        convert=_mcdonalds_convert,
    ),
    "dairy-queen": Source(
        key="dairy-queen",
        catalog_id=dairy_queen.CATALOG_ID,
        name=dairy_queen.NAME,
        kind="upload",
        how_to=("Open dairyqueen.com/en-ca/nutrition/food-treats/, wait for the tables to load, use "
                "File → Save Page As (HTML), and upload the saved page here."),
        accept=".html,.htm,text/html",
        prepare=_dairy_queen_prepare,
        convert=_dairy_queen_convert,
    ),
}
