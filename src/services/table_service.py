from __future__ import annotations

import copy
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.domain.context import ActorContext
from src.domain.fixed_reception_table import (
    DEPRECATED_FIXED_RECEPTION_FIELD_KEYS,
    FIXED_RECEPTION_REMINDER_RULES,
    FIXED_RECEPTION_SCHEMA,
    FIXED_RECEPTION_TABLE_DESCRIPTION,
    FIXED_RECEPTION_TABLE_NAME,
    RECEPTION_STATUS_COMPLETED,
    RECEPTION_STATUS_FIELD_KEY,
    RECEPTION_STATUS_IN_PROGRESS,
    RECEPTION_STATUS_PENDING,
)
from src.config import settings
from src.domain.table_schema import SchemaValidationError, public_schema_view, resolve_record_anchor, validate_table_schema
from src.records.store import RecordStore, store as default_store
from src.services.authorization import require_scope, require_table_manager
from src.services.errors import ServiceError, failure, ok


class TableService:
    def __init__(self, record_store: RecordStore | None = None) -> None:
        self.store = record_store or default_store

    def ensure_fixed_table(self, actor: ActorContext) -> dict[str, Any]:
        existing = self._find_fixed_table(actor)
        if existing:
            existing = self._ensure_fixed_table_schema(actor, existing)
            self._ensure_fixed_reminder_policy(actor, existing)
            return existing

        schema = validate_table_schema(FIXED_RECEPTION_SCHEMA)
        with self.store.connect() as conn:
            table = self.store.create_table(
                {
                    "scope_type": actor.scope_type,
                    "scope_id": actor.scope_id,
                    "name": FIXED_RECEPTION_TABLE_NAME,
                    "description": FIXED_RECEPTION_TABLE_DESCRIPTION,
                    "creation_policy": "confirm",
                    "schema": schema,
                    "created_by": actor.actor_user_id,
                },
                conn,
            )
            self.store.add_audit("table", table["table_id"], "create", actor.actor_user_id, actor.scope_type, actor.scope_id, after=table, conn=conn)
            self._ensure_fixed_reminder_policy(actor, table, conn)
            return table

    def refresh_reception_statuses(self, actor: ActorContext, current_time: str) -> dict[str, Any]:
        table = self.ensure_fixed_table(actor)
        now = _parse_current_time(current_time)
        records = self.store.list_records(actor.scope_type, actor.scope_id, table["table_id"], ["active"])
        with self.store.connect() as conn:
            for record in records:
                desired = _computed_reception_status(table, record, now)
                if not desired or record["values"].get(RECEPTION_STATUS_FIELD_KEY) == desired:
                    continue
                before = copy.deepcopy(record)
                values = {**record["values"], RECEPTION_STATUS_FIELD_KEY: desired}
                updated = self.store.update_record(record["record_id"], record["version"], {"values": values, "schema_version": table["schema_version"]}, "system", conn)
                if updated:
                    self.store.add_audit("record", record["record_id"], "auto_update_reception_status", "system", actor.scope_type, actor.scope_id, before=before, after=updated, conn=conn)
        return self.get_unfinished_reception_records(actor)

    def get_unfinished_reception_records(self, actor: ActorContext) -> dict[str, Any]:
        table = self.ensure_fixed_table(actor)
        records = self.store.list_records(actor.scope_type, actor.scope_id, table["table_id"], ["active"])
        unfinished = [
            record for record in records
            if record["values"].get(RECEPTION_STATUS_FIELD_KEY) != RECEPTION_STATUS_COMPLETED
        ]
        unfinished.sort(key=_record_schedule_sort_key)
        return {"table": table, "records": unfinished}

    def _find_fixed_table(self, actor: ActorContext) -> dict[str, Any] | None:
        tables = self.store.list_tables(actor.scope_type, actor.scope_id, include_archived=False)
        return next((table for table in tables if table["name"] == FIXED_RECEPTION_TABLE_NAME), None)

    def _ensure_fixed_table_schema(self, actor: ActorContext, table: dict[str, Any]) -> dict[str, Any]:
        fixed_schema = validate_table_schema(FIXED_RECEPTION_SCHEMA)
        if _fixed_schema_is_current(table["schema"], fixed_schema):
            return table

        updated = copy.deepcopy(table)
        current_by_key = {field["key"]: field for field in updated["schema"].get("fields", [])}
        for fixed_field in fixed_schema["fields"]:
            current_field = current_by_key.get(fixed_field["key"])
            if current_field:
                current_field.update(copy.deepcopy(fixed_field))
            else:
                updated["schema"]["fields"].append(fixed_field)
        for field in updated["schema"].get("fields", []):
            if field["key"] in DEPRECATED_FIXED_RECEPTION_FIELD_KEYS:
                field["status"] = "deprecated"
        updated["schema"]["time_config"] = fixed_schema["time_config"]
        updated["schema"] = validate_table_schema(updated["schema"])
        with self.store.connect() as conn:
            saved = self.store.update_table(table["table_id"], table["schema_version"], updated, actor.actor_user_id, conn)
            if not saved:
                return self.store.get_table(table["table_id"], conn=conn) or table
            self.store.add_audit("table", table["table_id"], "update_fixed_schema", actor.actor_user_id, actor.scope_type, actor.scope_id, before=table, after=saved, conn=conn)
            return saved

    def _ensure_fixed_reminder_policy(self, actor: ActorContext, table: dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
        policy = self.store.get_reminder_policy(table["table_id"], conn)
        if policy and policy.get("rules") == FIXED_RECEPTION_REMINDER_RULES:
            return
        expected_version = policy["version"] if policy else None
        saved = self.store.set_reminder_policy(
            {
                "table_id": table["table_id"],
                "scope_type": actor.scope_type,
                "scope_id": actor.scope_id,
                "rules": FIXED_RECEPTION_REMINDER_RULES,
                "actor_id": actor.actor_user_id,
            },
            expected_version,
            conn,
        )
        if saved:
            self.store.add_audit("reminder_policy", table["table_id"], "set", actor.actor_user_id, actor.scope_type, actor.scope_id, after=saved, conn=conn)

    def create_table(self, actor: ActorContext, data: dict[str, Any]) -> dict[str, Any]:
        operation = "create_record_table"
        try:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ServiceError("VALIDATION_FAILED", "记录表名称不能为空")
            schema = validate_table_schema({"fields": data.get("fields"), "time_config": data.get("time_config")})
            creation_policy = data.get("creation_policy") or "confirm"
            if creation_policy not in {"immediate", "confirm"}:
                raise ServiceError("VALIDATION_FAILED", "creation_policy 只能是 immediate 或 confirm")
            with self.store.connect() as conn:
                table = self.store.create_table(
                    {
                        "scope_type": actor.scope_type,
                        "scope_id": actor.scope_id,
                        "name": name,
                        "description": str(data.get("description") or "").strip(),
                        "creation_policy": creation_policy,
                        "schema": schema,
                        "created_by": actor.actor_user_id,
                    },
                    conn,
                )
                self.store.add_audit("table", table["table_id"], "create", actor.actor_user_id, actor.scope_type, actor.scope_id, after=table, conn=conn)
            return ok(operation, "记录表已创建", table=table)
        except SchemaValidationError as exc:
            return failure(operation, ServiceError("VALIDATION_FAILED", str(exc), field_errors=exc.field_errors))
        except sqlite3.IntegrityError as exc:
            message = "当前空间已经存在同名记录表" if "idx_record_tables_active_name" in str(exc) or "UNIQUE" in str(exc) else "记录表保存失败"
            return failure(operation, ServiceError("VALIDATION_FAILED", message))
        except ServiceError as exc:
            return failure(operation, exc)

    def list_tables(self, actor: ActorContext, keyword: str = "", include_archived: bool = False, limit: int = 20) -> dict[str, Any]:
        tables = self.store.list_tables(actor.scope_type, actor.scope_id, include_archived)
        needle = keyword.strip().lower()
        if needle:
            tables = [table for table in tables if needle in table["name"].lower() or needle in table["description"].lower()]
        summaries = [
            {
                "table_id": table["table_id"],
                "name": table["name"],
                "description": table["description"],
                "creation_policy": table["creation_policy"],
                "schema_version": table["schema_version"],
                "status": table["status"],
                "field_names": [field["name"] for field in table["schema"]["fields"] if field.get("status") == "active"],
            }
            for table in tables[: max(1, min(limit, 100))]
        ]
        return ok("list_record_tables", "记录表查询完成", tables=summaries)

    def get_table(self, actor: ActorContext, table_id: str) -> dict[str, Any]:
        operation = "get_record_table"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            public_table = copy.deepcopy(table)
            public_table["schema"] = public_schema_view(table["schema"])
            return ok(operation, "记录表查询完成", table=public_table)
        except ServiceError as exc:
            return failure(operation, exc)

    def update_table(self, actor: ActorContext, table_id: str, expected_schema_version: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        operation = "update_record_table"
        try:
            current = require_scope(actor, self.store.get_table(table_id))
            require_table_manager(actor, current)
            if current["status"] != "active":
                raise ServiceError("VALIDATION_FAILED", "已归档记录表不能修改")
            if current["schema_version"] != expected_schema_version:
                raise ServiceError("SCHEMA_VERSION_CONFLICT", "记录表格式已被其他操作修改", current_version=current["schema_version"])
            updated = copy.deepcopy(current)
            for change in operations:
                self._apply_operation(updated, change)
            updated["schema"] = validate_table_schema(updated["schema"])
            with self.store.connect() as conn:
                saved = self.store.update_table(table_id, expected_schema_version, updated, actor.actor_user_id, conn)
                if not saved:
                    raise ServiceError("SCHEMA_VERSION_CONFLICT", "记录表格式已被其他操作��改")
                self.store.add_audit("table", table_id, "update", actor.actor_user_id, actor.scope_type, actor.scope_id, before=current, after=saved, conn=conn)
            return ok(operation, "记录表已更新", table=saved)
        except SchemaValidationError as exc:
            return failure(operation, ServiceError("VALIDATION_FAILED", str(exc), field_errors=exc.field_errors))
        except ServiceError as exc:
            return failure(operation, exc)

    def archive_table(self, actor: ActorContext, table_id: str, expected_schema_version: int) -> dict[str, Any]:
        operation = "archive_record_table"
        try:
            table = require_scope(actor, self.store.get_table(table_id))
            require_table_manager(actor, table)
            if table["schema_version"] != expected_schema_version:
                raise ServiceError("SCHEMA_VERSION_CONFLICT", "记录表格式版本不匹配", current_version=table["schema_version"])
            with self.store.connect() as conn:
                archived = self.store.archive_table(table_id, expected_schema_version, conn)
                if not archived:
                    raise ServiceError("SCHEMA_VERSION_CONFLICT", "记录表格式已被其他操作修改")
                self.store.add_audit("table", table_id, "archive", actor.actor_user_id, actor.scope_type, actor.scope_id, before=table, after=archived, conn=conn)
            return ok(operation, "记录表已归档", table=archived)
        except ServiceError as exc:
            return failure(operation, exc)

    @staticmethod
    def _apply_operation(table: dict[str, Any], change: dict[str, Any]) -> None:
        action = change.get("action")
        if action == "rename_table":
            name = str(change.get("name") or "").strip()
            if not name:
                raise ServiceError("VALIDATION_FAILED", "记录表名称不能为空")
            table["name"] = name
        elif action == "update_description":
            table["description"] = str(change.get("description") or "").strip()
        elif action == "update_creation_policy":
            value = change.get("creation_policy")
            if value not in {"immediate", "confirm"}:
                raise ServiceError("VALIDATION_FAILED", "无效的创建策略")
            table["creation_policy"] = value
        elif action == "add_field":
            table["schema"]["fields"].append(change.get("field") or {})
        elif action == "update_field":
            field = _find_field(table, str(change.get("field_key") or ""))
            changes = dict(change.get("changes") or {})
            if "key" in changes or "type" in changes:
                raise ServiceError("VALIDATION_FAILED", "字段 key 和类型创建后不能修改")
            field.update(changes)
        elif action == "deprecate_field":
            _find_field(table, str(change.get("field_key") or ""))["status"] = "deprecated"
        elif action == "reorder_field":
            field = _find_field(table, str(change.get("field_key") or ""))
            fields = table["schema"]["fields"]
            fields.remove(field)
            fields.insert(max(0, min(int(change.get("position") or 0), len(fields))), field)
        elif action == "update_time_config":
            table["schema"]["time_config"] = change.get("time_config") or {}
        else:
            raise ServiceError("VALIDATION_FAILED", f"不支持的表结构操作：{action}")


def _find_field(table: dict[str, Any], field_key: str) -> dict[str, Any]:
    for field in table["schema"]["fields"]:
        if field["key"] == field_key:
            return field
    raise ServiceError("VALIDATION_FAILED", f"字段不存在：{field_key}")


def _fixed_schema_is_current(current_schema: dict[str, Any], fixed_schema: dict[str, Any]) -> bool:
    current_by_key = {field["key"]: field for field in current_schema.get("fields", [])}
    for fixed_field in fixed_schema["fields"]:
        current_field = current_by_key.get(fixed_field["key"])
        if not current_field or current_field.get("status") != "active":
            return False
        for key, value in fixed_field.items():
            if key == "position":
                continue
            if current_field.get(key) != value:
                return False
    for key in DEPRECATED_FIXED_RECEPTION_FIELD_KEYS:
        field = current_by_key.get(key)
        if field and field.get("status") == "active":
            return False
    return current_schema.get("time_config") == fixed_schema.get("time_config")


def _parse_current_time(value: str) -> datetime:
    timezone = ZoneInfo(settings.scheduler_timezone)
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone)
        except ValueError:
            pass
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)


def _computed_reception_status(table: dict[str, Any], record: dict[str, Any], now: datetime) -> str | None:
    try:
        start = resolve_record_anchor(table, record, "schedule_start")
        end = resolve_record_anchor(table, record, "schedule_end")
    except (SchemaValidationError, ValueError):
        return None
    current = now.astimezone(start.tzinfo)
    if current < start:
        return RECEPTION_STATUS_PENDING
    if current <= end:
        return RECEPTION_STATUS_IN_PROGRESS
    return RECEPTION_STATUS_COMPLETED


def _record_schedule_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    values = record.get("values", {})
    return (
        str(values.get("visit_date") or ""),
        str(values.get("visit_start_time") or ""),
        str(record.get("record_id") or ""),
    )
