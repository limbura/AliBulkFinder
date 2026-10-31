"""Расчёт закупок. Без сетевых зависимостей: модуль можно тестировать офлайн."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

# Только явные обозначения количества, чтобы не принять «20 мм» за 20 штук.
_COUNT_RE = re.compile(
    r"(?<!\d)(\d{1,5})\s*(?:шт(?:ук|уки|уках|\.)?|pcs?\b|pieces?\b|units?\b|个|pack\b|пак(?:ет|ов)?\b)",
    re.IGNORECASE,
)
_PACK_OF = re.compile(r"\b(?:pack|set|лот|набор|упаковка)\s*(?:of|из|по|:)?\s*(\d{1,5})\b", re.I)
_ONLY_NUM = re.compile(r"^\s*(\d{1,5})\s*$")
_Q_KEYS = ("quantity", "count", "pcs", "piece", "pieces", "шт", "колич", "комплект", "набор", "упаков", "lot", "pack")


@dataclass
class Offer:
    item_id: str
    source_id: int
    sku_id: str
    name: str
    url: str
    options: str
    pack_size: int | None
    pack_basis: str
    needed_lots: int | None
    actual_pieces: int | None
    unit_price: float | None
    goods_total: float | None
    delivery: float | None
    total: float | None
    per_piece: float | None
    status: str
    note: str
    fetched_at: str = ""
    confirmed_total: float | None = None
    confirmed_at: str = ""
    title_quality: str = "подходит"
    title_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("₽", "")
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def _from_string(s: str, quantity_label: bool = False) -> int | None:
    m = _COUNT_RE.search(s) or _PACK_OF.search(s)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 10000 else None
    m = _ONLY_NUM.match(s) if quantity_label else None
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 10000 else None


def pack_size_for_variant(variant: dict, lot: dict | None, product_name: str) -> tuple[int | None, str]:
    """Возвращает только обоснованное количество. Заголовки не считаются доказательством."""
    options = variant.get("options") or {}
    if isinstance(options, dict):
        for key, val in options.items():
            k = str(key).lower()
            v = str(val)
            n = _from_string(v, quantity_label=any(q in k for q in _Q_KEYS))
            if n:
                return n, "вариант SKU"
    elif options:
        n = _from_string(str(options))
        if n:
            return n, "вариант SKU"
    if lot and lot.get("pieces_per_lot"):
        try:
            n = int(lot["pieces_per_lot"])
            if 1 <= n <= 10000:
                return n, "лот в карточке"
        except (ValueError, TypeError):
            pass
    # Если в названии прямо говорится о пачке, но нет данных SKU,
    # запрещаем делать вид, что это единичный товар.
    guess = _from_string(product_name)
    if guess and guess > 1:
        return None, f"в названии указано {guess} шт., нужен осмотр карточки"
    return 1, "предположительно 1 шт. — проверить"


def get_variants(product: dict, target: int, overbuy: int) -> list[dict]:
    lot = product.get("lot") if isinstance(product.get("lot"), dict) else None
    variants = product.get("variants") or [product.get("selected_variant") or {}]
    data = []
    for variant in variants:
        price = to_number(variant.get("price_rub"))
        if price is None or price == 0:
            continue
        pack, basis = pack_size_for_variant(variant, lot, product.get("name") or "")
        amount = math.ceil(target / pack) if pack else None
        total = amount * pack if amount and pack else None
        if total is not None and total > target + overbuy:
            continue
        stock = variant.get("stock")
        try:
            if stock is not None and int(stock) >= 0 and amount is not None and amount > int(stock):
                # Некоторые продавцы возвращают низкий остаток, не показываем невозможную закупку.
                continue
        except (ValueError, TypeError):
            pass
        data.append({"variant": variant, "price": price, "pack": pack, "basis": basis,
                     "lots": amount, "pieces": total, "rough_per_piece": price / pack if pack else float("inf")})
    # На отдельную карточку допускаем до 4 проверок, чтобы ловить пачки разных размеров.
    data.sort(key=lambda x: x["rough_per_piece"])
    return data[:4]


def choose_coupon_goods(quote: dict, price: float, lots: int) -> float:
    coupons = quote.get("with_coupons")
    if isinstance(coupons, dict) and int(coupons.get("quantity") or 0) == lots:
        total = to_number(coupons.get("total_rub"))
        if total is not None:
            return total
    return round(price * lots, 2)


def offer_from_product(product: dict, selected: dict, target: int, quote: dict | None,
                       error: str = "") -> Offer:
    variant = selected["variant"]
    lots, pieces, pack = selected["lots"], selected["pieces"], selected["pack"]
    sku_id = str(variant.get("sku_id") or "")
    price = selected["price"]
    notes = [selected["basis"]]
    shipping: float | None = None
    goods: float | None = round(price * lots, 2) if lots else None
    status = "НУЖНА ПРОВЕРКА"
    if not pack:
        notes.append("Количество штук в одном лоте неизвестно")
    elif not lots:
        notes.append("Нельзя определить число лотов")
    elif lots > 99:
        notes.append("API ограничен 99 лотами: итоговая доставка для заказа не получена")
    elif quote:
        quoted_sku = (quote.get("selected_variant") or {}).get("sku_id")
        if str(quoted_sku or "") != sku_id:
            notes.append("Сервер вернул другой SKU — расчёт доставки отвергнут")
        else:
            freshprice = to_number((quote.get("selected_variant") or {}).get("price_rub"))
            if freshprice is not None:
                price = freshprice
            goods = choose_coupon_goods(quote, price, lots)
            d = quote.get("delivery") or {}
            shipping = to_number(d.get("cheapest_rub"))
            if d.get("warning"):
                notes.append(str(d["warning"]))
                shipping = None
            if d.get("note"):
                notes.append(str(d["note"]))
            if shipping is None:
                notes.append("Доставка не определена (НЕ бесплатная)")
            elif quote.get("combo"):
                notes.append("Choice/combo: сумма может измениться при оформлении")
            elif "предположительно" in selected["basis"]:
                notes.append("Размер упаковки требует проверки")
            status = "ОЦЕНКА ПО ГОРОДУ" if shipping is not None else "ДОСТАВКА НЕИЗВЕСТНА"
    if error:
        notes.append("Ошибка проверки: " + error)
    if goods is not None and shipping is not None:
        total = round(goods + shipping, 2)
        per_piece = round(total / pieces, 4) if pieces else None
    else:
        total, per_piece = None, None
    return Offer(item_id=str(product.get("item_id") or ""), source_id=int(product.get("source_id") or 0),
                 sku_id=sku_id, name=str(product.get("name") or ""), url=str(product.get("url") or ""),
                 options="; ".join(f"{k}: {v}" for k, v in (variant.get("options") or {}).items()) if isinstance(variant.get("options"), dict) else str(variant.get("options") or ""),
                 pack_size=pack, pack_basis=selected["basis"], needed_lots=lots, actual_pieces=pieces,
                 unit_price=price, goods_total=goods, delivery=shipping, total=total,
                 per_piece=per_piece, status=status, note=" | ".join(notes),
                 fetched_at=str((quote or product).get("fetched_at") or ""))


def confirm_total(o: Offer, total: float) -> Offer:
    if total < 0 or not math.isfinite(total):
        raise ValueError("Неверная сумма")
    if not o.actual_pieces:
        raise ValueError("Сначала укажи количество штук в упаковке")
    o.confirmed_total = round(total, 2)
    o.confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    o.status = "ПРОВЕРЕНО В КОРЗИНЕ"
    o.total = o.confirmed_total
    o.per_piece = round(o.total / o.actual_pieces, 4)
    return o


def sorting_key(o: Offer) -> tuple[int, float]:
    # Подтверждённые и рассчитанные отдельно не смешиваем как равнодостоверные.
    rank = {"ПРОВЕРЕНО В КОРЗИНЕ": 0, "ОЦЕНКА ПО ГОРОДУ": 1,
            "ДОСТАВКА НЕИЗВЕСТНА": 2, "НУЖНА ПРОВЕРКА": 3}
    return rank.get(o.status, 4), o.total if o.total is not None else float("inf")
