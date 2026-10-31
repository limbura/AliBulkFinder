"""Общие параметры и создание журналов с ротацией (без внешних зависимостей)."""
from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Каждый активный файл и три архива занимают в сумме около 20 МиБ.
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def _shrink_preexisting_log(path: Path, max_bytes: int) -> None:
    """Ограничивает старый файл от версии без ротации ещё до первой записи.

    Оставляет конец файла (самые свежие записи), без обрывков строк и UTF-8.
    Не трогает файл нормального размера и архивы .1/.2/.3.
    """
    if max_bytes <= 0 or not path.is_file() or path.stat().st_size <= max_bytes:
        return
    with path.open("rb") as source:
        source.seek(-max_bytes, os.SEEK_END)
        tail = source.read()
    if b"\n" in tail:
        tail = tail.split(b"\n", 1)[1]
    # При огромной записи без перевода строки не оставляем повреждённый UTF-8.
    tail = tail.decode("utf-8", errors="ignore").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=".alibulk_log_trim_", delete=False) as output:
            temporary = Path(output.name)
            output.write(tail)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_rotating_logger(name: str, path: Path, *, max_bytes: int = MAX_LOG_BYTES,
                           backups: int = BACKUP_COUNT) -> logging.Logger:
    """Логгер с одним обработчиком, UTF-8 и ротацией при превышении размера.

    Вызывающий код отвечает за закрытие обработчика, если логгер более не нужен.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    _shrink_preexisting_log(path, max_bytes)
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups,
        encoding="utf-8", delay=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
