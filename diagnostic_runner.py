"""Запуск GUI с захватом stdout/stderr в ограниченный по размеру console.log.

Ротация невозможна через обычный `>> console.log`: оболочка держит файл
открытым и не переключается на следующий при переименовании. Поэтому
дочерний процесс запускается с PIPE, а журнал пишет этот модуль.
"""
from __future__ import annotations

from timefmt import local_now
import os
from pathlib import Path
import subprocess
import sys

from log_rotation import MAX_LOG_BYTES, BACKUP_COUNT, close_logger, create_rotating_logger

ROOT = Path(__file__).resolve().parent
CONSOLE_LOG_PATH = ROOT / "alibulkfinder_console.log"
# Даже слишком длинные строки делятся на записи, не превышающие 16 КиБ.
LINE_PART_LIMIT = 16 * 1024


def log_output(stream, logger, *, part_limit: int = LINE_PART_LIMIT):
    """Пишет весь вывод процесса порциями, включая длинные строки."""
    for line in stream:
        line = line.rstrip("\r\n")
        if not line:
            logger.info("")
            continue
        for index in range(0, len(line), part_limit):
            logger.info(line[index:index + part_limit])


def main() -> int:
    logger = create_rotating_logger("alibulkfinder.console", CONSOLE_LOG_PATH,
                                    max_bytes=MAX_LOG_BYTES, backups=BACKUP_COUNT)
    child: subprocess.Popen | None = None
    try:
        logger.info("\n=== START %s ===", local_now())
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8:replace"
        child = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "app.py")],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
        )
        assert child.stdout is not None
        # stderr из запущенного внутри app.py MCP также попадает в PIPE.
        # Переменная PYTHONIOENCODING задаёт кодировку для дочернего процесса.
        import io
        with io.TextIOWrapper(child.stdout, encoding="utf-8", errors="replace") as text_stream:
            log_output(text_stream, logger)
        return_code = child.wait()
        logger.info("=== EXIT code=%s %s ===", return_code,
                    local_now())
        return return_code
    except KeyboardInterrupt:
        logger.info("=== INTERRUPTED BY USER ===")
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 130
    except Exception:
        logger.exception("=== DIAGNOSTIC RUNNER ERROR ===")
        return 1
    finally:
        close_logger(logger)


if __name__ == "__main__":
    raise SystemExit(main())
