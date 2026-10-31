"""MCP адаптер для aliexpress.ru: кэш, паузы вместо остановки, отмена.

Не обходит антибот-механизмы, не покупает и не оформляет заказы.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import threading
import time
from typing import Any, Callable

from debuglog import record
from request_guard import RequestGuard, SearchCancelled, is_throttle_message


class LiveProvider:
    def __init__(self, city: str, *, budget: int = 18, min_interval: float = 4.0,
                 budget_pause_seconds: float = 600.0, throttle_pause_seconds: float = 600.0,
                 guard: RequestGuard | None = None, stop_event: threading.Event | None = None,
                 notify: Callable[[str, dict], None] | None = None,
                 error_pause_seconds: float = 120.0):
        if budget < 1 or min_interval < 1 or budget_pause_seconds <= 0 or throttle_pause_seconds <= 0:
            raise ValueError("Лимит >= 1; интервал >= 1 сек.; длительности пауз > 0")
        self.city = city
        self.budget = budget
        self.min_interval = min_interval
        self.budget_pause_seconds = budget_pause_seconds
        self.throttle_pause_seconds = throttle_pause_seconds
        self.error_pause_seconds = error_pause_seconds
        self.guard = guard or RequestGuard()
        self.stop_event = stop_event or threading.Event()
        self.notify = notify or (lambda event, data: None)
        self.stack = None
        self.session = None
        self.uncached_calls = 0
        self.cache_hits = 0
        self.calls_in_batch = 0
        self.last_request_started = 0.0
        self.stop_reason = ""

    def _notify(self, event: str, **info):
        self.notify(event, info)

    def _check_stop(self):
        if self.stop_event.is_set():
            self.stop_reason = "Остановлено пользователем"
            raise SearchCancelled(self.stop_reason)

    async def _wait(self, seconds: float, reason: str, *, deadline: float | None = None):
        """Ожидание с возможностью отмены за ~0.5 сек. Обновление GUI отдельно от цикла."""
        self._check_stop()
        until_mono = time.monotonic() + max(0.0, seconds)
        self._notify("pause", seconds=seconds, reason=reason, deadline=until_mono)
        try:
            while True:
                self._check_stop()
                left = until_mono - time.monotonic()
                if left <= 0:
                    break
                await asyncio.sleep(min(0.5, left))
        finally:
            self._notify("resume", reason=reason)

    async def _honor_cooldown(self):
        until = self.guard.blocked_until()
        seconds = max(0.0, until - self.guard.now())
        if seconds > 0:
            await self._wait(seconds, "Защитная пауза после HTTP 429")

    async def _respect_interval(self):
        remaining = self.last_request_started + self.min_interval - time.monotonic()
        if remaining > 0:
            await self._wait(remaining, "Интервал между запросами")

    async def __aenter__(self):
        return self

    async def _connect(self):
        self._check_stop()
        if self.session is not None:
            return
        from contextlib import AsyncExitStack
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self.stack = AsyncExitStack()
        try:
            env = os.environ.copy()
            env["AE_CITY"] = self.city
            env["AE_MIN_INTERVAL"] = str(max(self.min_interval, float(env.get("AE_MIN_INTERVAL", "1.0"))))
            params = StdioServerParameters(command=sys.executable,
                                          args=["-m", "aliexpress_ru_mcp.server"], env=env)
            read_stream, write_stream = await self.stack.enter_async_context(stdio_client(params))
            self.session = await self.stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self.session.initialize()
            self._check_stop()
        except BaseException as exc:
            if not isinstance(exc, SearchCancelled):
                record("Не удалось инициализировать MCP", exc)
            await self.stack.aclose()
            self.stack = None
            self.session = None
            raise

    async def __aexit__(self, *args):
        if self.stack:
            await self.stack.aclose()
            self.stack = None
            self.session = None

    async def _call_tool_cancellable(self, name, args):
        task = asyncio.create_task(self.session.call_tool(name, args))
        try:
            while True:
                self._check_stop()
                done, _ = await asyncio.wait({task}, timeout=0.5)
                if done:
                    return await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @staticmethod
    def _extract_payload(result, name: str) -> dict[str, Any]:
        if getattr(result, "isError", False):
            message = " ".join(getattr(p, "text", "") for p in result.content)
            raise RuntimeError(message or f"Ошибка MCP {name}")
        payload = getattr(result, "structuredContent", None)
        if isinstance(payload, dict):
            if "error" in payload:
                raise RuntimeError(str(payload["error"]))
            return payload
        for part in getattr(result, "content", []):
            txt = getattr(part, "text", "")
            if txt:
                try:
                    parsed = json.loads(txt)
                except json.JSONDecodeError as exc:
                    hint = f"Ответ MCP {name} не является JSON; длина={len(txt)}, первые 350 символов: {txt[:350]!r}"
                    raise RuntimeError(hint) from exc
                if isinstance(parsed, dict):
                    if "error" in parsed:
                        raise RuntimeError(str(parsed["error"]))
                    return parsed
        raise RuntimeError(f"Пустой или неподдерживаемый ответ MCP {name}")

    async def _call(self, name: str, args: dict, *, force_refresh: bool = False) -> dict[str, Any]:
        self._check_stop()
        if not force_refresh:
            cached = self.guard.get(name, args)
            if cached is not None:
                self.cache_hits += 1
                self._notify("completed", cached=True)
                return cached
        while True:  # 429 и лимит — паузы, НЕ завершение поиска.
            self._check_stop()
            await self._honor_cooldown()
            if self.calls_in_batch >= self.budget:
                self._notify("log", text=f"Достигнут лимит {self.budget} запросов. "
                                          f"Пауза на {self.budget_pause_seconds / 60:g} мин; затем продолжаю поиск.")
                await self._wait(self.budget_pause_seconds, "Плановая пауза по лимиту запросов")
                self.calls_in_batch = 0
            await self._respect_interval()
            await self._connect()
            self._check_stop()
            self.calls_in_batch += 1
            self.uncached_calls += 1
            self.last_request_started = time.monotonic()
            self._notify("request", count=self.uncached_calls, batch=self.calls_in_batch)
            try:
                result = await self._call_tool_cancellable(name, args)
                data = self._extract_payload(result, name)
            except SearchCancelled:
                raise
            except Exception as exc:
                detail = str(exc)
                if is_throttle_message(detail):
                    self.calls_in_batch = 0  # после 429 начинаем новую серию после паузы
                    until = self.guard.mark_blocked(detail, cooldown_seconds=self.throttle_pause_seconds)
                    left = max(0, until - self.guard.now())
                    msg = (f"AliExpress вернул HTTP 429 / RGV587. Ожидаю {left / 60:.1f} мин "
                           f"(не ранее {time.strftime('%H:%M:%S', time.localtime(until))}), затем повторю запрос.")
                    record(msg + " " + detail[:350])
                    self._notify("log", text=msg)
                    continue
                record(f"Ошибка вызова MCP {name}: {detail[:500]}", exc)
                raise
            self.guard.put(name, args, data)
            self._notify("completed", cached=False)
            return data

    async def wait_for_error(self):
        await self._wait(self.error_pause_seconds, "Повтор после ошибки сервера / сети")

    async def search(self, query: str, page: int = 1) -> dict:
        return await self._call("search_products", {"query": query, "page": page, "sort": "relevance", "city": self.city, "limit": 20})

    async def product(self, item_id: int, source_id: int = 0, sku_id: str | None = None,
                      quantity: int = 1, *, force_refresh: bool = False) -> dict:
        if not 1 <= quantity <= 99:
            raise ValueError("MCP поддерживает только 1–99 лотов одного SKU")
        args = {"item_id": item_id, "source_id": source_id, "quantity": quantity,
                "city": self.city, "include_description": False}
        if sku_id:
            args["sku_id"] = sku_id
        return await self._call("get_product", args, force_refresh=force_refresh)
