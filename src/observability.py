from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI

from src.config import settings

logger = logging.getLogger(__name__)
_configured = False
_attempted = False


def _is_enabled() -> bool:
    if settings.logfire_enabled is not None:
        return settings.logfire_enabled
    return bool(settings.logfire_token)


def _instrument(logfire: Any, name: str, *args: Any, **kwargs: Any) -> None:
    instrument = getattr(logfire, f"instrument_{name}", None)
    if instrument is None:
        logger.debug("Logfire 集成不可用：%s", name)
        return
    try:
        instrument(*args, **kwargs)
    except TypeError:
        if not kwargs:
            raise
        instrument(*args)
    except Exception as exc:
        logger.warning("Logfire %s 自动埋点失败：%s", name, exc)


def _instrument_standard_logging(logfire: Any) -> None:
    handler_class = getattr(logfire, "LogfireLoggingHandler", None)
    if handler_class is None:
        logger.debug("Logfire 日志处理器不可用")
        return
    try:
        root_logger = logging.getLogger()
        if any(isinstance(handler, handler_class) for handler in root_logger.handlers):
            return
        handler = handler_class()
        handler.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except Exception as exc:
        logger.warning("Logfire 日志自动埋点失败：%s", exc)


def configure_observability() -> None:
    global _attempted, _configured

    if _configured or _attempted or not _is_enabled():
        return
    _attempted = True

    try:
        import logfire
    except ImportError:
        logger.warning("已启用 LOGFIRE_ENABLED，但未安装 logfire")
        return

    if settings.logfire_base_url:
        os.environ.setdefault("LOGFIRE_BASE_URL", settings.logfire_base_url)

    configure_kwargs: dict[str, Any] = {
        "send_to_logfire": True,
        "service_name": settings.logfire_service_name,
        "service_version": settings.agent_version,
        "environment": settings.logfire_environment,
        "console": settings.logfire_console,
    }
    if settings.logfire_token:
        configure_kwargs["token"] = settings.logfire_token
    if settings.logfire_base_url and hasattr(logfire, "AdvancedOptions"):
        configure_kwargs["advanced"] = logfire.AdvancedOptions(base_url=settings.logfire_base_url)

    try:
        logfire.configure(**configure_kwargs)
    except TypeError:
        configure_kwargs.pop("advanced", None)
        logfire.configure(**configure_kwargs)
    except Exception as exc:
        logger.warning("Logfire 配置已跳过：%s", exc)
        return

    _configured = True
    _instrument_standard_logging(logfire)
    _instrument(logfire, "httpx")
    _instrument(logfire, "sqlite3")
    _instrument(logfire, "pydantic", record=settings.logfire_pydantic_record)
    _instrument(logfire, "pydantic_ai")


def instrument_fastapi(app: FastAPI) -> None:
    if not _configured:
        configure_observability()
    if not _configured:
        return

    try:
        import logfire
    except ImportError:
        return

    _instrument(logfire, "fastapi", app)
