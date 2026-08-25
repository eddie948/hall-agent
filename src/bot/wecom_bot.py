from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Optional, Tuple

from src.config import settings

logger = logging.getLogger(__name__)


class _WecomAibotSdkLogBridge:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self._log = log or logging.getLogger("aibot.sdk")

    @staticmethod
    def _fmt(message: str, args: Tuple[Any, ...]) -> str:
        return f"{message} {' '.join(str(a) for a in args)}" if args else message

    def debug(self, message: str, *args: Any) -> None:
        self._log.debug("%s", self._fmt(message, args))

    def info(self, message: str, *args: Any) -> None:
        self._log.debug("%s", self._fmt(message, args))

    def warn(self, message: str, *args: Any) -> None:
        self._log.warning("%s", self._fmt(message, args))

    def error(self, message: str, *args: Any) -> None:
        self._log.error("%s", self._fmt(message, args))


_bot_instance = None
_bot_loop = None
_bot_thread = None
_bot_stop_flag = False
_bot_loop_ready = threading.Event()
_recent_chat_observations: deque[dict[str, Any]] = deque(maxlen=100)
_recent_chat_lock = threading.Lock()


def _run_push_attempts(async_push_fn, op_name: str) -> bool:
    for i in range(3):
        if i:
            time.sleep(6)
        loop = _bot_loop
        if loop is None:
            continue
        future = asyncio.run_coroutine_threadsafe(async_push_fn(), loop)
        try:
            if future.result(timeout=20):
                return True
        except Exception as exc:
            logger.warning("%s 第 %d 次失败: %s", op_name, i + 1, exc)
    return False


def _parse_scope_ref(value: str, default_scope_type: str | None = None) -> tuple[str, str] | None:
    value = value.strip()
    if not value:
        return None
    if ":" not in value:
        return (default_scope_type, value) if default_scope_type in {"user", "group"} else None
    scope_type, scope_id = value.split(":", 1)
    scope_type = scope_type.strip()
    scope_id = scope_id.strip()
    if scope_type not in {"user", "group"} or not scope_id:
        return None
    return scope_type, scope_id


def _targets_from_mapping(
    config_name: str,
    config_value: str,
    source_scope_type: str,
    source_scope_id: str,
    legacy_group_targets: bool = False,
) -> list[tuple[str, str]]:
    if not config_value.strip():
        return []
    try:
        mapping = json.loads(config_value)
    except json.JSONDecodeError as exc:
        logger.warning("%s 不是合法 JSON: %s", config_name, exc)
        return []
    if not isinstance(mapping, dict):
        logger.warning("%s 必须是 JSON object", config_name)
        return []

    source_key = source_scope_id if legacy_group_targets else f"{source_scope_type}:{source_scope_id}"
    raw_targets = mapping.get(source_key, [])
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    if not isinstance(raw_targets, list):
        logger.warning("%s[%s] 必须是字符串或数组", config_name, source_key)
        return []

    default_target_type = "group" if legacy_group_targets else None
    targets: list[tuple[str, str]] = []
    for target in raw_targets:
        if not isinstance(target, str):
            continue
        parsed = _parse_scope_ref(target, default_target_type)
        if parsed:
            targets.append(parsed)
    return targets


