from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.history import conversation_history_user_id, json_items_to_messages
from src.config import ensure_data_dirs, settings
from src.domain.context import Scope
from src.logging_config import configure_logging
from src.observability import configure_observability, instrument_fastapi
from src.records.store import store

configure_logging()
logger = logging.getLogger(__name__)
configure_observability()

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_dirs()
    store.init_db()
    try:
        from src.scheduler import init_scheduler
        init_scheduler()
    except Exception as exc:
        logger.error("调度器启动失败: %s", exc)
    try:
        from src.bot.wecom_bot import start_bot
        start_bot()
    except Exception as exc:
        logger.error("机器人启动失败: %s", exc)
    yield
    try:
        from src.scheduler import scheduler
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception as exc:
        logger.warning("调度器关闭失败: %s", exc)
    try:
        from src.bot.wecom_bot import stop_bot
        stop_bot()
    except Exception:
        pass


app = FastAPI(title="Record and Reminder Agent", version=settings.agent_version, lifespan=lifespan)
instrument_fastapi(app)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1)
    user_id: str = "local_user"
    chat_id: str = ""
    chat_type: Literal["single", "group"] = "single"
    message_id: str = ""


class AckOutboxRequest(BaseModel):
    outbox_id: str | None = None


def _check_admin(token: str | None) -> None:
    if settings.admin_init_token and token != settings.admin_init_token:
        raise HTTPException(status_code=403, detail="invalid admin token")


def _resolve_chat_id(user_id: str, chat_id: str, chat_type: str) -> str:
    resolved = chat_id.strip()
    if chat_type == "group":
        if not resolved:
            raise HTTPException(status_code=400, detail="群聊需要 chat_id")
        return resolved
    return resolved or user_id


def _history_turns(chat_id: str, user_id: str, limit: int = 50) -> list[dict[str, str]]:
    from pydantic_ai.messages import ModelRequest, ModelResponse

    messages = json_items_to_messages(store.list_conversation_message_json(chat_id, user_id, limit=limit))
    turns: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if getattr(part, "part_kind", None) == "user-prompt":
                    content = part.content
                    if isinstance(content, str) and content.strip():
                        turns.append({"role": "user", "content": content})
        elif isinstance(message, ModelResponse):
            texts = [
                part.content
                for part in message.parts
                if getattr(part, "part_kind", None) == "text" and isinstance(part.content, str)
            ]
            if texts:
                turns.append({"role": "assistant", "content": "\n".join(texts)})
    return turns


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "version": settings.agent_version,
        "enable_wecom_bot": settings.enable_wecom_bot,
        "data_dir": str(settings.data_dir),
    }


@app.post("/api/admin/send-reminders")
async def trigger_reminders(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    from src.scheduler.jobs import reminder_sender_job
    reminder_sender_job()
    return {"ok": True}


@app.get("/api/admin/wecom-chatids")
async def list_wecom_chatids(
    x_admin_token: str | None = Header(default=None),
    limit: int = 20,
    chat_type: Literal["group", "single", "all"] = "group",
) -> dict:
    _check_admin(x_admin_token)
    from src.bot.wecom_bot import list_recent_chat_observations

    normalized_chat_type = None if chat_type == "all" else chat_type
    items = list_recent_chat_observations(limit=max(1, min(limit, 100)), chat_type=normalized_chat_type)
    return {"ok": True, "items": items}


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> dict:
    user_id = payload.user_id.strip() or "local_user"
    chat_id = _resolve_chat_id(user_id, payload.chat_id, payload.chat_type)
    message_id = payload.message_id.strip() or f"local_{uuid.uuid4().hex}"
    try:
        Scope.from_message(user_id, chat_id, payload.chat_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from src.agent.brain import run_agent

    reply = await run_agent(payload.content.strip(), user_id, chat_id, payload.chat_type, message_id)
    scope = Scope.from_message(user_id, chat_id, payload.chat_type)
    return {
        "ok": True,
        "reply": reply,
        "message_id": message_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "chat_type": payload.chat_type,
        "scope_type": scope.scope_type,
        "scope_id": scope.scope_id,
    }


@app.get("/api/chat/history")
async def chat_history(
    user_id: str = "local_user",
    chat_id: str = "",
    chat_type: Literal["single", "group"] = "single",
    limit: int = 50,
) -> dict:
    user_id = user_id.strip() or "local_user"
    chat_id = _resolve_chat_id(user_id, chat_id, chat_type)
    scope = Scope.from_message(user_id, chat_id, chat_type)
    history_user_id = conversation_history_user_id(chat_type, user_id)
    turns = _history_turns(scope.scope_id, history_user_id, limit=max(1, min(limit, 200)))
    return {
        "ok": True,
        "user_id": user_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "scope_type": scope.scope_type,
        "scope_id": scope.scope_id,
        "messages": turns,
    }


@app.get("/api/local/push-outbox")
async def list_push_outbox(pending_only: bool = True, limit: int = 50) -> dict:
    items = store.list_local_push_outbox(pending_only=pending_only, limit=limit)
    return {"ok": True, "items": items, "enable_wecom_bot": settings.enable_wecom_bot}


@app.post("/api/local/push-outbox/ack")
async def ack_push_outbox(payload: AckOutboxRequest) -> dict:
    updated = store.acknowledge_local_push_outbox(payload.outbox_id)
    return {"ok": True, "acknowledged": updated}


@app.get("/")
async def local_ui() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="local UI not found")
    return FileResponse(index)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
