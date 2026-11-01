"""Single entrypoint for GUI and MCP server in a frozen Windows build."""
from __future__ import annotations

import multiprocessing
import sys

from portable import configure_browser, ensure_browser, is_frozen


def _restore_mcp_stdio() -> None:
    """PyInstaller --windowed sets sys.std* to None even with inherited pipes.

    Restore UTF-8 Python streams from the inherited Windows STD handles so
    the MCP stdio transport can communicate with the parent GUI.
    """
    if not (is_frozen() and sys.platform == "win32"):
        return
    import ctypes
    import io
    import msvcrt
    import os
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                       wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
                                       wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    me = kernel.GetCurrentProcess()
    for attribute, number, mode in (("stdin", -10, "rb"), ("stdout", -11, "wb"), ("stderr", -12, "wb")):
        if getattr(sys, attribute) is not None:
            continue
        handle = kernel.GetStdHandle(number)
        if not handle or handle == ctypes.c_void_p(-1).value:
            if attribute == "stderr":
                sys.stderr = io.StringIO()
                continue
            raise RuntimeError(f"Не удалось получить канал MCP {attribute}")
        duplicate = wintypes.HANDLE()
        if not kernel.DuplicateHandle(me, handle, me, ctypes.byref(duplicate), 0, False, 2):
            raise OSError(ctypes.get_last_error(), f"DuplicateHandle для {attribute}")
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY))
        stream = os.fdopen(fd, mode, buffering=0)
        setattr(sys, attribute, io.TextIOWrapper(stream, encoding="utf-8", errors="replace", write_through=True))


def main() -> int:
    multiprocessing.freeze_support()
    configure_browser()
    args = sys.argv[1:]
    if args == ["--mcp-server"]:
        _restore_mcp_stdio()
        # Stdout belongs exclusively to MCP JSON-RPC, never show GUI messages here.
        from aliexpress_ru_mcp.server import main as server_main
        server_main()
        return 0
    if args == ["--self-test"]:
        from aliexpress_ru_mcp.server import main as server_main  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        from playwright._impl._driver import compute_driver_executable
        from pathlib import Path
        node, cli = compute_driver_executable()
        if not Path(node).is_file() or not Path(cli).is_file():
            return 2
        return 0
    if args == ["--diagnostic"]:
        from diagnostic_runner import main as diagnostic_main
        return diagnostic_main()
    if args and args != ["--gui"]:
        return 2
    if not ensure_browser():
        return 1
    from app import main as app_main
    app_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
