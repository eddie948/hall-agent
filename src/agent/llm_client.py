from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.capabilities.process_history import ProcessHistory
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from src.agent.capabilities import build_observability_capability, build_record_management_capability
from src.agent.history import trim_message_history
from src.agent.model_input_snapshot import capture_model_input_snapshot, model_input_snapshot_context
from src.agent.prompts import AGENT_SYSTEM_PROMPT
from src.config import settings
from src.domain.context import ActorContext, Scope
from src.records.store import RecordStore, store
from src.services.record_service import RecordService
from src.services.reminder_service import ReminderService
from src.services.table_service import TableService

logger = logging.getLogger(__name__)

try:
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.alibaba import AlibabaProvider
except ImportError:  # pragma: no cover
    OpenAIChatModel = None
    AlibabaProvider = None


class AgentUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentDeps:
    user_id: str
    chat_id: str
    chat_type: str
    message_id: str
    current_time: str
    actor: ActorContext
    record_store: RecordStore
    table_service: TableService
    record_service: RecordService
    reminder_service: ReminderService
    history_message_limit: int = 12


AGENT_USAGE_LIMITS = UsageLimits(request_limit=10, tool_calls_limit=20)
RUNTIME_TABLE_SUMMARY_LIMIT = 20


def _new_snapshot_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout=600, connect=5), event_hooks={"request": [capture_model_input_snapshot]})


class SnapshotAlibabaProvider(AlibabaProvider if AlibabaProvider is not None else object):
    def __init__(self, *, api_key: str, base_url: str) -> None:
        http_client = _new_snapshot_http_client()
        super().__init__(api_key=api_key, base_url=base_url, http_client=http_client)
        self._own_http_client = http_client
        self._http_client_factory = _new_snapshot_http_client


def build_agent_deps(
    user_id: str,
    chat_id: str,
    chat_type: str,
    message_id: str,
    current_time: str,
    record_store: RecordStore | None = None,
) -> AgentDeps:
    target_store = record_store or store
    scope = Scope.from_message(user_id, chat_id, chat_type)
    actor = ActorContext(user_id, scope, chat_type, message_id)
    reminder_service = ReminderService(target_store)
    table_service = TableService(target_store)
    table_service.ensure_fixed_table(actor)
    return AgentDeps(
        user_id=user_id,
        chat_id=scope.scope_id,
        chat_type=chat_type,
        message_id=message_id,
        current_time=current_time,
        actor=actor,
        record_store=target_store,
        table_service=table_service,
        record_service=RecordService(target_store, reminder_service),
        reminder_service=reminder_service,
    )


def _get_model() -> Any:
    if OpenAIChatModel is None or AlibabaProvider is None:
        raise AgentUnavailable("未安装 pydantic-ai OpenAI / Alibaba 模型依赖")
    if not settings.dashscope_api_key:
        raise AgentUnavailable("未配置 DASHSCOPE_API_KEY")
    provider = SnapshotAlibabaProvider(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)
    return OpenAIChatModel(settings.llm_model, provider=provider, settings={"extra_body": {"enable_thinking": settings.llm_enable_thinking}})


def build_agent() -> Agent[AgentDeps, str]:
    return Agent(
        _get_model(),
        output_type=str,
        deps_type=AgentDeps,
        instructions=AGENT_SYSTEM_PROMPT,
        capabilities=[
            build_record_management_capability(),
            build_observability_capability(),
            ProcessHistory(trim_message_history),
        ],
        end_strategy="graceful",
        tool_timeout=120,
    )


async def run_agent_loop(content: str, deps: AgentDeps, message_history: list[ModelMessage] | None = None):
    agent = build_agent()
    prompt = build_runtime_prompt(content, deps)
    try:
        with model_input_snapshot_context(deps):
            async with agent:
                return await asyncio.wait_for(
                    agent.run(prompt, deps=deps, message_history=message_history, usage_limits=AGENT_USAGE_LIMITS),
                    timeout=settings.llm_invoke_wall_timeout_sec,
                )
    except AgentUnavailable:
        raise
    except Exception as exc:
        logger.warning("智能体 loop 调用失败: %s", exc)
        raise


def build_runtime_prompt(content: str, deps: AgentDeps) -> str:
    scope_name = "当前群" if deps.actor.scope_type == "group" else "当前用户"
    return "\n".join([
        f"当前时间（{settings.scheduler_timezone}）：{deps.current_time}",
        f"当前操作者：{deps.user_id}",
        f"当前数据空间：{scope_name}（{deps.actor.scope_type}:{deps.actor.scope_id}）",
        _format_runtime_table_summaries(deps),
        _format_unfinished_reception_records(deps),
        f"用户消息：\n{content}",
    ])


def _format_runtime_table_summaries(deps: AgentDeps) -> str:
    result = deps.table_service.list_tables(deps.actor, limit=RUNTIME_TABLE_SUMMARY_LIMIT)
    if not result.get("success"):
        return "当前空间固定接待表摘要：读取失败；必要时调用 list_record_tables 检索。"
    tables = result.get("data", {}).get("tables") or []
    if not tables:
        return "当前空间固定接待表摘要：暂未读取到固定表；用户要录入结构化信息时，先调用 list_record_tables 检索。"

    lines = [
        "当前空间固定接待表摘要：",
        "这些摘要只用于定位固定表；写入、查询或修改记录前必须调用 get_record_table 读取完整 Schema。",
    ]
    for index, table in enumerate(tables, start=1):
        description = table.get("description") or "无描述"
        field_names = "、".join(table.get("field_names") or []) or "无字段"
        lines.append(
            f"{index}. {table.get('table_id')}｜{table.get('name')}｜用途：{description}｜字段摘要：{field_names}"
        )
    if len(tables) >= RUNTIME_TABLE_SUMMARY_LIMIT:
        lines.append("表摘要可能未列全；如果不能唯一定位固定表，调用 list_record_tables 按关键词继续检索。")
    return "\n".join(lines)


def _format_unfinished_reception_records(deps: AgentDeps) -> str:
    try:
        snapshot = deps.table_service.refresh_reception_statuses(deps.actor, deps.current_time)
    except Exception as exc:
        logger.warning("刷新接待状态或读取未完成记录失败: %s", exc)
        return "当前未完成接待记录快照：读取失败；需要查询时调用 list_records。"

    table = snapshot["table"]
    records = snapshot["records"]
    if not records:
        return "当前未完成接待记录快照：暂无。"

    fields = [field for field in table["schema"]["fields"] if field.get("status") == "active"]
    lines = [
        "当前未完成接待记录快照：",
        "该快照来自本轮 prompt 构建前的数据库状态，可用于快速回答当前未完成接待查询和初筛冲突；写入、修改、取消前仍需调用工具确认最新版本。",
    ]
    for index, record in enumerate(records, start=1):
        values = record.get("values", {})
        field_parts = [
            f"{field['name']}={_format_snapshot_value(values.get(field['key']))}"
            for field in fields
            if values.get(field["key"]) not in (None, "")
        ]
        lines.append(
            f"{index}. record_id={record['record_id']}｜version={record['version']}｜系统状态={record['status']}｜"
            + "；".join(field_parts)
        )
    return "\n".join(lines)


def _format_snapshot_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value)
    return str(value)


__all__ = ["AgentDeps", "AgentUnavailable", "build_agent_deps", "build_agent", "build_runtime_prompt", "run_agent_loop"]
