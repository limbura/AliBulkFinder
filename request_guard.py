"""Локальный кэш и сохраняемый cooldown после HTTP 429.

429 не обходится: перед следующим запросом выдерживается установленная пауза.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE_DIR = BASE / "cache"
CACHE_TTL = {"search_products": 6 * 3600, "get_product": 45 * 60}


class SearchCancelled(Exception):
    """Пользователь остановил фоновую операцию."""


class AliExpressRateLimited(RuntimeError):
    """Используется для совместимости с более старыми вызовами guard.check()."""


def is_throttle_message(message: str) -> bool:
    return bool(re.search(r"\bHTTP\s*429\b|\bRGV587(?:_[A-Z]+)?\b|throttl|too many requests|rate.limit", message, re.I))


class RequestGuard:
    def __init__(self, cache_dir: Path = CACHE_DIR, *, now=time.time):
        self.cache_dir = Path(cache_dir)
        self.now = now

    def _key(self, name: str, arguments: dict) -> Path:
        payload = json.dumps([name, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return self.cache_dir / (hashlib.sha256(payload.encode("utf-8")).hexdigest() + ".json")

    def get(self, name: str, arguments: dict) -> dict | None:
        path = self._key(name, arguments)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            age = self.now() - float(entry["timestamp"])
            if age < 0 or age >= CACHE_TTL.get(name, 0):
                return None
            if isinstance(entry["response"], dict):
                return entry["response"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, name: str, arguments: dict, response: dict):
        if name not in CACHE_TTL:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._key(name, arguments)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"timestamp": self.now(), "response": response}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def clear(self) -> int:
        # Сброс ответов не сбрасывает сохранённую паузу после HTTP 429.
        number = 0
        if self.cache_dir.exists():
            for path in self.cache_dir.glob("*.json"):
                if re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                    path.unlink()
                    number += 1
        return number

    def blocked_until(self) -> float:
        try:
            state = json.loads((self.cache_dir / "blocked_until.json").read_text(encoding="utf-8"))
            return float(state["until"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def check(self):
        until = self.blocked_until()
        if until > self.now():
            stamp = datetime.fromtimestamp(until).strftime("%d.%m.%Y %H:%M:%S")
            raise AliExpressRateLimited(f"Ожидание после ограничения запросов сайтом до {stamp}")

    def mark_blocked(self, detail: str = "", *, cooldown_seconds: float = 600) -> float:
        """Сохраняет время ожидания для повторного запуска программы; возвращает timestamp."""
        if cooldown_seconds <= 0:
            raise ValueError("Ожидание после ограничения запросов сайтом должно быть положительным")
        until = max(self.blocked_until(), self.now() + cooldown_seconds)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "blocked_until.json").write_text(
            json.dumps({"until": until, "reason": detail[:250]}, ensure_ascii=False), encoding="utf-8"
        )
        return until

    def clear_blocked(self):
        """Удаляет только локальный защитный таймер после явного выбора пользователя.

        Не сбрасывает лимит на стороне AliExpress и не отменяет интервал запросов.
        """
        try:
            (self.cache_dir / "blocked_until.json").unlink()
        except FileNotFoundError:
            pass
