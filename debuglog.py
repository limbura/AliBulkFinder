"""Локальная диагностика ошибок; ротация до 5 МиБ × (1 + 3 копии)."""
from __future__ import annotations

from timefmt import local_now
import logging
from pathlib import Path
import threading

from log_rotation import MAX_LOG_BYTES, BACKUP_COUNT, create_rotating_logger

LOG_PATH = Path(__file__).resolve().parent / "alibulkfinder_debug.log"

# Одна файловая запись может поступить из разных фоновых потоков интерфейса.
_config_lock = threading.RLock()
_logger: logging.Logger | None = None
_logger_location: Path | None = None
_logger_limits: tuple[int, int] | None = None


def _get_logger() -> logging.Logger:
    global _logger, _logger_location, _logger_limits
    path = Path(LOG_PATH)
    limits = (MAX_LOG_BYTES, BACKUP_COUNT)
    with _config_lock:
        if _logger is None or _logger_location != path or _logger_limits != limits:
            _logger = create_rotating_logger("alibulkfinder.debug", path,
                                             max_bytes=limits[0], backups=limits[1])
            _logger_location = path
            _logger_limits = limits
        return _logger


def record(message: str, error: BaseException | None = None) -> None:
    """Сохраняет событие и traceback. Проблемы с логом не останавливают поиск."""
    try:
        text = f"\n[{local_now()}] {message}"
        if error is None:
            _get_logger().info(text)
        else:
            _get_logger().error(text, exc_info=(type(error), error, error.__traceback__))
    except OSError:
        pass
