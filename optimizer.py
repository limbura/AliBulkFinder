"""Поиск, фильтрация, расчёт. Выполняется до конца или до отмены пользователем."""
from __future__ import annotations
import asyncio
from engine import get_variants, offer_from_product, Offer
from filters import TitleFilter
from request_guard import SearchCancelled


async def run_search(provider, queries: list[str], target: int, overbuy: int,
                     pages: int, max_products: int, emit,
                     title_filter: TitleFilter | None = None,
                     max_skus: int = 2, *, on_offer=None, on_plan=None) -> list[Offer]:
    if not queries or target < 1 or not (0 <= overbuy <= 100000):
        raise ValueError("Недопустимые параметры закупки")
    if pages < 1 or max_products < 1 or not 1 <= max_skus <= 4:
        raise ValueError("Недопустимый лимит поиска")
    title_filter = title_filter or TitleFilter(mode="off")
    seen = set()
    listing_excluded = 0
    buckets = []
    for query in queries:
        bucket = []
        for page in range(1, pages + 1):
            if getattr(provider, "stop_event", None) is not None and provider.stop_event.is_set():
                raise SearchCancelled("Остановлено пользователем")
            emit(f"Поиск «{query}», страница {page}…")
            # Ошибки страницы (кроме отмены) не обрывают поиск: ожидание и повтор.
            while True:
                try:
                    data = await provider.search(query, page)
                    break
                except SearchCancelled:
                    raise
                except Exception as exc:
                    emit(f"Не удалось прочитать страницу поиска ({type(exc).__name__}: {exc}). "
                         "Пауза и повтор. Остановить можно кнопкой.")
                    if hasattr(provider, "wait_for_error"):
                        await provider.wait_for_error()
                    else:
                        # Для стороннего тестового провайдера: исключение без способа ожидания.
                        raise
            batch = data.get("items") or []
            for item in batch:
                title = str(item.get("name") or item.get("title") or item.get("item_title") or "")
                if not title_filter.check(title, preliminary=True).allowed:
                    listing_excluded += 1
                    continue
                key = str(item.get("item_id") or "")
                if key and key not in seen:
                    bucket.append(item)
                    seen.add(key)
            if not data.get("has_more") or not batch:
                break
        buckets.append(bucket)
    hits = []
    max_len = max((len(b) for b in buckets), default=0)
    for pos in range(max_len):
        for bucket in buckets:
            if pos < len(bucket):
                hits.append(bucket[pos])
    emit(f"Найдено {len(hits)} кандидатов (исключено по названию: {listing_excluded}). "
         f"Изучаю до {max_products}; до {max_skus} SKU/карточку…")
    if on_plan:
        # План приблизительный: количество вариантов и дополнительные запросы могут отличаться.
        on_plan(len(hits[:max_products]) * (1 + max_skus))
    results = []
    product_excluded = 0
    for i, item in enumerate(hits[:max_products], 1):
        if getattr(provider, "stop_event", None) is not None and provider.stop_event.is_set():
            raise SearchCancelled("Остановлено пользователем")
        item_id = int(item["item_id"])
        source_id = int(item.get("source_id") or 0)
        emit(f"Карточка {i}/{min(max_products, len(hits))}: {item_id}…")
        try:
            product = await provider.product(item_id, source_id, item.get("listing_sku_id"), 1)
        except SearchCancelled:
            raise
        except Exception as exc:
            if item.get("listing_sku_id"):
                emit(f"SKU из выдачи для {item_id} не открылся ({exc}); пробую по умолчанию…")
                try:
                    product = await provider.product(item_id, source_id, None, 1)
                except SearchCancelled:
                    raise
                except Exception as fallback_exc:
                    emit(f"Не удалось открыть {item_id}: {fallback_exc}. Пропускаю эту карточку.")
                    continue
            else:
                emit(f"Не удалось открыть {item_id}: {exc}. Пропускаю эту карточку.")
                continue
        real_title = str(product.get("name") or product.get("title") or item.get("name") or item.get("title") or "")
        decision = title_filter.check(real_title)
        if not decision.allowed:
            product_excluded += 1
            emit(f"  Пропуск по названию {item_id}: {decision.reason}")
            continue
        if not product.get("name") and real_title:
            product["name"] = real_title
        selected = get_variants(product, target, overbuy)[:max_skus]
        for variant in selected:
            quote = None
            error = ""
            lots = variant["lots"]
            if lots and lots <= 99:
                try:
                    quote = await provider.product(int(product["item_id"]), int(product.get("source_id") or 0),
                                                   str(variant["variant"]["sku_id"]), lots)
                except SearchCancelled:
                    raise
                except Exception as exc:
                    error = str(exc)
                    emit(f"  Котировка SKU {variant['variant']['sku_id']} недоступна: {error}. "
                         "Сохраняю вариант для ручной проверки.")
            row = offer_from_product(product, variant, target, quote, error)
            row.title_quality = decision.quality
            row.title_reason = decision.reason
            results.append(row)
            if on_offer:
                on_offer(row)
            emit(f"  SKU {row.sku_id}, {row.pack_size or '?'} шт./лот, статус: {row.status}")
    emit(f"Исключено по заголовку после открытия: {product_excluded}. "
         f"Подготовлено вариантов: {len(results)}")
    return results
