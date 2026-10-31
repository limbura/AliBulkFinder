"""Тесты интерфейса; при отсутствии графического дисплея корректно пропускаются."""
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class TestGUI(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Нет графического дисплея: {exc}")
        self.app = App(self.root)
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'root'):
            self.root.after_cancel(self.app._poll_after_id)
            self.root.destroy()

    def test_query_copy_paste_select_all_and_cut(self):
        w = self.app.queries
        w.focus_set()
        self.root.update()
        previous = w.get("1.0", "end-1c")
        w.event_generate("<Control-KeyPress>", keysym="a")
        self.assertTrue(w.tag_ranges("sel"))
        w.event_generate("<Control-KeyPress>", keysym="c")
        self.assertEqual(self.root.clipboard_get(), previous)
        w.event_generate("<Control-KeyPress>", keysym="x")
        self.assertEqual(w.get("1.0", "end-1c"), "")
        w.event_generate("<Control-KeyPress>", keysym="v")
        self.assertEqual(w.get("1.0", "end-1c"), previous)

    def test_all_editable_fields_clipboard_shortcuts(self):
        for key, variable in self.app.vars.items():
            with self.subTest(field=key):
                entry = self._find_entry_by_variable(variable)
                self.assertIsNotNone(entry)
                variable.set("Тест")
                entry.focus_set()
                self.root.update()
                entry.event_generate("<Control-KeyPress>", keysym="a")
                self.assertTrue(entry.selection_present())
                self.root.clipboard_clear()
                self.root.clipboard_append("Новый текст")
                entry.event_generate("<Control-KeyPress>", keysym="v")
                self.assertEqual(entry.get(), "Новый текст")
                entry.event_generate("<Control-KeyPress>", keysym="a")
                entry.event_generate("<Control-KeyPress>", keysym="c")
                self.assertEqual(self.root.clipboard_get(), "Новый текст")
                variable.set(self.app.inputs[key])

    def test_russian_keyboard_keys_and_compact_query(self):
        w = self.app.queries
        w.tag_remove("sel", "1.0", "end")
        event = SimpleNamespace(widget=w, keysym="Cyrillic_ef", keycode=0)
        self.assertEqual(self.app._clipboard_keypress(event), "break")
        self.assertTrue(w.tag_ranges("sel"))
        self.root.geometry("1300x850+0+0")
        self.root.update()
        self.assertLessEqual(w.winfo_width(), self.root.winfo_width() * 0.52)
        self.assertGreater(self.app.tree.winfo_height(), 150)

    def test_header_progress_and_eta(self):
        self.assertIsInstance(self.app.header_progress_bar, ttk.Progressbar)
        self.assertEqual(self.app.header_progress_value.get(), 0)
        self.app.pending_search_params = self.app.params()
        self.app._new_operation(planned_calls=10)
        self.app.calls_done = 3
        self.app._update_eta()
        self.assertEqual(self.app.header_progress_value.get(), 30)
        self.assertIn("30%", self.app.header_progress_text.get())
        self.assertIn("Осталось:", self.app.header_eta.get())
        self.app._set_idle()

    def test_protection_settings_and_stop(self):
        names = ("api_budget", "min_interval_sec", "budget_pause_minutes",
                 "throttle_pause_minutes", "error_pause_minutes")
        for name in names:
            with self.subTest(name=name):
                self.assertIsNotNone(self._find_entry_by_variable(self.app.vars[name]))
        self.assertTrue(self.app.stop_btn.instate(["disabled"]))
        self.app.pending_search_params = self.app.params()
        self.app._new_operation(planned_calls=12)
        self.assertTrue(self.app.stop_btn.instate(["!disabled"]))
        self.app.stop_search()
        self.assertTrue(self.app.stop_event.is_set())
        self.app._set_idle()

    def test_pause_countdown_appears_in_status(self):
        import time
        self.app.pending_search_params = self.app.params()
        self.app._new_operation(planned_calls=12)
        self.app._on_provider_event("pause", {"reason": "Ожидание после ограничения запросов сайтом",
                                              "deadline": time.monotonic() + 10, "seconds": 10})
        self.app._update_eta()
        self.assertIn("ограничения запросов сайтом", self.app.progress.get())
        self.assertIn("осталось", self.app.progress.get())
        self.app._on_provider_event("resume", {})
        self.app._set_idle()

    def test_requested_ui_and_large_card_limit(self):
        self.app.vars["max_products"].set("250")
        self.assertEqual(self.app.params()["max_products"], 250)
        self.app.vars["max_products"].set("5000")
        self.assertEqual(self.app.params()["max_products"], 5000)
        self.assertNotIn("address", self.app.vars)
        self.assertIsInstance(self.app.panes, ttk.Panedwindow)
        texts = [str(w.cget("text")) for w in self._walk(self.root)
                 if isinstance(w, (ttk.Label, ttk.Button))]
        self.assertTrue(any('МГ «Ночная Стража»' == text for text in texts))
        self.assertFalse(any('Подтвердить корзину' == text or 'Штук/лот' == text for text in texts))
        self.assertFalse(any('Поиск → проверка SKU' in text for text in texts))
        self.assertGreaterEqual(sum(isinstance(w, ttk.Scrollbar)
                                    for w in self._walk(self.root)), 2)
        self.assertTrue(self.app.log.cget("yscrollcommand"))

    def test_skip_button_only_enabled_for_skippable_pause(self):
        import time
        self.app.pending_search_params = self.app.params()
        self.app._new_operation(planned_calls=10)
        self.app._on_provider_event("pause", {"reason": "Интервал между запросами",
             "deadline": time.monotonic() + 10, "seconds": 10, "skippable": False})
        self.assertTrue(self.app.skip_btn.instate(["disabled"]))
        self.app.skip_pause()
        self.assertFalse(self.app.skip_pause_event.is_set())
        self.app._on_provider_event("resume", {})
        self.app._on_provider_event("pause", {"reason": "Ожидание после ограничения запросов сайтом",
             "deadline": time.monotonic() + 600, "seconds": 600, "skippable": True})
        self.assertTrue(self.app.skip_btn.instate(["!disabled"]))
        self.app.skip_pause()
        self.assertTrue(self.app.skip_pause_event.is_set())
        self.assertTrue(self.app.skip_btn.instate(["disabled"]))
        self.app._on_provider_event("resume", {"skipped": True})
        self.assertTrue(self.app.skip_btn.instate(["disabled"]))
        self.app._set_idle()

    def test_zero_disables_planned_pause_in_ui_and_eta(self):
        for field, value in (("api_budget", "0"), ("budget_pause_minutes", "0")):
            previous = self.app.vars[field].get()
            self.app.vars[field].set(value)
            try:
                data = self.app.params()
                self.assertEqual(data[field], 0)
                self.app.pending_search_params = data
                self.app._new_operation(planned_calls=12)
                self.app._update_eta()  # без деления на ноль
                self.assertIn("осталось", self.app.progress.get())
                self.app._set_idle()
            finally:
                self.app.vars[field].set(previous)

    def test_protection_fields_order_and_term(self):
        labels = [w.cget("text") for w in self._walk(self.root) if isinstance(w, ttk.Label)]
        self.assertLess(labels.index("Интервал, сек."), labels.index("Вызовов до паузы"))
        self.assertLess(labels.index("Вызовов до паузы"), labels.index("Пауза по лимиту, мин."))
        self.assertIn("Лимит сайта, мин.", labels)
        self.assertFalse(any("После HTTP 429" in t for t in labels))
        self.assertTrue(any("отключает плановые паузы" in t for t in labels))

    def test_log_pane_resizes_with_sash(self):
        self.root.geometry("1300x1000+0+0")
        self.root.update()
        before = self.app.log.winfo_height()
        old = self.app.panes.sashpos(0)
        self.app.panes.sashpos(0, max(100, old - 50))
        self.root.update()
        self.assertGreater(self.app.log.winfo_height(), before)

    def _walk(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)

    def _find_entry_by_variable(self, variable):
        def walk(widget):
            if isinstance(widget, tk.Entry) and str(widget.cget("textvariable")) == str(variable):
                return widget
            for child in widget.winfo_children():
                result = walk(child)
                if result is not None:
                    return result
            return None
        return walk(self.root)


if __name__ == '__main__':
    unittest.main()
