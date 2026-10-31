"""Тесты интерфейса; при отсутствии графического дисплея корректно пропускаются."""
import sys
import tkinter as tk
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
        self.app._on_provider_event("pause", {"reason": "Защитная пауза после HTTP 429",
                                              "deadline": time.monotonic() + 10, "seconds": 10})
        self.app._update_eta()
        self.assertIn("HTTP 429", self.app.progress.get())
        self.assertIn("осталось", self.app.progress.get())
        self.app._on_provider_event("resume", {})
        self.app._set_idle()

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
