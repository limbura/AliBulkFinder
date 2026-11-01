"""AliBulkFinder — настольный поисковик закупок для aliexpress.ru.

Только поиск и оценка; ни логина, ни оформления заказов, ни платежей.
"""
from __future__ import annotations

import asyncio
import csv
import json
import math
import queue
import sys
import time
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

from engine import Offer, sorting_key, offer_from_product
from optimizer import run_search
from provider import LiveProvider
from request_guard import RequestGuard, SearchCancelled
from filters import TitleFilter, PriceRange
from debuglog import LOG_PATH, record
from timefmt import local_now

from portable import app_folder
BASE = app_folder()
SETTINGS = BASE / "settings.json"
DEFAULTS = {
    "queries": "рулетка ретрактор для бейджа\nретрактор для бейджа 100 шт\nbadge reel retractable holder\nбейдж катушка 50 шт",
    "city": "Санкт-Петербург", "target": "99", "overbuy": "0",
    "pages": "1", "max_products": "8", "api_budget": "18", "max_skus": "2",
    "min_interval_sec": "3", "budget_pause_minutes": "0",
    "throttle_pause_minutes": "15", "error_pause_minutes": "2",
    "required_terms": "рулетк, ретрактор, retractable, badge reel, катушк",
    "excluded_terms": "lanyard, шнурок, ремешок",
    "title_mode": "strict", "price_min": "", "price_max": "", "show_unpriced": "false",
}


