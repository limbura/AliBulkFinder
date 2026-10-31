"""Локальная диагностика ошибок; не сохраняет cookie или HTTP-заголовки."""
from datetime import datetime
from pathlib import Path
import traceback

LOG_PATH = Path(__file__).resolve().parent / "alibulkfinder_debug.log"

def record(message: str, error: BaseException | None = None):
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}\n")
            if error is not None:
                traceback.print_exception(type(error), error, error.__traceback__, file=f)
    except OSError:
        pass  # Ошибка записи диагностики не должна останавливать поиск.
