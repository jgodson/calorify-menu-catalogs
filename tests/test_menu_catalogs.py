"""Tests use small made-up fixtures; no real menu data is stored in this repository."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from menu_catalogs import dairy_queen, mcdonalds, tim_hortons
from menu_catalogs.common import CatalogError, validate
from menu_catalogs.sources import SOURCES
from menu_catalogs.store import Store

DQ_PAGE = """<html><head><link rel="canonical" href="https://example.test/dq"/></head><body>
<h3 class="table-group-sub-heading">Blizzard® Treats</h3>
<table><tr><th>Menu Item</th><th>Calories (kcal)</th></tr>
<tr><th>Oreo Blizzard - Small</th><td>610</td><td>22</td><td>12</td><td>0.5</td><td>45</td><td>380</td><td>93</td><td>1</td><td>73</td><td>13</td><td>Milk, Wheat</td></tr>
<tr><th>Broken Row</th><td>100</td><td>1</td></tr></table>
<h3 class="table-group-sub-heading">Signature StackburgersTM (precooked weight)</h3>
<table><tr><th>Bacon Burger - Double</th><td>770</td><td>46</td><td>16</td><td>1</td><td>130</td><td>1,630</td><td>45</td><td>2</td><td>13</td><td>46</td><td>M\\S</td></tr></table>
</body></html>""".encode()


def mcd_item(item_id: int, name: str, internal: str, category: str, calories: str, grams: str = "250") -> dict:
    nutrient = lambda key, value, uom="g": {"nutrient_name_id": key, "value": value, "uom": uom}  # noqa: E731
    return {
        "item_id": item_id, "item_marketing_name": name, "item_name": internal,
        "default_category": {"category": {"name": category}},
        "nutrient_facts": {"nutrient": [
            nutrient("calories", calories, "Cals"), nutrient("primary_serving_size", grams),
            nutrient("protein", "20"), nutrient("sodium", "900", "mg"), nutrient("caffeine", "0", {}),
        ]},
    }


def mcd_har() -> bytes:
    beef = {"items": {"item": [
        mcd_item(1, "Big Burger", "Big Burger", "Beef", "550"),
        mcd_item(2, "Wrap", "Wrap - Grilled", "Beef", "480"),
        mcd_item(3, "Wrap", "Wrap - Crispy", "Featured Items ", "610"),
    ]}}
    return json.dumps({"log": {"entries": [
        {"request": {"url": "https://example.test/page.html", "headers": [{"name": "Cookie", "value": "secret"}]},
         "response": {"content": {"text": "<html></html>"}}},
        {"request": {"url": "https://example.test/dnaapp/itemList?item=1", "headers": [{"name": "Cookie", "value": "secret"}]},
         "response": {"content": {"text": json.dumps(beef)}}},
    ]}}).encode()


class ConverterTests(unittest.TestCase):
    def test_dairy_queen_reads_tables_categories_and_skips_misaligned_rows(self):
        catalog, skipped = dairy_queen.build([DQ_PAGE])
        foods = {food["id"]: food for food in catalog["foods"]}
        self.assertEqual(set(foods), {"oreo-blizzard-small", "bacon-burger-double"})
        self.assertEqual(foods["oreo-blizzard-small"]["category"], "Blizzard Treats")
        self.assertEqual(foods["bacon-burger-double"]["category"], "Signature Stackburgers")
        self.assertEqual(foods["bacon-burger-double"]["nutrients"]["sodium"], 1630)
        self.assertEqual(catalog["catalog"]["sourceUrl"], "https://example.test/dq")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(validate(catalog), [])

    def test_dairy_queen_rejects_pages_without_tables(self):
        with self.assertRaises(CatalogError):
            dairy_queen.build([b"<html>nothing</html>"])

    def test_mcdonalds_keeps_only_item_lists_and_disambiguates_variants(self):
        prepared = SOURCES["mcdonalds"].prepare(mcd_har())
        self.assertNotIn(b"secret", prepared)
        catalog, notes = SOURCES["mcdonalds"].convert(prepared)
        names = sorted(food["name"] for food in catalog["foods"])
        self.assertEqual(names, ["Big Burger", "Wrap - Crispy", "Wrap - Grilled"])
        burger = next(food for food in catalog["foods"] if food["id"] == "1")
        self.assertEqual(burger["category"], "Beef")
        self.assertEqual(burger["serving"], {"description": "1 item (250 g)", "grams": 250})
        self.assertEqual(burger["nutrients"], {"protein": 20, "sodium": 900})
        self.assertEqual(notes, ["1 calculator categories"])
        self.assertEqual(validate(catalog), [])

    def test_mcdonalds_rejects_unrelated_files(self):
        with self.assertRaises(CatalogError):
            mcdonalds.extract_item_lists(b"not json")
        with self.assertRaises(CatalogError):
            SOURCES["mcdonalds"].prepare(json.dumps({"log": {"entries": []}}).encode())

    def test_tim_hortons_filters_packs_and_reusable_cups_and_adds_coffee_orders(self):
        def option(name, choices):
            return {"name": {"en": name}, "options": [
                {"modifierMultiplier": {"_ref": f"modifierMultiplier_{whole}-00-1"}, "nutrition": nutrition}
                for whole, nutrition in choices]}

        coffee = {
            "_id": "item_1", "name": {"en": "Medium Coffee Original Blend", "fr": "Café moyen"},
            "nutritionWithModifiers": {"calories": 4, "proteins": 0.5},
            "options": [option("Cream", [(1, {"calories": 70, "fat": 6}), (2, {"calories": 140, "fat": 12})]),
                        option("Sugar", [(1, {"calories": 36, "sugar": 9}), (2, {"calories": 72, "sugar": 18})])],
        }
        items = {
            "item_1": coffee,
            "item_2": {"_id": "item_2", "name": {"en": "Medium Iced Latte - Reusable Cup"}, "nutrition": {"calories": 150}},
            "item_3": {"_id": "item_3", "name": {"en": "12 Assorted Muffins"}, "nutrition": {"calories": 5040}},
        }
        found = {"item_1": "Hot Drinks", "item_2": "Cold Drinks", "item_3": "Baked Goods"}
        foods = {food["name"]: food for food in tim_hortons.foods_from_items(found, items)}
        self.assertNotIn("12 Assorted Muffins", foods)
        self.assertIn("Medium Iced Latte", foods)
        double_double = foods["Medium Coffee Original Blend, Double Double"]
        self.assertEqual(double_double["calories"], 216)
        self.assertEqual(double_double["nutrients"]["sugars"], 18)
        self.assertEqual(double_double["id"], "item_1-double-double")
        self.assertEqual(double_double["nameFr"], "Café moyen, Double Double")


class StoreAndServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_upload_stores_sanitized_input_and_rebuilds_from_it(self):
        summary = self.store.upload(SOURCES["mcdonalds"], mcd_har())
        self.assertEqual(summary["foodCount"], 3)
        stored = self.store.input_path(SOURCES["mcdonalds"]).read_bytes()
        self.assertNotIn(b"secret", stored)
        first_etag = self.store.read_catalog("mcdonalds-ca")[1]
        self.store.rebuild_uploaded()
        self.assertEqual(self.store.read_catalog("mcdonalds-ca")[1], first_etag)

    def test_failed_upload_keeps_previous_catalogue_and_records_error(self):
        self.store.upload(SOURCES["dairy-queen"], DQ_PAGE)
        with self.assertRaises(CatalogError):
            self.store.upload(SOURCES["dairy-queen"], b"<html></html>")
        self.assertIsNotNone(self.store.read_catalog("dairy-queen-ca"))
        self.assertIn("No Dairy Queen", self.store.status()["dairy-queen"]["lastError"])

    def serve(self, **env):
        from menu_catalogs.server import Config, make_handler
        with mock.patch.dict(os.environ, {"DATA_DIR": self.directory.name, **env}, clear=False):
            config = Config()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, config))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def request(self, url, data=None, headers=None, method=None):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, dict(error.headers), error.read()

    def test_uploads_need_the_token_and_catalogues_support_etags(self):
        base = self.serve(UPLOAD_TOKEN="upload-secret", READ_TOKEN="")
        status, _, _ = self.request(f"{base}/api/sources/dairy-queen/upload", DQ_PAGE, method="POST")
        self.assertEqual(status, 401)
        status, _, body = self.request(f"{base}/api/sources/dairy-queen/upload", DQ_PAGE,
                                       {"Authorization": "Bearer upload-secret"}, "POST")
        self.assertEqual((status, json.loads(body)["foodCount"]), (200, 2))

        status, headers, body = self.request(f"{base}/catalogs/dairy-queen-ca.catalog.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["catalog"]["id"], "dairy-queen-ca")
        status, _, _ = self.request(f"{base}/catalogs/dairy-queen-ca.catalog.json", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)

        status, _, page = self.request(f"{base}/")
        self.assertEqual(status, 200)
        self.assertIn(b"Dairy Queen (Canada)", page)

    def test_uploads_are_disabled_without_a_token_and_read_token_is_enforced(self):
        base = self.serve(UPLOAD_TOKEN="", READ_TOKEN="read-secret")
        status, _, _ = self.request(f"{base}/api/sources/dairy-queen/upload", DQ_PAGE,
                                    {"Authorization": "Bearer anything"}, "POST")
        self.assertEqual(status, 403)
        self.store.upload(SOURCES["dairy-queen"], DQ_PAGE)
        self.assertEqual(self.request(f"{base}/catalogs/dairy-queen-ca.catalog.json")[0], 401)
        self.assertEqual(self.request(f"{base}/catalogs/dairy-queen-ca.catalog.json?token=read-secret")[0], 200)


if __name__ == "__main__":
    unittest.main()