def read_settings() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return {**DEFAULTS, **{k: str(v) for k, v in data.items() if k in DEFAULTS}}
    except (OSError, ValueError):
        return DEFAULTS.copy()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AliBulkFinder · поиск оптовых предложений AliExpress RU · v0.4.0")
        self.root.geometry("1300x850")
        self.root.minsize(1000, 650)
        self.messages: queue.Queue = queue.Queue()
        self.running = False
        self.stop_event = threading.Event()
        self.skip_pause_event = threading.Event()
        self._close_requested = False
        self.operation = ""
        self.started_at = 0.0
        self.pause_started = None
        self.paused_total = 0.0
        self.pause_deadline = 0.0
        self.pause_reason = ""
        self.pause_skippable = False
        self.calls_done = 0
        self.live_calls = 0
        self.calls_planned = 0
        self.action = "Готово к поиску"
        self.results: list[Offer] = []
        self._last_redraw = 0.0
        self._redraw_pending = False
        self.used_city = ""
        self.current_target = 99
        self.current_overbuy = 0
        self.used_filter = TitleFilter(mode="off")
        self.inputs = read_settings()
        self.vars: dict[str, tk.StringVar] = {}
        self._build_ui()
        self.root.after_idle(self._maximize_window)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_after_id = self.root.after(130, self._poll)

    def _maximize_window(self):
        """Открыть обычное развёрнутое окно с панелью задач, не kiosk fullscreen."""
        try:
            if sys.platform == "win32":
                self.root.state("zoomed")
            else:
                self.root.attributes("-zoomed", True)
        except tk.TclError:
            # Останется базовый размер, если ОС/оконный менеджер не умеет zoom.
            pass

    def _bind_clipboard_shortcuts(self, widget):
        """Обработчик до штатных Tk class-bindings, включая русскую раскладку."""
        widget.bind("<Control-KeyPress>", self._clipboard_keypress, add="+")
        return widget

    def _clipboard_keypress(self, event):
        widget = event.widget
        if not isinstance(widget, (tk.Text, tk.Entry, ttk.Entry)):
            return None
        # На Windows keycode — физический virtual-key, одинаковый для RU/EN.
        # Для X11/Tk с русской раскладкой используется кириллический keysym.
        vk = event.keycode if sys.platform == "win32" else None
        k = event.keysym.lower()
        command = None
        if vk == 65 or k in ("a", "cyrillic_ef"):
            command = "select"
        elif vk == 67 or k in ("c", "cyrillic_es"):
            command = "copy"
        elif vk == 86 or k in ("v", "cyrillic_em"):
            command = "paste"
        elif vk == 88 or k in ("x", "cyrillic_che"):
            command = "cut"
        if command is None:
            return None
        self._clipboard_action(widget, command)
        return "break"  # Исключает двойную вставку/копирование class bindings Tk.

    def _clipboard_action(self, widget, command: str):
        is_text = isinstance(widget, tk.Text)
        if command == "select":
            if is_text:
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
            return
        try:
            if is_text:
                selected = widget.get("sel.first", "sel.last")
            elif widget.selection_present():
                selected = widget.get()[int(widget.index("sel.first")):int(widget.index("sel.last"))]
            else:
                selected = None
        except tk.TclError:
            selected = None

        if command in ("copy", "cut"):
            if selected is None:
                return  # Копирование без выделения не меняет буфер обмена.
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
            self.root.update_idletasks()

        if command in ("paste", "cut"):
            if str(widget.cget("state")) != "normal":
                return
            if command == "paste":
                try:
                    pasted = self.root.clipboard_get()
                except tk.TclError:
                    return
            if is_text:
                if selected is not None:
                    first = widget.index("sel.first")
                    widget.delete("sel.first", "sel.last")
                    widget.mark_set("insert", first)
                if command == "paste":
                    widget.insert("insert", pasted)
                widget.see("insert")
            else:
                if selected is not None:
                    first = widget.index("sel.first")
                    widget.delete("sel.first", "sel.last")
                    widget.icursor(first)
                if command == "paste":
                    widget.insert("insert", pasted)

    def _build_ui(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=27)
        main = ttk.Frame(self.root, padding=9)
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 5))
        # Заголовок, крупный прогресс и команда — в одной строке.
        header.columnconfigure(0, weight=1)
        header.columnconfigure(2, weight=1)
        ttk.Label(header, text="Оптимизатор оптовых закупок AliExpress.ru",
                  font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        center = ttk.Frame(header)
        center.grid(row=0, column=1, sticky="n", padx=12)
        self.header_progress_value = tk.DoubleVar(value=0)
        self.header_progress_text = tk.StringVar(value="0%")
        self.header_eta = tk.StringVar(value="Осталось: —")
        # Всё в одной горизонтальной строке: полоса + проценты + оставшееся время.
        bar_row = ttk.Frame(center)
        bar_row.pack(anchor="center")
        style.configure("Header.Horizontal.TProgressbar", thickness=18)
        self.header_progress_bar = ttk.Progressbar(
            bar_row, style="Header.Horizontal.TProgressbar", orient="horizontal",
            mode="determinate", maximum=100, length=200,
            variable=self.header_progress_value)
        self.header_progress_bar.pack(side="left", padx=(0, 8))
        ttk.Label(bar_row, textvariable=self.header_progress_text,
                  font=("Segoe UI", 15, "bold"), width=6).pack(side="left")
        self.header_eta_label = ttk.Label(
            bar_row, textvariable=self.header_eta, font=("Segoe UI", 12, "bold"))
        self.header_eta_label.pack(side="left", padx=(10, 0))
        ttk.Label(header, text='МГ «Ночная Стража»',
                  font=("Segoe UI", 11, "bold")).grid(row=0, column=2, sticky="e")

        settings = ttk.LabelFrame(main, text="Параметры поиска", padding=7)
        settings.pack(fill="x")
        settings.columnconfigure(0, weight=1, uniform="settings_half")
        settings.columnconfigure(1, weight=1, uniform="settings_half")

        # Запросы занимают только левую половину блока настроек.
        query_area = ttk.Frame(settings)
        query_area.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(query_area, text="Поисковые запросы (один на строку)").pack(anchor="w", pady=(0, 3))
        self.queries = self._bind_clipboard_shortcuts(tk.Text(
            query_area, height=3, width=44, wrap="word", font=("Segoe UI", 10), undo=True))
        self.queries.pack(fill="both", expand=True)
        self.queries.insert("1.0", self.inputs["queries"])

        # Справа 3 плотных ряда вместо растянутых на всю ширину полей.
        details = ttk.Frame(settings)
        details.grid(row=0, column=1, sticky="new")
        details.columnconfigure(1, weight=0)
        details.columnconfigure(3, weight=1)

        def setting_entry(row, col, caption, name, width):
            ttk.Label(details, text=caption).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=4)
            v = tk.StringVar(value=self.inputs[name])
            self.vars[name] = v
            e = self._bind_clipboard_shortcuts(ttk.Entry(details, textvariable=v, width=width))
            e.grid(row=row, column=col + 1, sticky="w", padx=(0, 12 if col == 0 else 0), pady=4)
            return e

        setting_entry(0, 0, "Город доставки", "city", 17)
        setting_entry(0, 2, "Нужно штук", "target", 8)
        setting_entry(1, 0, "Страниц на запрос", "pages", 6)
        setting_entry(1, 2, "Макс. лишних штук", "overbuy", 8)
        setting_entry(2, 0, "Изучить карточек", "max_products", 7)
        setting_entry(2, 2, "SKU на карточку", "max_skus", 6)

        ttk.Label(settings, text="⚠ Если нужно >99 штук: MCP проверяет максимум 99 ЛОТОВ одного SKU за запрос. "
                  "Для упаковок по 1 шт. расчёт доставки на весь заказ может быть недоступен.",
                  foreground="#8a4c00", wraplength=1150).grid(row=1, column=0, columnspan=2,
                                                            sticky="w", pady=(4, 0))

        filters = ttk.LabelFrame(main, text="Фильтры названия и ПОЛНОЙ стоимости закупки (все товары + доставка − скидки)", padding=7)
        filters.pack(fill="x", pady=(2, 0))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)

        def filter_entry(row, col, label, name, width, stretch=True):
            ttk.Label(filters, text=label).grid(row=row, column=col, sticky="w", padx=(0, 5), pady=3)
            v = tk.StringVar(value=self.inputs[name])
            self.vars[name] = v
            e = self._bind_clipboard_shortcuts(ttk.Entry(filters, textvariable=v, width=width))
            e.grid(row=row, column=col + 1, sticky="ew" if stretch else "w", padx=(0, 10), pady=3)
            return e

        filter_entry(0, 0, "Обязательные слова/фразы (ИЛИ)", "required_terms", 24)
        filter_entry(0, 2, "Нежелательные слова/фразы", "excluded_terms", 24)
        ttk.Label(filters, text="Фильтр названий").grid(row=1, column=0, sticky="w", pady=3)
        self.mode_var = tk.StringVar(value=self.inputs["title_mode"] if self.inputs["title_mode"] in ("strict", "soft", "off") else "strict")
        modes = ttk.Frame(filters)
        modes.grid(row=1, column=1, columnspan=3, sticky="w")
        for title, value in (("Строгий", "strict"), ("Мягкий: включая сомнительные", "soft"), ("Без фильтра", "off")):
            ttk.Radiobutton(modes, text=title, value=value, variable=self.mode_var).pack(side="left", padx=(0, 16))
        filter_entry(2, 0, "Минимум за весь заказ, ₽", "price_min", 13, stretch=False)
        filter_entry(2, 2, "Максимум за весь заказ, ₽", "price_max", 13, stretch=False)
        self.show_unpriced_var = tk.BooleanVar(value=self.inputs.get("show_unpriced", "false").lower() in ("1", "true", "yes"))
        ttk.Checkbutton(filters, text="Показывать без известной итоговой цены", variable=self.show_unpriced_var,
                        command=self._redraw).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Label(filters, text="Допустима часть слова: «принт» найдёт «принтер», «термопринтер». "
                  "Слова/фразы разделяются запятыми; неизвестная доставка ≠ 0 ₽.",
                  foreground="#8a4c00", wraplength=690).grid(row=3, column=2,
                                                             columnspan=2, sticky="w", pady=(3, 0))
        for key in ("price_min", "price_max"):
            self.vars[key].trace_add("write", lambda *_: self._redraw())

        protection = ttk.LabelFrame(main, text="Защита от ограничений AliExpress — автоматические паузы", padding=6)
        protection.pack(fill="x", pady=(2, 0))
        protection_fields = ttk.Frame(protection)
        protection_fields.pack(fill="x")
        def protection_entry(caption, key, width):
            box = ttk.Frame(protection_fields)
            box.pack(side="left", padx=(0, 14))
            ttk.Label(box, text=caption).pack(side="left", padx=(0, 4))
            v = tk.StringVar(value=self.inputs[key])
            self.vars[key] = v
            self._bind_clipboard_shortcuts(ttk.Entry(box, textvariable=v, width=width)).pack(side="left")
        protection_entry("Интервал, сек.", "min_interval_sec", 5)
        protection_entry("Вызовов до паузы", "api_budget", 5)
        protection_entry("Пауза по лимиту, мин.", "budget_pause_minutes", 5)
        protection_entry("Лимит сайта, мин.", "throttle_pause_minutes", 5)
        protection_entry("После сбоя сети, мин.", "error_pause_minutes", 5)
        ttk.Label(protection, text="0 в «Вызовов до паузы» ИЛИ «Пауза по лимиту» отключает плановые паузы. "
                  "Интервал и защита при ограничении сайтом остаются.",
                  foreground="#8a4c00", wraplength=1200).pack(anchor="w", pady=(3, 0))

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=(6, 5))
        self.search_btn = ttk.Button(buttons, text="🔎  Найти и рассчитать", command=self.start_search)
        self.search_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(buttons, text="■  Остановить поиск", command=self.stop_search, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 6))
        self.skip_btn = ttk.Button(buttons, text="Пропустить паузу", command=self.skip_pause, state="disabled")
        self.skip_btn.pack(side="left", padx=(0, 9))
        ttk.Button(buttons, text="Открыть товар", command=self.open_selected).pack(side="left", padx=3)
        self.recheck_btn = ttk.Button(buttons, text="Проверить SKU", command=self.recheck_selected)
        self.recheck_btn.pack(side="left", padx=3)
        ttk.Button(buttons, text="Экспорт CSV", command=self.export_csv).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="JSON", command=self.export_json).pack(side="right", padx=3)
        ttk.Button(buttons, text="Очистить кэш", command=self.clear_cache).pack(side="right", padx=3)

        self.panes = ttk.Panedwindow(main, orient="vertical")
        self.panes.pack(fill="both", expand=True)
        frame = ttk.LabelFrame(self.panes, text="Предложения (по степени проверки и полной стоимости)", padding=5)
        self.table_frame = frame
        self.panes.add(frame, weight=5)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        table_area = ttk.Frame(frame)
        table_area.grid(row=0, column=0, sticky="nsew")
        cols = ("status", "name", "pack", "lots", "pcs", "price", "goods", "delivery", "total", "per_piece")
        self.tree = ttk.Treeview(table_area, columns=cols, show="headings", selectmode="browse", height=5)
        titles = {"status": "Статус", "name": "Название товара / вариант", "pack": "Штук/лот",
                  "lots": "Лотов", "pcs": "Всего штук", "price": "₽/лот", "goods": "Товары, ₽",
                  "delivery": "Доставка, ₽", "total": "ИТОГО, ₽", "per_piece": "₽/штуку"}
        sizes = {"status": 160, "name": 390, "pack": 85, "lots": 60, "pcs": 90, "price": 90, "goods": 95,
                 "delivery": 95, "total": 110, "per_piece": 85}
        for col in cols:
            self.tree.heading(col, text=titles[col])
            self.tree.column(col, width=sizes[col], minwidth=65, stretch=col == "name", anchor="w" if col in ("name", "status") else "e")
        scrollbar = ttk.Scrollbar(table_area, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.tag_configure("confirmed", background="#e3f5e5")
        self.tree.tag_configure("unknown", background="#fff4e2")

        self.details = tk.StringVar(value="Выбери предложение: здесь будут сведения о SKU, комплектности и причина неопределённости цены.")
        ttk.Label(frame, textvariable=self.details, wraplength=1250, justify="left").grid(
            row=1, column=0, sticky="ew", pady=(4, 3))
        self.progress = tk.StringVar(value="Готово к поиску. Для первого запуска требуется выполнить setup_windows.bat")
        ttk.Label(frame, textvariable=self.progress).grid(row=2, column=0, sticky="w")
        log_frame = ttk.LabelFrame(self.panes, text="Журнал последних действий (перетаскивай разделитель выше или ниже)", padding=4)
        self.panes.add(log_frame, weight=1)
        self.log = self._bind_clipboard_shortcuts(tk.Text(
            log_frame, height=3, bg="#f5f5f5", wrap="word", font=("Consolas", 9)))
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._write_log("Для реальных цен нужен интернет. Покупки и вход в аккаунт не выполняются.")

    def _price_range(self) -> PriceRange:
        return PriceRange.from_text(self.vars["price_min"].get(), self.vars["price_max"].get())

    def _visible_indices(self) -> list[int]:
        try:
            prices = self._price_range()
        except ValueError:
            return []
        show_unknown = self.show_unpriced_var.get()
        return [i for i, o in enumerate(self.results)
                if prices.accepts(o.total, allow_unknown=show_unknown)]

    def params(self) -> dict:
        d = {k: var.get().strip() for k, var in self.vars.items()}
        d["queries"] = self.queries.get("1.0", "end").strip()
        d["target"] = int(d["target"])
        d["overbuy"] = int(d["overbuy"])
        d["pages"] = int(d["pages"])
        d["max_products"] = int(d["max_products"])
        d["api_budget"] = int(d["api_budget"])
        d["max_skus"] = int(d["max_skus"])
        d["min_interval_sec"] = float(d["min_interval_sec"].replace(",", "."))
        for key in ("budget_pause_minutes", "throttle_pause_minutes", "error_pause_minutes"):
            d[key] = float(d[key].replace(",", "."))
        d["title_mode"] = self.mode_var.get()
        d["title_filter"] = TitleFilter.from_text(d["required_terms"], d["excluded_terms"], d["title_mode"])
        d["price_range"] = PriceRange.from_text(d["price_min"], d["price_max"])
        if not d["city"] or not d["queries"]:
            raise ValueError("Нужны город и хотя бы один поисковый запрос")
        if not 1 <= d["target"] <= 100000 or not 0 <= d["overbuy"] <= 10000:
            raise ValueError("Проверь количество товара и допустимый избыток")
        if not 1 <= d["pages"] <= 5 or not 1 <= d["max_products"] <= 5000:
            raise ValueError("Страниц: 1–5; карточек: 1–5000. Большое число увеличивает время поиска.")
        if not 0 <= d["api_budget"] <= 1000 or not 1 <= d["max_skus"] <= 4:
            raise ValueError("Вызовов до паузы: 0–1000 (0 — без плановой паузы); SKU на карточку: 1–4")
        if not (math.isfinite(d["min_interval_sec"]) and 1 <= d["min_interval_sec"] <= 3600):
            raise ValueError("Интервал между запросами: 1–3600 секунд")
        if not math.isfinite(d["budget_pause_minutes"]) or not 0 <= d["budget_pause_minutes"] <= 1440:
            raise ValueError("Пауза по лимиту: 0–1440 минут (0 — отключена)")
        if any(not math.isfinite(d[key]) or not 0 < d[key] <= 1440
               for key in ("throttle_pause_minutes", "error_pause_minutes")):
            raise ValueError("Ожидание после ограничения сайтом и сбоя сети: больше 0 и не более 1440 минут")
        return d

    def clear_cache(self):
        if self.running:
            messagebox.showinfo("Кэш", "Подожди окончания текущей операции")
            return
        if not messagebox.askyesno("Очистить кэш", "Удалить сохранённые результаты поиска и цены? Защитная пауза при ограничении запросов сайтом при этом НЕ снимается."):
            return
        count = RequestGuard().clear()
        self.progress.set(f"Удалено кэшированных ответов: {count}. Защитная пауза при ограничении сайтом не сброшена.")

    def _save_settings(self):
        data = {k: var.get() for k, var in self.vars.items()}
        data["queries"] = self.queries.get("1.0", "end").strip()
        data["title_mode"] = self.mode_var.get()
        data["show_unpriced"] = str(self.show_unpriced_var.get()).lower()
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _new_operation(self, *, planned_calls=0, operation="search"):
        self.running = True
        self.operation = operation
        self.stop_event = threading.Event()
        self.started_at = time.monotonic()
        self.paused_total = 0.0
        self.pause_started = None
        self.pause_deadline = 0.0
        self.pause_reason = ""
        self.pause_skippable = False
        self.skip_pause_event.clear()
        self.skip_btn.configure(state="disabled")
        self.calls_done = 0
        self.live_calls = 0
        self.calls_planned = planned_calls
        self.action = "Подключение к AliExpress…"
        self.header_progress_value.set(0)
        self.header_progress_text.set("0%")
        self.header_eta.set("Осталось: рассчитываю…")
        self.search_btn.configure(state="disabled")
        self.recheck_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

    def _provider_options(self, d):
        return dict(budget=d["api_budget"], min_interval=d["min_interval_sec"],
                    budget_pause_seconds=d["budget_pause_minutes"] * 60,
                    throttle_pause_seconds=d["throttle_pause_minutes"] * 60,
                    error_pause_seconds=d["error_pause_minutes"] * 60,
                    stop_event=self.stop_event, skip_pause_event=self.skip_pause_event,
                    notify=lambda event, info: self.messages.put(("provider", (event, info))))

    def skip_pause(self):
        """Запрашивает пропуск только текущей плановой/ошибочной паузы."""
        if not self.running or not self.pause_skippable or self.stop_event.is_set():
            return
        self.skip_pause_event.set()
        self.skip_btn.configure(state="disabled")
        self._write_log(f"Пользователь запросил пропуск паузы: {self.pause_reason}. "
                        "При ограничении запросов блокировка на стороне AliExpress может сохраниться.")

    def stop_search(self):
        if not self.running or self.stop_event.is_set():
            return
        self.stop_event.set()
        self.stop_btn.configure(state="disabled")
        self.action = "Останавливаю поиск по запросу пользователя…"
        self._write_log("Пользователь запросил остановку. Сохраняю уже полученные предложения.")

    def start_search(self):
        if self.running:
            return
        try:
            d = self.params()
            self._save_settings()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Параметры", str(exc))
            return
        self.pending_search_params = d
        queries = list(dict.fromkeys(x.strip() for x in d["queries"].splitlines() if x.strip()))
        self.results = []
        self.used_city = d["city"]
        self.current_target = d["target"]
        self.current_overbuy = d["overbuy"]
        self.used_filter = d["title_filter"]
        self._redraw()
        planned = len(queries) * d["pages"] + d["max_products"] * (1 + d["max_skus"])
        self._new_operation(planned_calls=planned)
        plan_status = (f"серия {d['api_budget']} вызовов → ожидание {d['budget_pause_minutes']:g} мин"
                       if d["api_budget"] > 0 and d["budget_pause_minutes"] > 0 else "плановые паузы выключены")
        self._write_log(f"Начинаю поиск, ориентировочно до {planned} операций с API/кэшем. "
                        f"{plan_status}; при ограничении запросов сайтом → "
                        f"{d['throttle_pause_minutes']:g} мин. Остановка кнопкой.")

        def work():
            provider = None
            try:
                async def go():
                    nonlocal provider
                    async with LiveProvider(d["city"], **self._provider_options(d)) as provider:
                        return await run_search(provider, queries, d["target"], d["overbuy"],
                                                d["pages"], d["max_products"],
                                                lambda txt: self.messages.put(("log", txt)),
                                                d["title_filter"], max_skus=d["max_skus"],
                                                on_offer=lambda row: self.messages.put(("offer", row)),
                                                on_plan=lambda n: self.messages.put(("plan", n)))
                asyncio.run(go())
                self.messages.put(("done", (False, provider.uncached_calls, provider.cache_hits)))
            except SearchCancelled:
                calls = provider.uncached_calls if provider else 0
                hits = provider.cache_hits if provider else 0
                self.messages.put(("done", (True, calls, hits)))
            except BaseException as exc:
                if self.stop_event.is_set():
                    self.messages.put(("done", (True, provider.uncached_calls if provider else 0,
                                                provider.cache_hits if provider else 0)))
                else:
                    record("Неожиданная ошибка фонового потока поиска", exc)
                    self.messages.put(("fail", f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _on_provider_event(self, event, info):
        if event == "log":
            self._write_log(info["text"])
            self.action = info["text"]
        elif event == "request":
            self.live_calls = info["count"]
        elif event == "completed":
            self.calls_done += 1
        elif event == "pause":
            now = time.monotonic()
            self.pause_started = now
            self.pause_deadline = info["deadline"]
            self.pause_reason = info["reason"]
            self.pause_skippable = bool(info.get("skippable", False))
            self.skip_btn.configure(state="normal" if self.pause_skippable and not self.stop_event.is_set() else "disabled")
            # Часто логировать четырёхсекундные интервалы не нужно.
            if "Интервал" not in info["reason"]:
                self._write_log(f"Пауза: {info['reason']}, на {info['seconds'] / 60:.1f} мин.")
        elif event == "resume":
            if self.pause_started is not None:
                self.paused_total += time.monotonic() - self.pause_started
            self.pause_started = None
            self.pause_deadline = 0.0
            self.pause_reason = ""
            self.pause_skippable = False
            self.skip_btn.configure(state="disabled")
            if info.get("skipped"):
                self._write_log("Текущая пауза пропущена пользователем. Продолжаю поиск.")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        mins, secs = divmod(remainder, 60)
        return f"{hours} ч {mins:02} мин" if hours else f"{mins} мин {secs:02} сек"

    def _update_eta(self):
        if not self.running:
            return
        now = time.monotonic()
        remaining = max(0, self.calls_planned - self.calls_done)
        active = max(0, now - self.started_at - self.paused_total -
                     (now - self.pause_started if self.pause_started is not None else 0))
        interval = float(self.pending_search_params["min_interval_sec"]) if hasattr(self, "pending_search_params") else 4.0
        average = max(interval, active / self.calls_done) if self.calls_done else interval + 2
        d = self.pending_search_params if hasattr(self, "pending_search_params") else self.params()
        budget = d["api_budget"]
        # Будущие плановые паузы — грубая оценка, учитывающая кэш не идеально.
        future_pauses = (max(0, (self.live_calls % budget + remaining - 1) // budget)
                         if budget > 0 and d["budget_pause_minutes"] > 0 else 0)
        estimate = remaining * average + future_pauses * d["budget_pause_minutes"] * 60
        if self.pause_started is not None:
            left = max(0, self.pause_deadline - now)
            estimate += left
            pause_msg = f" | {self.pause_reason}: ещё {self._format_duration(left)}"
        else:
            pause_msg = ""
        # План уточняется после поиска карточек, поэтому процент приблизительный.
        percentage = min(99, int(100 * self.calls_done / self.calls_planned)) if self.calls_planned > 0 else 0
        self.header_progress_value.set(percentage)
        self.header_progress_text.set(f"≈{percentage}%")
        if self.stop_event.is_set():
            self.header_eta.set("Останавливаю…")
            self.progress.set(f"Останавливаю… Обработано вызовов/кэша {self.calls_done}, новых запросов {self.live_calls}.")
        else:
            self.header_eta.set(f"Осталось: ≈ {self._format_duration(estimate)}")
            self.progress.set(f"{self.action} | Шагов ~{self.calls_done}/{self.calls_planned}; "
                              f"новых запросов {self.live_calls}; осталось ≈ {self._format_duration(estimate)}"
                              f"{pause_msg}")

    def _poll(self):
        try:
            while True:
                typ, data = self.messages.get_nowait()
                if typ == "log":
                    self._write_log(data)
                    self.action = data
                elif typ == "provider":
                    event, info = data
                    self._on_provider_event(event, info)
                elif typ == "plan":
                    self.calls_planned = self.calls_done + data
                elif typ == "offer":
                    self.results.append(data)
                    # Массовая выдача: максимум ~4 полных перерисовки/сек.
                    if time.monotonic() - self._last_redraw >= 0.25:
                        self._redraw()
                        self._redraw_pending = False
                    else:
                        self._redraw_pending = True
                elif typ == "done":
                    cancelled, live_calls, cache_hits = data
                    self._redraw()
                    self._redraw_pending = False
                    self._set_idle()
                    visible = self._visible_indices()
                    if cancelled:
                        self.header_eta.set("Поиск остановлен")
                        self.progress.set(f"Остановлено пользователем: обработано {len(self.results)} вариантов, "
                                          f"показано {len(visible)}, новых запросов {live_calls}, из кэша {cache_hits}.")
                        self._write_log("ПОИСК ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ. Частичные результаты доступны в таблице и экспорте.")
                    else:
                        self.header_progress_value.set(100)
                        self.header_progress_text.set("100%")
                        self.header_eta.set("Поиск завершён")
                        self.progress.set(f"Готово: найдено {len(self.results)}, показано {len(visible)}; "
                                          f"новых запросов {live_calls}, из кэша {cache_hits}.")
                        self._write_log("Поиск завершён. Результаты доступны в таблице.")
                elif typ == "recheck":
                    index, row = data
                    self.results[index] = row
                    self._redraw(select_index=index)
                    self._set_idle()
                    self.header_progress_value.set(100)
                    self.header_progress_text.set("100%")
                    self.header_eta.set("Проверка SKU завершена")
                    self.progress.set("Повторная оценка SKU получена; стоимость города — не итог корзины")
                elif typ == "stopped":
                    self._set_idle()
                    self.header_eta.set("Остановлено")
                    self.progress.set("Операция остановлена пользователем; текущие предложения сохранены")
                elif typ == "fail":
                    self._set_idle()
                    self.header_eta.set("Ошибка поиска")
                    self._write_log("ОШИБКА: " + data)
                    if not self._close_requested:
                        messagebox.showerror("Ошибка обращения к AliExpress", data +
                                             f"\n\nДиагностический журнал:\n{LOG_PATH}\n\nПолученные результаты сохранены.")
        except queue.Empty:
            pass
        if self._close_requested and not self.running:
            self._final_close()
            return
        if self._redraw_pending and time.monotonic() - self._last_redraw >= 0.25:
            self._redraw()
            self._redraw_pending = False
        self._update_eta()
        self._poll_after_id = self.root.after(130, self._poll)

    def _set_idle(self):
        self.running = False
        self.operation = ""
        self.search_btn.configure(state="normal")
        self.recheck_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.skip_btn.configure(state="disabled")
        self.pause_skippable = False

    def _write_log(self, text: str):
        self.log.insert("end", f"{local_now()}  {text}\n")
        self.log.see("end")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 250:
            self.log.delete("1.0", "100.0")

    @staticmethod
    def fmt(number, digits=2):
        return "—" if number is None else f"{number:,.{digits}f}".replace(",", " ")

    def _redraw(self, select_index=None):
        if not hasattr(self, "tree"):
            return
        self._last_redraw = time.monotonic()
        self.tree.delete(*self.tree.get_children())
        visible = self._visible_indices()
        try:
            price_range = self._price_range()
            unpriced = sum(o.total is None for o in self.results)
            excluded_by_price = sum(o.total is not None and not price_range.accepts(o.total) for o in self.results)
            text = f"Предложения: показано {len(visible)} из {len(self.results)}; вне диапазона цены — {excluded_by_price}; без полной суммы — {unpriced}"
        except ValueError as exc:
            text = f"Ошибка границ цены: {exc}"
        self.table_frame.configure(text=text)
        for i in sorted(visible, key=lambda j: sorting_key(self.results[j])):
            o = self.results[i]
            name = ("⚠ " if o.title_quality == "сомнительно" else "") + (o.name or "?") + ("  [" + o.options + "]" if o.options else "")
            vals = [o.status, name, o.pack_size or "?", o.needed_lots or "?", o.actual_pieces or "?",
                    self.fmt(o.unit_price), self.fmt(o.goods_total), self.fmt(o.delivery),
                    self.fmt(o.total), self.fmt(o.per_piece)]
            tag = "confirmed" if o.confirmed_total is not None else "unknown" if o.total is None else ""
            self.tree.insert("", "end", iid=str(i), values=vals, tags=(tag,))
        if select_index is not None:
            idx = str(select_index)
            if self.tree.exists(idx):
                self.tree.selection_set(idx)
                self.tree.see(idx)
                self.show_detail()

    def chosen(self) -> tuple[int, Offer] | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Выбор", "Сначала выбери строку в таблице")
            return None
        i = int(selected[0])
        return i, self.results[i]

    def show_detail(self, _event=None):
        item = self.tree.selection()
        if not item:
            return
        o = self.results[int(item[0])]
        self.details.set(f"Товар {o.item_id} · SKU {o.sku_id} · {o.url}\n"
                         f"Комплектность: {o.pack_basis}. Проверено: {o.fetched_at or 'нет даты'}. "
                         f"Фильтр названия: {o.title_quality} ({o.title_reason}). Примечания: {o.note}")

    def open_selected(self):
        x = self.chosen()
        if x and x[1].url.startswith("https://aliexpress.ru/"):
            webbrowser.open(x[1].url)

    def recheck_selected(self):
        if self.running:
            return
        x = self.chosen()
        if not x:
            return
        index, row = x
        if not row.needed_lots or row.needed_lots > 99:
            messagebox.showwarning("Лимит API", "Проверка через API возможна только для 1–99 лотов одного SKU.")
            return
        city = self.vars["city"].get().strip()
        if city != self.used_city:
            messagebox.showwarning("Изменён город", "Город изменён. Запусти поиск заново.")
            return
        try:
            d = self.params()
        except ValueError as exc:
            messagebox.showerror("Параметры", str(exc))
            return
        self.pending_search_params = d
        self._new_operation(planned_calls=1, operation="recheck")
        self.action = "Перепроверяю выбранный SKU…"
        def work():
            try:
                async def go():
                    async with LiveProvider(self.used_city, **self._provider_options(d)) as provider:
                        p = await provider.product(int(row.item_id), row.source_id, row.sku_id,
                                                   row.needed_lots, force_refresh=True)
                        variant = p.get("selected_variant") or {"sku_id": row.sku_id, "price_rub": row.unit_price}
                        sel = {"variant": variant, "price": row.unit_price, "pack": row.pack_size,
                               "basis": row.pack_basis, "lots": row.needed_lots, "pieces": row.actual_pieces}
                        refreshed = offer_from_product(p, sel, self.current_target, p)
                        refreshed.title_quality = row.title_quality
                        refreshed.title_reason = row.title_reason
                        return refreshed
                self.messages.put(("recheck", (index, asyncio.run(go()))))
            except SearchCancelled:
                self.messages.put(("stopped", None))
            except BaseException as exc:
                if self.stop_event.is_set():
                    self.messages.put(("stopped", None))
                else:
                    record("Неожиданная ошибка перепроверки SKU", exc)
                    self.messages.put(("fail", f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def export_csv(self):
        if not self.results:
            messagebox.showinfo("Экспорт", "Сначала получи результаты поиска")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="aliexpress_bulk_offers.csv",
                                            filetypes=[("CSV для Excel", "*.csv")])
        if not path:
            return
        keys = ["status", "name", "title_quality", "title_reason", "options", "pack_size", "pack_basis", "needed_lots", "actual_pieces",
                "unit_price", "goods_total", "delivery", "total", "per_piece", "note", "url", "item_id", "sku_id", "fetched_at"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            out = csv.writer(f, delimiter=";")
            out.writerow(["Город расчёта", self.used_city])
            out.writerow(["Целевое количество", self.current_target, "Допустимый избыток", self.current_overbuy])
            out.writerow(["Фильтр полной цены, ₽", self.vars["price_min"].get(), "до", self.vars["price_max"].get()])
            out.writerow(["Без известной суммы", "показывать" if self.show_unpriced_var.get() else "не показывать"])
            out.writerow([])
            out.writerow(keys)
            for o in sorted((self.results[i] for i in self._visible_indices()), key=sorting_key):
                d = o.as_dict()
                out.writerow([d.get(k) for k in keys])
        self.progress.set("Экспортировано: " + path)

    def export_json(self):
        if not self.results:
            messagebox.showinfo("Экспорт", "Сначала получи результаты поиска")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="aliexpress_bulk_offers.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        record = {"search_city": self.used_city,
                  "target_pieces": self.current_target, "max_extra_pieces": self.current_overbuy,
                  "filters": {"required_terms": self.vars["required_terms"].get(),
                              "excluded_terms": self.vars["excluded_terms"].get(),
                              "title_mode_used_in_search": self.used_filter.mode,
                              "min_full_total_rub": self.vars["price_min"].get(),
                              "max_full_total_rub": self.vars["price_max"].get(),
                              "show_unpriced": self.show_unpriced_var.get()},
                  "offers": [self.results[i].as_dict() for i in sorted(self._visible_indices(), key=lambda j: sorting_key(self.results[j]))]}
        Path(path).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.progress.set("Экспортировано: " + path)

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno("Идёт поиск", "Остановить текущую операцию и закрыть программу? "
                                      "Чтобы сохранить найденное в файл, сначала нажми «Остановить поиск» и экспортируй данные."):
                return
            self._close_requested = True
            self.stop_search()
            return
        self._final_close()

    def _final_close(self):
        try:
            self.root.after_cancel(self._poll_after_id)
        except tk.TclError:
            pass
        try:
            self._save_settings()
        except OSError:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