def _mirror_scopes_for(scope_type: str, scope_id: str) -> list[tuple[str, str]]:
    targets = _targets_from_mapping(
        "WECOM_MIRROR_SCOPES",
        settings.wecom_mirror_scopes,
        scope_type,
        scope_id,
    )
    if scope_type == "group":
        targets.extend(_targets_from_mapping(
            "WECOM_MIRROR_GROUPS",
            settings.wecom_mirror_groups,
            scope_type,
            scope_id,
            legacy_group_targets=True,
        ))

    result: list[tuple[str, str]] = []
    seen = {(scope_type, scope_id)}
    for target_scope_type, target_scope_id in targets:
        key = (target_scope_type, target_scope_id)
        if key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _record_chat_observation(chat_type: str, chat_id: str, user_id: str, message_id: str, content: str) -> None:
    observation = {
        "chat_type": chat_type,
        "chat_id": chat_id,
        "user_id": user_id,
        "message_id": message_id,
        "content": content,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with _recent_chat_lock:
        _recent_chat_observations.appendleft(observation)


def list_recent_chat_observations(limit: int = 20, chat_type: str | None = "group") -> list[dict[str, Any]]:
    with _recent_chat_lock:
        items = list(_recent_chat_observations)
    if chat_type:
        items = [item for item in items if item.get("chat_type") == chat_type]
    return items[: max(1, min(limit, 100))]


def push_message_to_scope(scope_type: str, scope_id: str, content: str) -> bool:
    if scope_type not in {"user", "group"} or not scope_id:
        return False
    mirror_scopes = _mirror_scopes_for(scope_type, scope_id)
    if not settings.enable_wecom_bot:
        from src.records.store import store

        store.add_local_push_outbox(scope_type, scope_id, content)
        for mirror_scope_type, mirror_scope_id in mirror_scopes:
            store.add_local_push_outbox(mirror_scope_type, mirror_scope_id, content)
        logger.info("本地模式：提醒本应推送 scope=%s/%s", scope_type, scope_id)
        return True
    if not _bot_loop_ready.wait(timeout=30):
        return False
    if _bot_instance is None or _bot_loop is None:
        return False

    async def _send_markdown(target_scope_type: str, target_scope_id: str) -> bool:
        try:
            await _bot_instance.send_message(
                chatid=target_scope_id,
                body={
                    "chat_type": 1 if target_scope_type == "user" else 2,
                    "msgtype": "markdown",
                    "markdown": {"content": content},
                },
            )
            return True
        except Exception as exc:
            logger.error("主动推送失败: %s", exc)
            return False

    ok = _run_push_attempts(lambda: _send_markdown(scope_type, scope_id), "主动推送消息")
    if not ok:
        return False
    for mirror_scope_type, mirror_scope_id in mirror_scopes:
        mirror_ok = _run_push_attempts(
            lambda mirror_scope_type=mirror_scope_type, mirror_scope_id=mirror_scope_id: _send_markdown(mirror_scope_type, mirror_scope_id),
            "同步推送消息",
        )
        if not mirror_ok:
            logger.warning("同步推送失败: source=%s:%s mirror=%s:%s", scope_type, scope_id, mirror_scope_type, mirror_scope_id)
    return True


def _make_ws_client():
    from aibot import WSClient, WSClientOptions, generate_req_id

    ws_client = WSClient(
        WSClientOptions(
            bot_id=settings.wecom_bot_id,
            secret=settings.wecom_bot_secret,
            logger=_WecomAibotSdkLogBridge(),
            max_reconnect_attempts=-1,
        )
    )

    @ws_client.on("disconnected")
    def on_disconnected(reason: str = "") -> None:
        global _bot_loop
        _bot_loop = None
        _bot_loop_ready.clear()
        logger.warning("企业微信 AI 机器人断开: %s", reason)

    @ws_client.on("message.text")
    async def on_text(frame):
        from src.agent.brain import run_agent

        body = frame.get("body", {})
        content = body.get("text", {}).get("content", "").strip()
        user_id = body.get("from", {}).get("userid", "")
        chat_type = body.get("chattype", "single")
        chat_id = body.get("chatid", "") or body.get("conversationid", "")
        message_id = body.get("msgid", "") or frame.get("headers", {}).get("req_id", "")
        content = re.sub(r"^(@\S+\s*)+", "", content).strip()
        if not content:
            return
        _record_chat_observation(chat_type, chat_id, user_id, message_id, content)
        if settings.wecom_debug_chat_id_log:
            logger.info("wecom chat observation: chat_type=%s chat_id=%s user_id=%s message_id=%s", chat_type, chat_id, user_id, message_id)
        stream_id = generate_req_id("stream")
        try:
            await ws_client.reply_stream(frame, stream_id, "处理中…", False)
        except Exception:
            pass
        try:
            reply = await asyncio.wait_for(run_agent(content, user_id, chat_id, chat_type, message_id), timeout=180)
        except asyncio.TimeoutError:
            reply = "抱歉，处理超时，请稍后重试。"
        except Exception as exc:
            logger.error("智能体处理失败: %s", exc)
            reply = "抱歉，处理消息时出错，请稍后重试。"
        try:
            await ws_client.reply_stream(frame, stream_id, reply, True)
        except Exception as exc:
            logger.error("流式回复失败: %s", exc)

    return ws_client


def start_bot() -> bool:
    global _bot_thread, _bot_stop_flag
    if not settings.enable_wecom_bot:
        logger.info("ENABLE_WECOM_BOT=false，跳过企业微信机器人启动")
        return False
    if not settings.wecom_bot_id or not settings.wecom_bot_secret:
        logger.warning("WECOM_BOT_ID/WECOM_BOT_SECRET 未配置，跳过机器人启动")
        return False
    try:
        import aibot  # noqa: F401
    except ImportError:
        logger.error("wecom-aibot-python-sdk 未安装")
        return False
    _bot_stop_flag = False

    def _run():
        global _bot_instance, _bot_loop
        retry_delay = 5
        while not _bot_stop_flag:
            try:
                client = _make_ws_client()
                _bot_instance = client

                @client.on("authenticated")
                def _capture_loop():
                    global _bot_loop
                    _bot_loop = asyncio.get_running_loop()
                    _bot_loop_ready.set()

                client.run()
            except Exception as exc:
                if _bot_stop_flag:
                    break
                logger.error("机器人异常退出: %s", exc)
            _bot_loop_ready.clear()
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

    _bot_thread = threading.Thread(target=_run, daemon=True, name="record-reminder-wecom-aibot")
    _bot_thread.start()
    return True


def stop_bot() -> None:
    global _bot_instance, _bot_loop, _bot_stop_flag
    _bot_stop_flag = True
    _bot_loop_ready.clear()
    client = _bot_instance
    _bot_instance = None
    _bot_loop = None
    if client is not None:
        try:
            client.stop()
        except Exception as exc:
            logger.warning("停止机器人异常: %s", exc)
