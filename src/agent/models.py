from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings


def current_time_local() -> str:
    return datetime.now(ZoneInfo(settings.scheduler_timezone)).strftime("%Y-%m-%d %H:%M")
