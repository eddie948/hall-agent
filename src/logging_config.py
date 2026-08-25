from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from src.config import settings


class TimezoneFormatter(logging.Formatter):
    def __init__(self, fmt: str, timezone: str) -> None:
        super().__init__(fmt)
        self.timezone = ZoneInfo(timezone)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        value = datetime.fromtimestamp(record.created, self.timezone)
        if datefmt:
            return value.strftime(datefmt)
        return value.strftime("%Y-%m-%d %H:%M:%S,") + f"{int(record.msecs):03d}"


class DailyFileHandler(logging.Handler):
    """Write to app_YYYY-MM-DD.log and switch files at local midnight."""

    def __init__(self, directory: Path, timezone: str, retention_days: int) -> None:
        super().__init__()
        self.directory = directory
        self.timezone = ZoneInfo(timezone)
        self.retention_days = max(retention_days, 1)
        self._day = ""
        self._stream: TextIO | None = None
        self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def current_path(self) -> Path:
        day = datetime.now(self.timezone).strftime("%Y-%m-%d")
        return self.directory / f"app_{day}.log"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.now(self.timezone).strftime("%Y-%m-%d")
            if day != self._day:
                self._open_day(day)
            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()

    def _open_day(self, day: str) -> None:
        if self._stream is not None:
            self._stream.close()
        self._day = day
        path = self.directory / f"app_{day}.log"
        self._stream = path.open("a", encoding="utf-8")
        self._remove_expired_files()

    def _remove_expired_files(self) -> None:
        cutoff = datetime.now(self.timezone).date() - timedelta(days=self.retention_days - 1)
        for path in self.directory.glob("app_????-??-??.log"):
            try:
                file_day = datetime.strptime(path.stem.removeprefix("app_"), "%Y-%m-%d").date()
                if file_day < cutoff:
                    path.unlink()
            except (OSError, ValueError):
                continue


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    level = logging.DEBUG if settings.app_debug else logging.INFO
    formatter = TimezoneFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        settings.scheduler_timezone,
    )
    handlers: list[logging.Handler] = []

    file_handler = DailyFileHandler(
        settings.log_dir,
        settings.scheduler_timezone,
        settings.log_retention_days,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    if settings.log_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    # Uvicorn normally installs separate handlers. Route it through the same
    # daily file so HTTP and application events have one timeline.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers.clear()
        target.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    _configured = True
