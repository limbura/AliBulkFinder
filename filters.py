"""Проверяемые фильтры поиска и полной стоимости закупки.

Полная стоимость — это сумма всего заказа (товары, доставка, скидки), а НЕ
цена одной единицы товара. Неизвестная доставка не равна нулю.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


def split_terms(text: str) -> tuple[str, ...]:
    """Разделитель — запятая / точка с запятой / новая строка. Фразы сохраняются."""
    seen: dict[str, None] = {}
    for term in re.split(r"[,;\n]+", text):
        normalized = " ".join(term.casefold().split())
        if normalized:
            seen[normalized] = None
    return tuple(seen)


def _normalize_title(title: str) -> str:
    return " ".join((title or "").casefold().split())


@dataclass(frozen=True)
class TitleDecision:
    allowed: bool
    quality: str   # подходит / сомнительно / исключено
    reason: str


@dataclass(frozen=True)
class TitleFilter:
    required: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    mode: str = "strict"  # strict | soft | off

    @classmethod
    def from_text(cls, required: str = "", excluded: str = "", mode: str = "strict") -> "TitleFilter":
        if mode not in {"strict", "soft", "off"}:
            raise ValueError("Режим фильтра: строгий, мягкий или выключен")
        return cls(split_terms(required), split_terms(excluded), mode)

    def check(self, title: str, *, preliminary: bool = False) -> TitleDecision:
        if self.mode == "off":
            return TitleDecision(True, "подходит", "Фильтр названий отключён")
        normalized = _normalize_title(title)
        if not normalized:
            # На стадии поисковой выдачи название может отсутствовать, тогда
            # проверим реальное название на странице товара перед запросом SKU.
            return TitleDecision(preliminary or self.mode == "soft", "сомнительно", "Название отсутствует")
        hits = [x for x in self.required if x in normalized]
        bad = [x for x in self.excluded if x in normalized]
        positive = bool(hits) or not self.required
        if bad and positive:
            if hits:
                # Не выкидываем карточку «badge reel with lanyard»: она может
                # содержать подходящий вариант. Показываем предупреждение.
                return TitleDecision(True, "сомнительно", "Искомое: " + ", ".join(hits) + "; нежелательное: " + ", ".join(bad))
            return TitleDecision(False, "исключено", "Нежелательное: " + ", ".join(bad))
        if bad:
            return TitleDecision(False, "исключено", "Нежелательное: " + ", ".join(bad))
        if positive:
            return TitleDecision(True, "подходит", "Совпадение: " + ", ".join(hits) if hits else "Нет обязательных слов")
        return TitleDecision(self.mode == "soft", "сомнительно", "Нет обязательных слов в названии")


def parse_money(value: str) -> float | None:
    """Пустое значение снимает ограничение, иное должно быть суммой в рублях."""
    value = value.strip()
    if not value:
        return None
    compact = value.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
    try:
        amount = float(compact)
    except ValueError as exc:
        raise ValueError("Границы цены должны быть числами в рублях (например, 2500 или 2500,50)") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("Границы полной цены должны быть неотрицательными конечными числами")
    return round(amount, 2)


@dataclass(frozen=True)
class PriceRange:
    minimum: float | None = None
    maximum: float | None = None

    @classmethod
    def from_text(cls, minimum: str, maximum: str) -> "PriceRange":
        lo, hi = parse_money(minimum), parse_money(maximum)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("Минимальная итоговая цена не может быть выше максимальной")
        return cls(lo, hi)

    @property
    def active(self) -> bool:
        return self.minimum is not None or self.maximum is not None

    def accepts(self, total: float | None, *, allow_unknown: bool = False) -> bool:
        if total is None:
            return not self.active or allow_unknown
        return (self.minimum is None or total >= self.minimum) and (self.maximum is None or total <= self.maximum)
