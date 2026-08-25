from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.domain.context import ActorContext
from src.domain.fixed_reception_table import FIXED_RECEPTION_TABLE_NAME, build_fixed_reception_reminder_content
from src.domain.table_schema import SchemaValidationError, resolve_record_anchor
from src.records.store import RecordStore, store as default_store
from src.services.authorization import require_scope, require_table_manager
from src.services.errors import ServiceError, failure, ok


class ReminderService:
    def __init__(self, record_store: RecordStore | None = None) -> None:
        self.store = record_store or default_store

    def get_policy(self, actor: ActorContext, table_id: str) -> dict[str, Any]:
        operation = "get_table_reminder_policy"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            policy = self.store.get_reminder_policy(table_id)
            return ok(operation, "自动提醒策略查询完成", table={"table_id": table_id, "name": table["name"]}, policy=policy)
        except ServiceError as exc:
            return failure(operation, exc)

    def set_policy(self, actor: ActorContext, table_id: str, rules: list[dict[str, Any]], expected_version: int | None = None, apply_to: str = "new_records") -> dict[str, Any]:
        operation = "set_table_reminder_policy"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            require_table_manager(actor, table)
            if table["status"] != "active":
                raise ServiceError("VALIDATION_FAILED", "已归档记录表不能配置自动提醒")
            if apply_to not in {"new_records", "all_active_records"}:
                raise ServiceError("VALIDATION_FAILED", "apply_to 必须是 new_records 或 all_active_records")
            clean_rules = [self._validate_policy_rule(table, rule) for rule in rules]
            if not clean_rules:
                raise ServiceError("VALIDATION_FAILED", "自动提醒策略至少需要一条规则")
            with self.store.connect() as conn:
                policy = self.store.set_reminder_policy(
                    {
                        "table_id": table_id,
                        "scope_type": actor.scope_type,
                        "scope_id": actor.scope_id,
                        "rules": clean_rules,
                        "actor_id": actor.actor_user_id,
                    },
                    expected_version,
                    conn,
                )
                if not policy:
                    raise ServiceError("REMINDER_POLICY_VERSION_CONFLICT", "自动提醒策略已被其他操作修改")
                self.store.add_audit("reminder_policy", table_id, "set", actor.actor_user_id, actor.scope_type, actor.scope_id, after=policy, conn=conn)
                if apply_to == "all_active_records":
                    for record in self.store.list_records(actor.scope_type, actor.scope_id, table_id, ["active"], conn):
                        self.reconcile_record(record, table, actor.actor_user_id, conn)
            return ok(operation, "自动提醒策略已保存", policy=policy, apply_to=apply_to)
        except (ServiceError, SchemaValidationError, ValueError) as exc:
            error = exc if isinstance(exc, ServiceError) else ServiceError("VALIDATION_FAILED", str(exc))
            return failure(operation, error)

    def clear_policy(self, actor: ActorContext, table_id: str, expected_version: int) -> dict[str, Any]:
        operation = "clear_table_reminder_policy"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            require_table_manager(actor, table)
            with self.store.connect() as conn:
                if not self.store.clear_reminder_policy(table_id, expected_version, conn):
                    raise ServiceError("REMINDER_POLICY_VERSION_CONFLICT", "自动提醒策略版本不匹配")
                for record in self.store.list_records(actor.scope_type, actor.scope_id, table_id, None, conn):
                    self.store.cancel_reminders_for_record(record["record_id"], "table_policy", conn)
                self.store.add_audit("reminder_policy", table_id, "clear", actor.actor_user_id, actor.scope_type, actor.scope_id, conn=conn)
            return ok(operation, "自动提醒策略已清除")
        except ServiceError as exc:
            return failure(operation, exc)

    def create_reminder(self, actor: ActorContext, content: str, trigger: dict[str, Any], record_id: str | None = None) -> dict[str, Any]:
        operation = "create_reminder"
        try:
            clean_content = content.strip()
            if not clean_content:
                raise ServiceError("VALIDATION_FAILED", "提醒内容不能为空")
            record = table = None
            if record_id:
                record = require_scope(actor, self.store.get_record(record_id))
                table = require_scope(actor, self.store.get_table(record["table_id"]))
            trigger_type, clean_trigger, fire_at, timezone = self._resolve_trigger(trigger, record, table)
            if fire_at <= datetime.now(UTC):
                raise ServiceError("REMINDER_TIME_IN_PAST", "提醒时间必须晚于当前时间")
            with self.store.connect() as conn:
                reminder = self.store.create_reminder(
                    {
                        "scope_type": actor.scope_type,
                        "scope_id": actor.scope_id,
                        "record_id": record_id,
                        "content": clean_content,
                        "trigger_type": trigger_type,
                        "trigger": clean_trigger,
                        "next_fire_at": _utc_iso(fire_at),
                        "timezone": timezone,
                        "source_type": "manual",
                        "actor_id": actor.actor_user_id,
                    },
                    conn,
                )
                self.store.add_audit("reminder", reminder["reminder_id"], "create", actor.actor_user_id, actor.scope_type, actor.scope_id, after=reminder, conn=conn)
            warning = None if self.store.is_conversation_reachable(actor.scope_type, actor.scope_id) else "当前会话尚未记录为可主动推送；请先确保在该会话中与机器人交互"
            return ok(operation, "提醒已创建", reminder=reminder, delivery_warning=warning)
        except (ServiceError, SchemaValidationError, ValueError) as exc:
            error = exc if isinstance(exc, ServiceError) else ServiceError("VALIDATION_FAILED", str(exc))
            return failure(operation, error)

    def list_reminders(self, actor: ActorContext, statuses: list[str] | None = None, record_id: str | None = None, date_from: str | None = None, date_to: str | None = None, limit: int = 20) -> dict[str, Any]:
        if record_id:
            try:
                require_scope(actor, self.store.get_record(record_id))
            except ServiceError as exc:
                return failure("list_reminders", exc)
        reminders = self.store.list_reminders(actor.scope_type, actor.scope_id, statuses, record_id)
        if date_from:
            reminders = [item for item in reminders if item["next_fire_at"] >= date_from]
        if date_to:
            reminders = [item for item in reminders if item["next_fire_at"] <= date_to]
        return ok("list_reminders", "提醒查询完成", reminders=reminders[: max(1, min(limit, 100))])

    def get_reminder(self, actor: ActorContext, reminder_id: str) -> dict[str, Any]:
        operation = "get_reminder"
        try:
            reminder = require_scope(actor, self.store.get_reminder(reminder_id))
            return ok(operation, "提醒查询完成", reminder=reminder)
        except ServiceError as exc:
            return failure(operation, exc)

    def update_reminder(self, actor: ActorContext, reminder_id: str, expected_version: int, content: str | None = None, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
        operation = "update_reminder"
        try:
            reminder = require_scope(actor, self.store.get_reminder(reminder_id))
            if reminder["status"] != "active":
                raise ServiceError("REMINDER_ALREADY_SENT", "只有未发送提醒可以修改")
            if reminder["source_type"] != "manual":
                raise ServiceError("PERMISSION_DENIED", "表级策略生成的提醒应通过自动提醒策略修改")
            if reminder["version"] != expected_version:
                raise ServiceError("REMINDER_VERSION_CONFLICT", "提醒已被其他操作修改", current_version=reminder["version"])
            changes: dict[str, Any] = {}
            if content is not None:
                if not content.strip():
                    raise ServiceError("VALIDATION_FAILED", "提醒内容不能为空")
                changes["content"] = content.strip()
            if trigger is not None:
                record = table = None
                if reminder.get("record_id"):
                    record = require_scope(actor, self.store.get_record(reminder["record_id"]))
                    table = require_scope(actor, self.store.get_table(record["table_id"]))
                trigger_type, clean_trigger, fire_at, _ = self._resolve_trigger(trigger, record, table)
                if fire_at <= datetime.now(UTC):
                    raise ServiceError("REMINDER_TIME_IN_PAST", "提醒时间必须晚于当前时间")
                changes.update({"trigger_type": trigger_type, "trigger": clean_trigger, "next_fire_at": _utc_iso(fire_at)})
            if not changes:
                raise ServiceError("VALIDATION_FAILED", "没有可更新的提醒内容")
            with self.store.connect() as conn:
                updated = self.store.update_reminder(reminder_id, expected_version, changes, actor.actor_user_id, conn)
                if not updated:
                    raise ServiceError("REMINDER_VERSION_CONFLICT", "提醒已被其他操作修改")
                self.store.add_audit("reminder", reminder_id, "update", actor.actor_user_id, actor.scope_type, actor.scope_id, before=reminder, after=updated, conn=conn)
            return ok(operation, "提醒已更新", reminder=updated)
        except (ServiceError, SchemaValidationError, ValueError) as exc:
            error = exc if isinstance(exc, ServiceError) else ServiceError("VALIDATION_FAILED", str(exc))
            return failure(operation, error)

    def cancel_reminder(self, actor: ActorContext, reminder_id: str, expected_version: int) -> dict[str, Any]:
        operation = "cancel_reminder"
        try:
            reminder = require_scope(actor, self.store.get_reminder(reminder_id))
            if reminder["status"] != "active":
                raise ServiceError("REMINDER_ALREADY_SENT", "提醒已发送或已取消")
            with self.store.connect() as conn:
                updated = self.store.update_reminder(reminder_id, expected_version, {"status": "cancelled"}, actor.actor_user_id, conn)
                if not updated:
                    raise ServiceError("REMINDER_VERSION_CONFLICT", "提醒已被其他操作修改")
                conn.execute("update reminder_deliveries set status='cancelled', updated_at=? where reminder_id=? and status!='sent'", (_utc_iso(datetime.now(UTC)), reminder_id))
                self.store.add_audit("reminder", reminder_id, "cancel", actor.actor_user_id, actor.scope_type, actor.scope_id, before=reminder, after=updated, conn=conn)
            return ok(operation, "提醒已取消", reminder=updated)
        except ServiceError as exc:
            return failure(operation, exc)

    def reconcile_record(self, record: dict[str, Any], table: dict[str, Any], actor_id: str, conn=None) -> None:
        if record["status"] != "active":
            self.store.cancel_reminders_for_record(record["record_id"], None, conn)
            return
        policy = self.store.get_reminder_policy(table["table_id"], conn)
        policy_rules = {rule["rule_id"]: rule for rule in (policy or {}).get("rules", [])}
        existing_policy = self.store.list_reminders(record["scope_type"], record["scope_id"], ["active"], record["record_id"], "table_policy", conn)
        for reminder in existing_policy:
            if reminder.get("source_rule_id") not in policy_rules:
                self.store.update_reminder(reminder["reminder_id"], reminder["version"], {"status": "cancelled"}, actor_id, conn)
                if conn is not None:
                    conn.execute("update reminder_deliveries set status='cancelled', updated_at=? where reminder_id=? and status!='sent'", (_utc_iso(datetime.now(UTC)), reminder["reminder_id"]))

        for rule in policy_rules.values():
            fire_at = self._resolve_policy_fire_at(table, record, rule)
            existing = next((item for item in existing_policy if item.get("source_rule_id") == rule["rule_id"]), None)
            if fire_at <= datetime.now(UTC):
                if existing:
                    self.store.update_reminder(existing["reminder_id"], existing["version"], {"status": "cancelled"}, actor_id, conn)
                continue
            trigger = {key: value for key, value in rule.items() if key != "content"}
            content = _resolve_reminder_content(table, record, rule)
            if existing:
                self.store.update_reminder(existing["reminder_id"], existing["version"], {"content": content, "trigger": trigger, "next_fire_at": _utc_iso(fire_at)}, actor_id, conn)
            else:
                self.store.create_reminder(
                    {
                        "scope_type": record["scope_type"], "scope_id": record["scope_id"],
                        "record_id": record["record_id"], "content": content,
                        "trigger_type": "relative_to_record", "trigger": trigger,
                        "next_fire_at": _utc_iso(fire_at), "timezone": table["schema"].get("time_config", {}).get("timezone", settings.scheduler_timezone),
                        "source_type": "table_policy", "source_rule_id": rule["rule_id"], "actor_id": actor_id,
                    },
                    conn,
                )

        manual_relative = [item for item in self.store.list_reminders(record["scope_type"], record["scope_id"], ["active"], record["record_id"], "manual", conn) if item["trigger_type"] == "relative_to_record"]
        for reminder in manual_relative:
            _, clean_trigger, fire_at, _ = self._resolve_trigger(reminder["trigger"], record, table)
            if fire_at <= datetime.now(UTC):
                self.store.update_reminder(reminder["reminder_id"], reminder["version"], {"status": "cancelled"}, actor_id, conn)
            else:
                self.store.update_reminder(reminder["reminder_id"], reminder["version"], {"trigger": clean_trigger, "next_fire_at": _utc_iso(fire_at)}, actor_id, conn)

    def _validate_policy_rule(self, table: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
        rule_type = rule.get("type")
        anchor = rule.get("anchor") or "schedule_start"
        self._assert_anchor(table, anchor)
        clean: dict[str, Any] = {"type": rule_type, "anchor": anchor, "content": str(rule.get("content") or "").strip()}
        if rule_type == "relative_to_record":
            offset = int(rule.get("offset_minutes", 0))
            if offset >= 0:
                raise ServiceError("VALIDATION_FAILED", "自动提醒的 offset_minutes 必须小于 0")
            clean["offset_minutes"] = offset
        elif rule_type == "day_before":
            days_before = int(rule.get("days_before", 1))
            if days_before < 0:
                raise ServiceError("VALIDATION_FAILED", "days_before 不能小于 0")
            at_time = time.fromisoformat(str(rule.get("at_time") or "18:00")).strftime("%H:%M")
            clean.update({"days_before": days_before, "at_time": at_time})
        else:
            raise ServiceError("VALIDATION_FAILED", f"不支持的自动提醒规则：{rule_type}")
        signature = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        clean["rule_id"] = f"RULE_{hashlib.sha256(signature.encode()).hexdigest()[:12]}"
        return clean

    def _resolve_policy_fire_at(self, table: dict[str, Any], record: dict[str, Any], rule: dict[str, Any]) -> datetime:
        anchor = resolve_record_anchor(table, record, rule["anchor"])
        if rule["type"] == "relative_to_record":
            return (anchor + timedelta(minutes=int(rule["offset_minutes"]))).astimezone(UTC)
        timezone = anchor.tzinfo or ZoneInfo(settings.scheduler_timezone)
        target_date = anchor.date() - timedelta(days=int(rule["days_before"]))
        return datetime.combine(target_date, time.fromisoformat(rule["at_time"]), timezone).astimezone(UTC)

    def _resolve_trigger(self, trigger: dict[str, Any], record: dict[str, Any] | None, table: dict[str, Any] | None) -> tuple[str, dict[str, Any], datetime, str]:
        trigger_type = trigger.get("type")
        if trigger_type == "absolute":
            raw = str(trigger.get("at") or "")
            if not raw:
                raise ServiceError("VALIDATION_FAILED", "绝对提醒缺少 at")
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            timezone = str(trigger.get("timezone") or settings.scheduler_timezone)
            if value.tzinfo is None:
                try:
                    value = value.replace(tzinfo=ZoneInfo(timezone))
                except Exception as exc:
                    raise ServiceError("VALIDATION_FAILED", f"无效时区：{timezone}") from exc
            return "absolute", {"type": "absolute", "at": value.isoformat(timespec="minutes")}, value.astimezone(UTC), timezone
        if trigger_type == "relative_to_record":
            if not record or not table:
                raise ServiceError("VALIDATION_FAILED", "相对提醒必须关联记录")
            anchor = str(trigger.get("anchor") or "schedule_start")
            offset = int(trigger.get("offset_minutes", 0))
            self._assert_anchor(table, anchor)
            value = resolve_record_anchor(table, record, anchor) + timedelta(minutes=offset)
            clean = {"type": "relative_to_record", "anchor": anchor, "offset_minutes": offset}
            timezone = table["schema"].get("time_config", {}).get("timezone", settings.scheduler_timezone)
            return "relative_to_record", clean, value.astimezone(UTC), timezone
        raise ServiceError("VALIDATION_FAILED", f"不支持的提醒触发类型：{trigger_type}")

    @staticmethod
    def _assert_anchor(table: dict[str, Any], anchor: str) -> None:
        config = table["schema"].get("time_config") or {}
        key = "start" if anchor == "schedule_start" else "end" if anchor == "schedule_end" else ""
        if not key or key not in config:
            raise ServiceError("INVALID_TIME_ANCHOR", f"记录表未配置时间锚点：{anchor}")


def _record_reminder_content(table: dict[str, Any], record: dict[str, Any]) -> str:
    title_field = next((field["key"] for field in table["schema"]["fields"] if field.get("semantic_role") == "schedule_title"), None)
    title = record["values"].get(title_field) if title_field else None
    return f"【{table['name']}提醒】{title or '有一条日程即将开始'}"


def _resolve_reminder_content(table: dict[str, Any], record: dict[str, Any], rule: dict[str, Any]) -> str:
    if table.get("name") == FIXED_RECEPTION_TABLE_NAME:
        return build_fixed_reception_reminder_content(record)
    content = str(rule.get("content") or "").strip()
    return content or _record_reminder_content(table, record)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
