"""Windows portable runtime: keep Chromium and state beside the EXE."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_folder() -> Path:
    return Path(sys.executable if is_frozen() else __file__).resolve().parent


def browser_folder() -> Path:
    return app_folder() / "browsers"


def configure_browser() -> None:
    """Use the same browser directory in GUI and the MCP subprocess."""
    if is_frozen():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_folder())


def browser_ready() -> bool:
    """Check the matching full Chromium revision used by channel='chromium'."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        return Path(pw.chromium.executable_path).is_file()


def playwright_install_command() -> list[str]:
    """Launch Playwright's bundled Node CLI, not an external python.exe."""
    from playwright._impl._driver import compute_driver_executable
    node_exe, cli_js = compute_driver_executable()
    return [node_exe, cli_js, "install", "chromium", "--no-shell"]


def ensure_browser() -> bool:
    """First-run browser installation; source runs still use setup_windows.bat."""
    if not is_frozen():
        return True
    configure_browser()
    try:
        browser_folder().mkdir(parents=True, exist_ok=True)
        if browser_ready():
            return True
    except Exception as exc:
        _show_error(f"Не удалось проверить Chromium в папке программы:\n{exc}")
        return False
    return _install_dialog()


def _show_error(message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("AliBulkFinder — установка Chromium", message, parent=root)
    root.destroy()


def _install_dialog() -> bool:
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("AliBulkFinder — первоначальная настройка")
    root.geometry("560x180")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Установка Chromium для AliBulkFinder",
              font=("Segoe UI", 13, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Браузер сохраняется рядом с программой. Python устанавливать не нужно.",
              wraplength=520).pack(anchor="w", pady=(8, 8))
    state = tk.StringVar(value="Подготовка загрузки…")
    ttk.Label(frame, textvariable=state, wraplength=520).pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate", length=515)
    bar.pack(fill="x", pady=(8, 6))
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    stop = threading.Event()
    current: dict[str, subprocess.Popen | None] = {"process": None}
    finished = {"ok": False}

    def close():
        stop.set()
        proc = current["process"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
        root.destroy()

    def start_install():
        stop.clear()
        state.set("Загрузка Chromium…")
        bar.configure(mode="indeterminate", value=0)
        bar.start(12)

        def worker():
            try:
                cmd = playwright_install_command()
                proc = subprocess.Popen(cmd, cwd=app_folder(), env=os.environ.copy(),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
                current["process"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    if stop.is_set():
                        break
                    line = line.strip()
                    if line:
                        events.put(("line", line))
                code = proc.wait()
                if stop.is_set():
                    return
                if code != 0 or not browser_ready():
                    events.put(("error", f"Установщик Chromium завершился с кодом {code}. "
                                         "Проверьте интернет, свободное место и разрешение на запись в папку программы."))
                else:
                    events.put(("done", None))
            except Exception as exc:
                if not stop.is_set():
                    events.put(("error", str(exc)))
            finally:
                current["process"] = None
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                event, value = events.get_nowait()
                if event == "line":
                    line = str(value)
                    match = re.search(r"(\d{1,3})%", line)
                    if match:
                        percent = min(100, int(match.group(1)))
                        bar.stop()
                        bar.configure(mode="determinate", maximum=100, value=percent)
                        state.set(f"Загрузка Chromium: {percent}%")
                    elif "Downloading" in line or "downloaded" in line or "Extracting" in line:
                        state.set(line[:90])
                elif event == "done":
                    finished["ok"] = True
                    root.destroy()
                    return
                elif event == "error":
                    bar.stop()
                    if messagebox.askretrycancel("Не удалось установить Chromium", str(value) +
                                                 "\n\nПовторить попытку?", parent=root):
                        start_install()
                    else:
                        root.destroy()
                        return
        except queue.Empty:
            pass
        if root.winfo_exists():
            root.after(100, poll)

    root.protocol("WM_DELETE_WINDOW", close)
    start_install()
    root.after(100, poll)
    root.mainloop()
    return finished["ok"]
