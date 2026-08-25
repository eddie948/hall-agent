from __future__ import annotations

import logging

from src.agent.history import (
    conversation_history_user_id,
    json_items_to_messages,
    messages_to_json_items,
    replace_first_user_prompt_content,
)
from src.agent.llm_client import AgentUnavailable, build_agent_deps, run_agent_loop
from src.agent.models import current_time_local
from src.domain.context import Scope
from src.records.store import store

logger = logging.getLogger(__name__)
AGENT_FAILURE_MESSAGE = "抱歉，智能体处理失败，请稍后重试或联系管理员查看日志。"


async def run_agent(content: str, user_id: str, chat_id: str, chat_type: str, message_id: str = "") -> str:
    store.init_db()
    cached = store.get_message_result(message_id)
    if cached:
        return cached

    scope = Scope.from_message(user_id, chat_id, chat_type)
    history_user_id = conversation_history_user_id(chat_type, user_id)
    store.mark_conversation_reachable(scope.scope_type, scope.scope_id)
    deps = build_agent_deps(user_id, chat_id, chat_type, message_id, current_time_local(), store)
    try:
        history_json = store.list_conversation_message_json(scope.scope_id, history_user_id)
        result = await run_agent_loop(content, deps, message_history=json_items_to_messages(history_json))
        reply = str(result.output)
        saved_content = f"[{user_id}] {content}" if chat_type == "group" else content
        store.save_conversation_message_json(
            chat_id=scope.scope_id,
            user_id=history_user_id,
            conversation_id=result.conversation_id,
            messages_json=messages_to_json_items(replace_first_user_prompt_content(result.new_messages(), saved_content)),
            source_message_id=message_id,
        )
    except AgentUnavailable as exc:
        logger.warning("智能体不可用: %s", exc)
        reply = AGENT_FAILURE_MESSAGE
    except Exception as exc:
        logger.warning("智能体处理失败: %s", exc)
        reply = AGENT_FAILURE_MESSAGE
    store.save_message_result(message_id, scope.scope_id, user_id, reply)
    return reply
