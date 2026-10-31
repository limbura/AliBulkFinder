"""Единое отображение даты и времени в часовом поясе компьютера пользователя."""
from __future__ import annotations
from datetime import datetime

LOCAL_FORMAT = "%d.%m.%Y %H:%M:%S"


def local_now() -> str:
    """Локальное системное время, совпадающее с часами Windows."""
    return datetime.now().strftime(LOCAL_FORMAT)


def local_datetime(value: str | datetime | None) -> str:
    """ISO 8601 (включая UTC/Z) → местное время; отсутствие зоны означает локальное время.

    Если источник вместо даты вернул произвольный текст, он сохраняется как есть.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime(LOCAL_FORMAT)
