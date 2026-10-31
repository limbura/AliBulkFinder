"""Тесты без реального AliExpress: кэш, паузы, 429, отмена."""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from request_guard import RequestGuard, SearchCancelled, is_throttle_message
from provider import LiveProvider
from optimizer import run_search


class StubSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def call_tool(self, name, args):
        self.calls += 1
        return self.responses.pop(0)


def good(data):
    return SimpleNamespace(isError=False, structuredContent=data, content=[])


def bad(message):
    return SimpleNamespace(isError=True, structuredContent=None,
                           content=[SimpleNamespace(text=message)])


class GuardTests(unittest.TestCase):
    def test_throttle_markers(self):
        for marker in ("RGV587", "RGV587_ERROR", "HTTP 429", "throttling this IP", "Too Many Requests"):
            with self.subTest(marker=marker):
                self.assertTrue(is_throttle_message(marker))
        self.assertFalse(is_throttle_message("Error: no delivery methods"))

    def test_caches_success_and_avoids_second_call(self):
        with tempfile.TemporaryDirectory() as temp:
            guard = RequestGuard(Path(temp))
            s = StubSession([good({"items": [{"item_id": 22}], "has_more": False})])
            p = LiveProvider("Санкт-Петербург", guard=guard)
            p.session = s
            first = asyncio.run(p.search("reel"))
            self.assertEqual(first["items"][0]["item_id"], 22)
            self.assertEqual(asyncio.run(p.search("reel")), first)
            self.assertEqual(s.calls, 1)
            self.assertEqual(p.cache_hits, 1)
            other = LiveProvider("Санкт-Петербург", guard=guard)
            self.assertEqual(asyncio.run(other.search("reel")), first)
            self.assertIsNone(other.session)

    def test_429_waits_and_retries_same_query_then_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            guard = RequestGuard(Path(temp))
            session = StubSession([bad("RGV587 / HTTP 429"), good({"items": [], "has_more": False})])
            notices = []
            p = LiveProvider("СПб", guard=guard, min_interval=1,
                             throttle_pause_seconds=0.05, notify=lambda e, data: notices.append((e, data)))
            p.session = session
            # Пауза внешнего лимита min_interval=1; полная проверка занимает ~1 сек.
            self.assertEqual(asyncio.run(p.search("reel"))["items"], [])
            self.assertEqual(session.calls, 2)
            self.assertEqual(p.uncached_calls, 2)
            self.assertTrue(any(e == "log" and "HTTP 429" in info["text"] for e, info in notices))
            self.assertTrue(any(e == "pause" and "429" in info["reason"] for e, info in notices))

    def test_budget_pauses_then_continues(self):
        with tempfile.TemporaryDirectory() as temp:
            notices = []
            session = StubSession([good({"items": [], "has_more": False}), good({"items": [], "has_more": False})])
            p = LiveProvider("СПб", guard=RequestGuard(Path(temp)), budget=1,
                             min_interval=1, budget_pause_seconds=0.01,
                             notify=lambda e, info: notices.append((e, info)))
            p.session = session
            async def run():
                await p.search("one")
                await p.search("two")
            asyncio.run(run())
            self.assertEqual(session.calls, 2)
            self.assertEqual(p.uncached_calls, 2)
            self.assertTrue(any(e == "log" and "лимит" in info["text"] for e, info in notices))

    def test_stop_during_long_cooldown_immediately(self):
        async def run():
            with tempfile.TemporaryDirectory() as temp:
                guard = RequestGuard(Path(temp))
                guard.mark_blocked("429", cooldown_seconds=1000)
                stop = threading.Event()
                p = LiveProvider("СПб", guard=guard, stop_event=stop)
                task = asyncio.create_task(p.search("reel"))
                await asyncio.sleep(0.02)
                stop.set()
                with self.assertRaises(SearchCancelled):
                    await asyncio.wait_for(task, timeout=1.5)
                self.assertEqual(p.uncached_calls, 0)
        asyncio.run(run())

    def test_stop_during_inflight_mcp_call(self):
        class SlowSession:
            async def call_tool(self, name, args):
                await asyncio.sleep(30)
        async def run():
            with tempfile.TemporaryDirectory() as temp:
                stop = threading.Event()
                p = LiveProvider("СПб", guard=RequestGuard(Path(temp)), stop_event=stop)
                p.session = SlowSession()
                task = asyncio.create_task(p.search("reel"))
                await asyncio.sleep(0.05)
                stop.set()
                with self.assertRaises(SearchCancelled):
                    await asyncio.wait_for(task, timeout=2)
        asyncio.run(run())

    def test_cached_quotes_expire(self):
        with tempfile.TemporaryDirectory() as temp:
            clock = [1000.]
            guard = RequestGuard(Path(temp), now=lambda: clock[0])
            args = {"city": "СПб", "quantity": 2}
            guard.put("get_product", args, {"price": 10})
            clock[0] += 40 * 60
            self.assertEqual(guard.get("get_product", args), {"price": 10})
            clock[0] += 10 * 60
            self.assertIsNone(guard.get("get_product", args))

    def test_cancel_preserves_emitted_rows(self):
        class Fake:
            def __init__(self):
                self.stop_event = threading.Event()
            async def search(self, q, page):
                return {"items": [{"item_id": 1, "title": "Badge reel"},
                                  {"item_id": 2, "title": "Badge reel"}], "has_more": False}
            async def product(self, item_id, source_id, sku_id, quantity):
                return {"item_id": item_id, "source_id": 0, "name": "Badge reel",
                        "url": f"https://aliexpress.ru/item/{item_id}.html",
                        "variants": [{"sku_id": "B", "options": {"Quantity": "100pcs"},
                                      "price_rub": 700, "stock": 50}],
                        "selected_variant": {"sku_id": "B", "price_rub": 700},
                        "delivery": {"cheapest_rub": 50}}
        p = Fake()
        emitted = []
        def on_offer(row):
            emitted.append(row)
            p.stop_event.set()
        with self.assertRaises(SearchCancelled):
            asyncio.run(run_search(p, ["reel"], 200, 0, 1, 10, lambda s: None, on_offer=on_offer))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].item_id, "1")


if __name__ == "__main__":
    unittest.main()
