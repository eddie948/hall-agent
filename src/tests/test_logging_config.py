import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.logging_config import DailyFileHandler, TimezoneFormatter


def test_daily_file_handler_writes_date_named_file(tmp_path: Path):
    handler = DailyFileHandler(tmp_path, "Asia/Shanghai", retention_days=30)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.daily-file")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("hello")
    handler.close()

    day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    assert (tmp_path / f"app_{day}.log").read_text(encoding="utf-8") == "INFO hello\n"


def test_timezone_formatter_uses_configured_timezone():
    formatter = TimezoneFormatter("%(asctime)s", "Asia/Shanghai")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    record.created = 0
    record.msecs = 0

    assert formatter.format(record) == "1970-01-01 08:00:00,000"
