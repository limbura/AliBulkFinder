import unittest
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import pack_size_for_variant, get_variants, offer_from_product, sorting_key, confirm_total, product_url_for_sku
from optimizer import run_search


class TestEngine(unittest.TestCase):
    def test_packs(self):
        self.assertEqual(pack_size_for_variant({"options": {"Количество": "100pcs"}}, None, "foo"), (100, "вариант SKU"))
        self.assertEqual(pack_size_for_variant({"options": {"Quantity": "50"}}, None, "foo"), (50, "вариант SKU"))
        self.assertEqual(pack_size_for_variant({"options": {"Color": "black"}}, {"pieces_per_lot": 20}, "foo"), (20, "лот в карточке"))
        self.assertEqual(pack_size_for_variant({"options": {}}, None, "100pcs hooks")[0], None)
        self.assertEqual(pack_size_for_variant({"options": {}}, None, "badge reel")[0], 1)
        self.assertEqual(pack_size_for_variant({"options": {"Size": "40 mm"}}, None, "foo")[0], 1)

    def test_links_open_selected_numeric_sku(self):
        base = "https://aliexpress.ru/item/1005007679081292.html"
        expected = base + "?sku_id=12000041782264143"
        self.assertEqual(product_url_for_sku(base, "12000041782264143"), expected)
        self.assertEqual(
            product_url_for_sku(base + "?spm=test&sku_id=11#details", "12000041782264143"),
            base + "?spm=test&sku_id=12000041782264143#details")
        self.assertEqual(product_url_for_sku(base, ""), base)
        self.assertEqual(product_url_for_sku(base, "unknown"), base)
        self.assertEqual(product_url_for_sku("https://example.com/item/11.html", "123"), "https://example.com/item/11.html")
        product = {"item_id": 1005007679081292, "name": "badge reel", "url": base}
        selected = {"variant": {"sku_id": "12000041782264143", "price_rub": 12},
                    "price": 12, "pack": 1, "basis": "вариант SKU", "lots": 2, "pieces": 2}
        self.assertEqual(offer_from_product(product, selected, 2, None).url, expected)

    def test_selection_and_totals(self):
        p = {"item_id": 123, "source_id": 0, "name": "reels", "url": "https://aliexpress.ru/item/123.html",
             "lot": None, "variants": [
                 {"sku_id": "A", "options": {"Quantity": "1pcs"}, "price_rub": 12, "stock": 300},
                 {"sku_id": "B", "options": {"Quantity": "50pcs"}, "price_rub": 390, "stock": 50},
                 {"sku_id": "C", "options": {"Quantity": "100pcs"}, "price_rub": 760, "stock": 20},
                 {"sku_id": "D", "options": {"Quantity": "120pcs"}, "price_rub": 930, "stock": 20}]}
        selected = get_variants(p, 200, 0)
        self.assertEqual([s["variant"]["sku_id"] for s in selected], ["C", "B", "A"])
        s = selected[0]
        self.assertEqual((s["lots"], s["pieces"]), (2, 200))
        quote = {**p, "selected_variant": {"sku_id": "C", "price_rub": 760},
                 "with_coupons": {"quantity": 2, "total_rub": 1480},
                 "delivery": {"cheapest_rub": 120}}
        row = offer_from_product(p, s, 200, quote)
        self.assertEqual(row.total, 1600)
        self.assertEqual(row.per_piece, 8)
        self.assertEqual(row.status, "ОЦЕНКА ПО ГОРОДУ")
        confirm_total(row, 1570)
        self.assertEqual(row.status, "ПРОВЕРЕНО В КОРЗИНЕ")
        self.assertEqual(row.per_piece, 7.85)

    def test_server_timestamp_becomes_readable_local_timestamp(self):
        from timefmt import local_datetime
        product = {"item_id": 1, "name": "badge reels", "source_id": 0,
                   "fetched_at": "2026-10-31T21:12:21+00:00"}
        selected = {"variant": {"sku_id": "V", "price_rub": 10}, "price": 10,
                    "pack": 100, "basis": "вариант SKU", "lots": 2, "pieces": 200}
        row = offer_from_product(product, selected, 200, None)
        self.assertEqual(row.fetched_at, local_datetime(product["fetched_at"]))
        self.assertRegex(row.fetched_at, r"^\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d$")

    def test_delivery_unknown_is_not_zero(self):
        p = {"item_id": 1, "source_id": 0, "name": "a", "url": "https://aliexpress.ru/item/1.html"}
        sel = {"variant": {"sku_id": "x", "price_rub": 100}, "price": 100, "pack": 20,
               "basis": "вариант SKU", "lots": 10, "pieces": 200}
        quote = {"selected_variant": {"sku_id": "x", "price_rub": 100}, "with_coupons": {"quantity": 10, "total_rub": 900},
                 "delivery": {"methods": []}}
        result = offer_from_product(p, sel, 200, quote)
        self.assertIsNone(result.total)
        self.assertIsNone(result.delivery)
        self.assertEqual(result.status, "ДОСТАВКА НЕИЗВЕСТНА")

    def test_over_99_cannot_get_fictitious_delivery(self):
        p = {"item_id": 2, "source_id": 0, "name": "a", "url": "https://aliexpress.ru/item/2.html"}
        sel = {"variant": {"sku_id": "x", "price_rub": 10}, "price": 10, "pack": 1,
               "basis": "вариант SKU", "lots": 200, "pieces": 200}
        result = offer_from_product(p, sel, 200, None)
        self.assertIsNone(result.total)
        self.assertEqual(result.goods_total, 2000)

    def test_shipping_to_wrong_city_rejected(self):
        p = {"item_id": 2, "source_id": 0, "name": "a", "url": "https://aliexpress.ru/item/2.html"}
        sel = {"variant": {"sku_id": "x", "price_rub": 10}, "price": 10, "pack": 100,
               "basis": "вариант SKU", "lots": 2, "pieces": 200}
        quote = {"selected_variant": {"sku_id": "x", "price_rub": 10},
                 "delivery": {"cheapest_rub": 0, "warning": "quoted other city"}}
        result = offer_from_product(p, sel, 200, quote)
        self.assertIsNone(result.total)
        self.assertEqual(result.status, "ДОСТАВКА НЕИЗВЕСТНА")

    def test_end_to_end_fake_mcp(self):
        class FakeProvider:
            async def search(self, q, page):
                return {"has_more": False, "items": [{"item_id": 55, "source_id": 0, "listing_sku_id": "B"}]}
            async def product(self, item_id, source_id, sku_id, quantity):
                return {"item_id": 55, "source_id": 0, "name": "badge reels",
                        "url": "https://aliexpress.ru/item/55.html",
                        "variants": [{"sku_id": "B", "options": {"Quantity": "100pcs"}, "price_rub": 790, "stock": 20}],
                        "selected_variant": {"sku_id": "B", "price_rub": 790},
                        "with_coupons": {"quantity": quantity, "total_rub": quantity * 790},
                        "delivery": {"cheapest_rub": 90}}
        rows = asyncio.run(run_search(FakeProvider(), ["badge", "reel"], 200, 0, 1, 10, lambda _: None))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].total, 1670)


if __name__ == "__main__":
    unittest.main()
