import asyncio
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filters import TitleFilter, PriceRange, parse_money
from optimizer import run_search


class TestFilters(unittest.TestCase):
    def test_terms_and_russian_substring(self):
        filt = TitleFilter.from_text("рулетк, ретрактор; badge reel\nretractable", "lanyard, шнурок", "strict")
        self.assertTrue(filt.check("Рулетки для бейджей 100 шт").allowed)
        self.assertTrue(filt.check("RETRACTABLE ID holders 20pcs").allowed)
        self.assertFalse(filt.check("100 шнурков для бейджей").allowed)
        self.assertFalse(filt.check("Бейджи и аксессуары").allowed)
        conflict = filt.check("Badge reel + lanyard 100pcs")
        self.assertTrue(conflict.allowed)
        self.assertEqual(conflict.quality, "сомнительно")

    def test_soft_and_off_modes(self):
        soft = TitleFilter.from_text("reel", "lanyard", "soft")
        self.assertEqual(soft.check("badge holder").quality, "сомнительно")
        self.assertTrue(soft.check("badge holder").allowed)
        self.assertFalse(soft.check("just lanyard").allowed)
        self.assertTrue(TitleFilter.from_text("reel", "lanyard", "off").check("just lanyard").allowed)
        strict = TitleFilter.from_text("reel", "", "strict")
        self.assertTrue(strict.check("", preliminary=True).allowed)
        self.assertFalse(strict.check("").allowed)

    def test_price_bounds_inclusive_and_unknown(self):
        rng = PriceRange.from_text("1 500,50", "3000")
        self.assertFalse(rng.accepts(None))
        self.assertTrue(rng.accepts(None, allow_unknown=True))
        self.assertTrue(rng.accepts(1500.50))
        self.assertTrue(rng.accepts(3000))
        self.assertFalse(rng.accepts(1499.99))
        self.assertFalse(rng.accepts(3000.01))
        self.assertTrue(PriceRange.from_text("", "").accepts(None))
        self.assertEqual(parse_money(" 1\u00a0234,56 ₽".replace(" ₽", "")), 1234.56)
        for low, high in (("-1", ""), ("200", "100"), ("NaN", ""), ("Inf", ""), ("bad", "")):
            with self.subTest(low=low, high=high), self.assertRaises(ValueError):
                PriceRange.from_text(low, high)

    def test_pre_filter_saves_calls_and_second_filter_rejects(self):
        class Fake:
            opened = []
            async def search(self, q, page):
                return {"has_more": False, "items": [
                    {"item_id": 1, "name": "lanyard / шнурки", "source_id": 0},
                    {"item_id": 2, "name": "retractable badge reel", "source_id": 0},
                    {"item_id": 3, "name": "retractable badge reel", "source_id": 0},
                    {"item_id": 4, "name": "", "source_id": 0},
                ]}
            async def product(self, item_id, source_id, sku_id, quantity):
                self.opened.append((item_id, quantity))
                name = {2: "retractable reel 100pcs", 3: "shoelaces", 4: "badge reel 100pcs"}[item_id]
                return {"item_id": item_id, "name": name, "source_id": 0,
                        "url": f"https://aliexpress.ru/item/{item_id}.html",
                        "variants": [{"sku_id": "100", "options": {"Количество": "100pcs"}, "price_rub": 200}],
                        "selected_variant": {"sku_id": "100", "price_rub": 200},
                        "delivery": {"cheapest_rub": 100}}
        provider = Fake()
        rows = asyncio.run(run_search(provider, ["test"], 200, 0, 1, 10, lambda _: None,
                                       TitleFilter.from_text("reel, retractable", "lanyard", "strict")))
        self.assertEqual([o.item_id for o in rows], ["2", "4"])
        self.assertNotIn(1, [n for n, _ in provider.opened])
        self.assertEqual(len([x for x in provider.opened if x[0] == 3]), 1)
        self.assertTrue(all(o.total == 500 for o in rows))


if __name__ == "__main__":
    unittest.main()
