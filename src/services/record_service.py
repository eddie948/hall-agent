from __future__ import annotations

from typing import Any

from src.domain.context import ActorContext
from src.domain.table_schema import public_schema_view, validate_record_values
from src.records.store import RecordStore, store as default_store
from src.services.authorization import require_scope
from src.services.errors import ServiceError, failure, ok
from src.services.reminder_service import ReminderService


class RecordService:
    def __init__(self, record_store: RecordStore | None = None, reminder_service: ReminderService | None = None) -> None:
        self.store = record_store or default_store
        self.reminders = reminder_service or ReminderService(self.store)

    def create_record(self, actor: ActorContext, table_id: str, values: dict[str, Any], raw_text: str = "") -> dict[str, Any]:
        operation = "create_record"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            if table["status"] != "active":
                raise ServiceError("VALIDATION_FAILED", "已归档记录表不能添加记录")
            existing = self.store.get_actor_draft(table_id, actor.scope_type, actor.scope_id, actor.actor_user_id)
            merged = {**(existing or {}).get("values", {}), **values}
            clean, missing, field_errors = validate_record_values(table["schema"], merged)
            if field_errors:
                raise ServiceError("VALIDATION_FAILED", "记录字段校验失败", field_errors=field_errors, missing_fields=missing)
            status = "draft" if missing else "pending_confirm" if table["creation_policy"] == "confirm" else "active"
            with self.store.connect() as conn:
                if existing:
                    record = self.store.update_record(existing["record_id"], existing["version"], {"values": clean, "status": status, "schema_version": table["schema_version"], "raw_text": raw_text or existing["raw_text"]}, actor.actor_user_id, conn)
                    if not record:
                        raise ServiceError("RECORD_VERSION_CONFLICT", "记录草稿已被其他操作修改")
                    event = "update_draft"
                else:
                    record = self.store.create_record({"table_id": table_id, "scope_type": actor.scope_type, "scope_id": actor.scope_id, "status": status, "values": clean, "schema_version": table["schema_version"], "raw_text": raw_text, "actor_id": actor.actor_user_id}, conn)
                    event = "create"
                self.store.add_audit("record", record["record_id"], event, actor.actor_user_id, actor.scope_type, actor.scope_id, after=record, conn=conn)
                if status == "active":
                    self.reminders.reconcile_record(record, table, actor.actor_user_id, conn)
            message = "记录信息还不完整" if missing else "记录等待确认" if status == "pending_confirm" else "记录已创建"
            return ok(operation, message, record=record, missing_fields=missing)
        except ServiceError as exc:
            return failure(operation, exc)

    def confirm_record(self, actor: ActorContext, record_id: str, expected_version: int) -> dict[str, Any]:
        operation = "confirm_record"
        try:
            record = require_scope(actor, self.store.get_record(record_id))
            if record["status"] != "pending_confirm":
                raise ServiceError("VALIDATION_FAILED", "只有待确认记录可以确认")
            table = require_scope(actor, self.store.get_table(record["table_id"]))
            clean, missing, errors = validate_record_values(table["schema"], record["values"])
            if missing or errors:
                raise ServiceError("VALIDATION_FAILED", "记录信息不完整", missing_fields=missing, field_errors=errors)
            with self.store.connect() as conn:
                updated = self.store.update_record(record_id, expected_version, {"status": "active", "values": clean, "schema_version": table["schema_version"]}, actor.actor_user_id, conn)
                if not updated:
                    raise ServiceError("RECORD_VERSION_CONFLICT", "记录已被其他操作修改", current_version=record["version"])
                self.reminders.reconcile_record(updated, table, actor.actor_user_id, conn)
                self.store.add_audit("record", record_id, "confirm", actor.actor_user_id, actor.scope_type, actor.scope_id, before=record, after=updated, conn=conn)
            return ok(operation, "记录已确认", record=updated)
        except ServiceError as exc:
            return failure(operation, exc)

    def list_records(self, actor: ActorContext, table_id: str, statuses: list[str] | None = None, filters: list[dict[str, Any]] | None = None, sort: list[dict[str, str]] | None = None, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        operation = "list_records"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            records = self.store.list_records(actor.scope_type, actor.scope_id, table_id, statuses or ["active"])
            records = _apply_filters(records, table, filters or [])
            records = _apply_sort(records, table, sort or [])
            offset = int(cursor or 0)
            page_size = max(1, min(limit, 100))
            page = records[offset: offset + page_size]
            next_cursor = str(offset + page_size) if offset + page_size < len(records) else None
            return ok(operation, "记录查询完成", records=page, next_cursor=next_cursor, total=len(records))
        except (ServiceError, ValueError, TypeError) as exc:
            error = exc if isinstance(exc, ServiceError) else ServiceError("INVALID_FILTER", str(exc))
            return failure(operation, error)

    def get_record(self, actor: ActorContext, record_id: str) -> dict[str, Any]:
        operation = "get_record"
        try:
            record = require_scope(actor, self.store.get_record(record_id))
            table = require_scope(actor, self.store.get_table(record["table_id"]))
            public_schema = public_schema_view(table["schema"])
            return ok(operation, "记录查询完成", record=record, table={"table_id": table["table_id"], "name": table["name"], "fields": public_schema["fields"]})
        except ServiceError as exc:
            return failure(operation, exc)

    def update_record(self, actor: ActorContext, record_id: str, expected_version: int, values: dict[str, Any], unset_fields: list[str] | None = None) -> dict[str, Any]:
        operation = "update_record"
        try:
            record = require_scope(actor, self.store.get_record(record_id))
            if record["status"] == "cancelled":
                raise ServiceError("VALIDATION_FAILED", "已取消记录不能修改")
            if record["version"] != expected_version:
                raise ServiceError("RECORD_VERSION_CONFLICT", "记录已被其他操作修改", current_version=record["version"])
            table = require_scope(actor, self.store.get_table(record["table_id"]))
            merged = {**record["values"], **values}
            for key in unset_fields or []:
                merged.pop(key, None)
            clean, missing, errors = validate_record_values(table["schema"], merged)
            if missing or errors:
                raise ServiceError("VALIDATION_FAILED", "记录字段校验失败", missing_fields=missing, field_errors=errors)
            with self.store.connect() as conn:
                updated = self.store.update_record(record_id, expected_version, {"values": clean, "schema_version": table["schema_version"]}, actor.actor_user_id, conn)
                if not updated:
                    raise ServiceError("RECORD_VERSION_CONFLICT", "记录已被其他操作修改")
                if updated["status"] == "active":
                    self.reminders.reconcile_record(updated, table, actor.actor_user_id, conn)
                self.store.add_audit("record", record_id, "update", actor.actor_user_id, actor.scope_type, actor.scope_id, before=record, after=updated, conn=conn)
            return ok(operation, "记录已更新", record=updated)
        except ServiceError as exc:
            return failure(operation, exc)

    def cancel_record(self, actor: ActorContext, record_id: str, expected_version: int, reason: str = "") -> dict[str, Any]:
        operation = "cancel_record"
        try:
            record = require_scope(actor, self.store.get_record(record_id))
            if record["version"] != expected_version:
                raise ServiceError("RECORD_VERSION_CONFLICT", "记录已被其他操作修改", current_version=record["version"])
            if record["status"] == "cancelled":
                raise ServiceError("VALIDATION_FAILED", "记录已经取消")
            with self.store.connect() as conn:
                updated = self.store.update_record(record_id, expected_version, {"status": "cancelled"}, actor.actor_user_id, conn)
                if not updated:
                    raise ServiceError("RECORD_VERSION_CONFLICT", "记录已被其他操作修改")
                self.store.cancel_reminders_for_record(record_id, None, conn)
                self.store.add_audit("record", record_id, "cancel", actor.actor_user_id, actor.scope_type, actor.scope_id, before=record, after={**updated, "reason": reason}, conn=conn)
            return ok(operation, "记录已取消", record=updated)
        except ServiceError as exc:
            return failure(operation, exc)


def _field_map(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["key"]: field for field in table["schema"]["fields"] if field.get("status") == "active"}


def _apply_filters(records: list[dict[str, Any]], table: dict[str, Any], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = _field_map(table)
    result = records
    for condition in filters:
        key = str(condition.get("field_key") or "")
        operator = str(condition.get("operator") or "")
        if key not in fields:
            raise ValueError(f"查询字段不存在：{key}")
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"}:
            raise ValueError(f"不支持的查询操作符：{operator}")
        target = condition.get("value")
        result = [record for record in result if _matches(record["values"].get(key), operator, target)]
    return result


def _matches(value: Any, operator: str, target: Any) -> bool:
    if operator == "eq": return value == target
    if operator == "ne": return value != target
    if operator == "contains": return str(target).lower() in str(value or "").lower()
    if operator == "in": return value in (target if isinstance(target, list) else [target])
    if value is None: return False
    if operator == "gt": return value > target
    if operator == "gte": return value >= target
    if operator == "lt": return value < target
    if operator == "lte": return value <= target
    return False


def _apply_sort(records: list[dict[str, Any]], table: dict[str, Any], rules: list[dict[str, str]]) -> list[dict[str, Any]]:
    fields = _field_map(table)
    result = list(records)
    for rule in reversed(rules):
        key = rule.get("field_key", "")
        if key not in fields:
            raise ValueError(f"排序字段不存在：{key}")
        result.sort(key=lambda item: (item["values"].get(key) is None, item["values"].get(key)), reverse=rule.get("direction") == "desc")
    return result
