from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest
from pydantic_ai.tools import RunContext

GROUP_HISTORY_USER_ID = "__group__"


def messages_to_json_items(messages: list[ModelMessage]) -> list[str]:
    return [ModelMessagesTypeAdapter.dump_json([message]).decode("utf-8") for message in messages]


def json_items_to_messages(items: list[str]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for item in items:
        messages.extend(ModelMessagesTypeAdapter.validate_json(item))
    return messages


def conversation_history_user_id(chat_type: str, user_id: str) -> str:
    return GROUP_HISTORY_USER_ID if chat_type == "group" else user_id


def replace_first_user_prompt_content(messages: list[ModelMessage], content: str) -> list[ModelMessage]:
    replaced = False
    result: list[ModelMessage] = []
    for message in messages:
        if replaced or not isinstance(message, ModelRequest):
            result.append(message)
            continue
        parts = []
        for part in message.parts:
            if not replaced and part.part_kind == "user-prompt":
                parts.append(replace(part, content=content))
                replaced = True
            else:
                parts.append(part)
        result.append(replace(message, parts=parts) if replaced else message)
    return result


def trim_message_history(ctx: RunContext[object], messages: list[ModelMessage]) -> list[ModelMessage]:
    history_limit = getattr(ctx.deps, "history_message_limit", 12)
    if len(messages) <= history_limit:
        return messages
    current_request = messages[-1:]
    history = messages[:-1]
    trimmed = history[-max(history_limit - len(current_request), 0):] + current_request
    return _drop_leading_tool_returns(trimmed)


def _drop_leading_tool_returns(messages: list[ModelMessage]) -> list[ModelMessage]:
    while messages and isinstance(messages[0], ModelRequest):
        parts = [
            part
            for part in messages[0].parts
            if part.part_kind not in ("tool-return", "retry-prompt")
        ]
        if parts:
            messages[0] = replace(messages[0], parts=parts)
            return messages
        messages = messages[1:]
    return messages
