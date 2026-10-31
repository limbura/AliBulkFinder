"""Нулевые плановые паузы, системное время и заводские настройки."""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DEFAULTS, read_settings
from provider import LiveProvider
from request_guard import RequestGuard
from timefmt import local_datetime, local_now


class Session:
    def __init__(self): self.calls = 0
    async def call_tool(self, name, args):
        self.calls += 1
        return SimpleNamespace(isError=False, structuredContent={"items": [], "has_more": False})


def test_fresh_install_defaults(self):
    self.assertEqual(DEFAULTS["target"], "99")
    self.assertEqual(DEFAULTS["min_interval_sec"], "3")
    self.assertEqual(DEFAULTS["budget_pause_minutes"], "0")
    self.assertEqual(DEFAULTS["throttle_pause_minutes"], "15")
    with tempfile.TemporaryDirectory() as folder, patch("app.SETTINGS", Path(folder) / "settings.json"):
        self.assertEqual(read_settings()["budget_pause_minutes"], "0")

    def test_existing_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "settings.json"
            dest.write_text('{"min_interval_sec":"8", "budget_pause_minutes":"15", "api_budget":"25"}', encoding="utf-8")
            with patch("app.SETTINGS", dest):
                data = read_settings()
            self.assertEqual(data["min_interval_sec"], "8")
            self.assertEqual(data["budget_pause_minutes"], "15")
            self.assertEqual(data["api_budget"], "25")

    def test_zero_budget_or_zero_pause_disables_planned_wait(self):
        async def run(budget, pause):
            with tempfile.TemporaryDirectory() as temp:
                events = []
                session = Session()
                provider = LiveProvider("СПб", budget=budget, budget_pause_seconds=pause, min_interval=1,
                                        guard=RequestGuard(Path(temp)), notify=lambda e, d: events.append((e,d)))
                provider.session = session
                await provider.search("one")
                await provider.search("two")
                self.assertEqual(session.calls, 2)
                self.assertFalse(any(e == "pause" and "Плановая" in d["reason"] for e,d in events))
        asyncio.run(run(0, 600))
        asyncio.run(run(1, 0))
        asyncio.run(run(0, 0))

    def test_negative_values_rejected(self):
        for k, minutes in ((-1, 0), (0, -1)):
            with self.subTest(k=k, minutes=minutes):
                with self.assertRaises(ValueError):
                    LiveProvider("СПб", budget=k, budget_pause_seconds=minutes)

    def test_local_time_format_and_utc_conversion(self):
        self.assertRegex(local_now(), r"^\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d$")
        # Контроль часового пояса через TZ доступен в тестовой Linux-среде.
        if not hasattr(time, "tzset"):
            self.skipTest("Смена временной зоны в тесте требует tzset")
        with patch.dict(os.environ, {"TZ": "Europe/Moscow"}):
            time.tzset()
            try:
                self.assertEqual(local_datetime("2026-10-31T21:12:21+00:00"), "01.11.2026 00:12:21")
                self.assertEqual(local_datetime("2026-10-31T21:12:21Z"), "01.11.2026 00:12:21")
                self.assertEqual(local_datetime("2026-10-31 21:12:21"), "31.10.2026 21:12:21")
            finally:
                time.tzset()

if __name__ == "__main__": unittest.main()
