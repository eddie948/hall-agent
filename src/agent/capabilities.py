from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability, Hooks

from src.agent.schemas import (
    CreateRecordInput,
    CreateRecordTableInput,
    CreateReminderInput,
    RecordListQuery,
    ReminderListQuery,
    SetReminderPolicyInput,
    TableListQuery,
    UpdateRecordInput,
    UpdateRecordTableInput,
    UpdateReminderInput,
)

logger = logging.getLogger(__name__)


def _dict(model) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


async def create_record_table(ctx: RunContext[Any], data: CreateRecordTableInput) -> dict[str, Any]:
    """在当前个人或群空间创建一张具有动态字段格式的记录表。"""
    return ctx.deps.table_service.create_table(ctx.deps.actor, _dict(data))


async def list_record_tables(ctx: RunContext[Any], query: TableListQuery) -> dict[str, Any]:
    """列出当前空间的记录表，用于定位用户要操作的表。"""
    return ctx.deps.table_service.list_tables(ctx.deps.actor, **_dict(query))


async def get_record_table(ctx: RunContext[Any], table_id: str) -> dict[str, Any]:
    """读取记录表的完整字段 Schema；解析、查询或修改动态记录前必须调用。"""
    return ctx.deps.table_service.get_table(ctx.deps.actor, table_id)


async def update_record_table(ctx: RunContext[Any], data: UpdateRecordTableInput) -> dict[str, Any]:
    """修改当前空间的一张记录表格式；群表仅创建者可以修改。"""
    payload = _dict(data)
    return ctx.deps.table_service.update_table(ctx.deps.actor, payload["table_id"], payload["expected_schema_version"], payload["operations"])


async def archive_record_table(ctx: RunContext[Any], table_id: str, expected_schema_version: int) -> dict[str, Any]:
    """归档一张记录表，保留已有记录和提醒历史。"""
    return ctx.deps.table_service.archive_table(ctx.deps.actor, table_id, expected_schema_version)


async def create_record(ctx: RunContext[Any], data: CreateRecordInput) -> dict[str, Any]:
    """按照记录表 Schema 创建或补充当前用户的记录草稿。"""
    payload = _dict(data)
    return ctx.deps.record_service.create_record(ctx.deps.actor, **payload)


async def confirm_record(ctx: RunContext[Any], record_id: str, expected_version: int) -> dict[str, Any]:
    """确认一条字段完整的待确认记录。"""
    return ctx.deps.record_service.confirm_record(ctx.deps.actor, record_id, expected_version)


async def list_records(ctx: RunContext[Any], query: RecordListQuery) -> dict[str, Any]:
    """使用结构化字段条件查询当前空间某张表的记录。"""
    return ctx.deps.record_service.list_records(ctx.deps.actor, **_dict(query))


async def get_record(ctx: RunContext[Any], record_id: str) -> dict[str, Any]:
    """读取当前空间的一条记录及其字段定义。"""
    return ctx.deps.record_service.get_record(ctx.deps.actor, record_id)


async def update_record(ctx: RunContext[Any], data: UpdateRecordInput) -> dict[str, Any]:
    """按当前 Schema 更新一条记录，并自动重算关联提醒。"""
    return ctx.deps.record_service.update_record(ctx.deps.actor, **_dict(data))


async def cancel_record(ctx: RunContext[Any], record_id: str, expected_version: int, reason: str = "") -> dict[str, Any]:
    """取消一条记录，并自动取消其所有未发送提醒。"""
    return ctx.deps.record_service.cancel_record(ctx.deps.actor, record_id, expected_version, reason)


async def get_table_reminder_policy(ctx: RunContext[Any], table_id: str) -> dict[str, Any]:
    """读取一张表为每条正式记录自动生成提醒的规则。"""
    return ctx.deps.reminder_service.get_policy(ctx.deps.actor, table_id)


async def set_table_reminder_policy(ctx: RunContext[Any], data: SetReminderPolicyInput) -> dict[str, Any]:
    """设置表级自动提醒规则，例如前一天18点和开始前1小时提醒。"""
    payload = _dict(data)
    return ctx.deps.reminder_service.set_policy(ctx.deps.actor, **payload)


async def clear_table_reminder_policy(ctx: RunContext[Any], table_id: str, expected_version: int) -> dict[str, Any]:
    """清除表级自动提醒规则并取消规则生成的未发送提醒。"""
    return ctx.deps.reminder_service.clear_policy(ctx.deps.actor, table_id, expected_version)


async def create_reminder(ctx: RunContext[Any], data: CreateReminderInput) -> dict[str, Any]:
    """创建独立提醒，或创建相对某条记录时间锚点的手工提醒。"""
    payload = _dict(data)
    return ctx.deps.reminder_service.create_reminder(ctx.deps.actor, **payload)


async def list_reminders(ctx: RunContext[Any], query: ReminderListQuery) -> dict[str, Any]:
    """查询当前空间的提醒。"""
    return ctx.deps.reminder_service.list_reminders(ctx.deps.actor, **_dict(query))


async def get_reminder(ctx: RunContext[Any], reminder_id: str) -> dict[str, Any]:
    """读取当前空间的一条提醒。"""
    return ctx.deps.reminder_service.get_reminder(ctx.deps.actor, reminder_id)


async def update_reminder(ctx: RunContext[Any], data: UpdateReminderInput) -> dict[str, Any]:
    """更新一条尚未发送的手工提醒。"""
    return ctx.deps.reminder_service.update_reminder(ctx.deps.actor, **_dict(data))


async def cancel_reminder(ctx: RunContext[Any], reminder_id: str, expected_version: int) -> dict[str, Any]:
    """取消一条尚未发送的提醒。"""
    return ctx.deps.reminder_service.cancel_reminder(ctx.deps.actor, reminder_id, expected_version)


def build_record_management_capability() -> Capability[Any]:
    return Capability(
        id="record-management",
        instructions=(
            "当前个人或群空间只使用系统固定的外部客户到访登记与接待报备表。"
            "写入、查询或修改记录前必须用 get_record_table 读取当前 Schema，不能猜测字段 key。"
            "新录入到访记录前，可用 list_records 查询同表相近候选；发现疑似冲突时一次性让用户确认处理方式和拟新增内容。"
        ),
        tools=[Tool(tool, takes_ctx=True, sequential=True) for tool in (
            list_record_tables, get_record_table, create_record, confirm_record, list_records, get_record,
            update_record, cancel_record,
        )],
    )


def build_reminder_management_capability() -> Capability[Any]:
    return Capability(
        id="reminder-management",
        instructions=(
            "提醒策略固定为到访开始前 2 小时，由系统在记录确认、修改或取消时自动联动。"
            "当前不向模型开放提醒查询、创建、修改、取消或策略调整。"
        ),
        tools=[],
    )


def build_observability_capability() -> Hooks:
    hooks = Hooks(id="observability")

    @hooks.on.tool_execute
    async def log_tool_execute(ctx: RunContext[Any], *, call, tool_def, args, handler):
        logger.info("Agent工具调用: tool=%s args=%s actor=%s scope=%s:%s", call.tool_name, args, ctx.deps.actor.actor_user_id, ctx.deps.actor.scope_type, ctx.deps.actor.scope_id)
        result = await handler(args)
        logger.info("Agent工具完成: tool=%s success=%s", call.tool_name, result.get("success") if isinstance(result, dict) else "")
        return result

    @hooks.on.tool_execute_error
    async def log_tool_error(ctx: RunContext[Any], *, call, tool_def, args, error):
        logger.warning("Agent工具失败: tool=%s args=%s error=%s", call.tool_name, args, error)
        raise error

    return hooks
