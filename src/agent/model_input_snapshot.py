from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from src.config import settings


_snapshot_identity: ContextVar[tuple[str, str]] = ContextVar(
    "model_input_snapshot_identity",
    default=("unknown", "unknown"),
)


def _filename_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_")
    return cleaned[:80] or "unknown"


def _filename_timestamp() -> str:
    local_now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    return local_now.strftime("%Y_%m_%d_%H_%M_%S_%f")


@contextmanager
def model_input_snapshot_context(deps: Any) -> Iterator[None]:
    """Associate all model HTTP requests in one agent run with its message."""
    token = _snapshot_identity.set(
        (
            str(getattr(deps, "chat_id", "unknown")),
            str(getattr(deps, "message_id", "unknown")),
        )
    )
    try:
        yield
    finally:
        _snapshot_identity.reset(token)


def write_model_input_snapshot(raw_input: bytes) -> Path:
    """Persist the model request as readable JSON without changing its data."""
    settings.model_input_snapshot_dir.mkdir(parents=True, exist_ok=True)
    chat_id, message_id = _snapshot_identity.get()
    now = _filename_timestamp()
    filename = (
        f"{now}_{_filename_part(chat_id)}_{_filename_part(message_id)}_"
        f"{uuid4().hex[:8]}.json"
    )
    path = settings.model_input_snapshot_dir / filename
    payload = json.loads(raw_input)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


async def capture_model_input_snapshot(request: httpx.Request) -> None:
    """HTTPX request hook used at the final boundary before DashScope."""
    if request.method != "POST" or not request.url.path.rstrip("/").endswith("/chat/completions"):
        return
    raw_input = await request.aread()
    write_model_input_snapshot(raw_input)
