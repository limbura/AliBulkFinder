"""Build a Windows x64 portable ZIP (Chromium downloaded on first run)."""
from __future__ import annotations

import os
import asyncio
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.4.0"
NAME = f"AliBulkFinder_v{VERSION}_windows_x64"


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> None:
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise SystemExit("Выпуск Windows x64 нужно собирать на 64-битной Windows")
    import PyInstaller  # noqa: F401
    out = ROOT / "builds"
    out.mkdir(exist_ok=True)
    run("-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "AliBulkFinder", "--collect-all", "playwright",
        "--collect-all", "aliexpress_ru_mcp",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT), "launcher.py")
    folder = ROOT / "dist" / "AliBulkFinder"
    exe = folder / "AliBulkFinder.exe"
    if not exe.is_file():
        raise RuntimeError("PyInstaller не создал AliBulkFinder.exe")
    # Smoke test the frozen Python dependencies and Playwright's Node driver.
    subprocess.run([str(exe), "--self-test"], cwd=folder, check=True, timeout=90)
    # Check the actual MCP JSON-RPC stdio handshake of the frozen GUI-subprocess.
    async def smoke_mcp():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=str(exe), args=["--mcp-server"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=40)
                tools = await asyncio.wait_for(session.list_tools(), timeout=40)
                names = {tool.name for tool in tools.tools}
                if "search_products" not in names or "get_product" not in names:
                    raise RuntimeError("MCP-сервер не предоставил инструменты поиска товаров")
    asyncio.run(asyncio.wait_for(smoke_mcp(), timeout=60))
    shutil.copy2(ROOT / "README.md", folder / "README.md")
    archive = out / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                z.write(file, Path("AliBulkFinder") / file.relative_to(folder))
    print(f"Создано: {archive} ({archive.stat().st_size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    main()
