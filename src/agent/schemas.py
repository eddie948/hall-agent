from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


FieldType = Literal["text", "integer", "number", "boolean", "date", "time", "datetime", "enum", "user", "location", "url"]
SemanticRole = Literal["", "schedule_title", "schedule_date", "schedule_start_time", "schedule_end_time", "schedule_start_at", "schedule_end_at", "schedule_status"]


class FieldDefinition(StrictModel):
    key: str = Field(description="稳定字段键，使用小写英文字母、数字和下划线，创建后不可修改。")
    name: str = Field(description="用户可见的字段名称。")
    type: FieldType
    required: bool = False
    default_value: Any | None = None
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    enum_options: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    semantic_role: SemanticRole = ""


class TimeAnchor(StrictModel):
    datetime_field: str | None = None
    date_field: str | None = None
    time_field: str | None = None


class TimeConfig(StrictModel):
    timezone: str = "Asia/Shanghai"
    start: TimeAnchor | None = None
    end: TimeAnchor | None = None


class CreateRecordTableInput(StrictModel):
    name: str
    description: str = ""
    creation_policy: Literal["immediate", "confirm"] = "confirm"
    fields: list[FieldDefinition]
    time_config: TimeConfig | None = None


class TableListQuery(StrictModel):
    keyword: str = ""
    include_archived: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class TableUpdateOperation(StrictModel):
    action: Literal["rename_table", "update_description", "update_creation_policy", "add_field", "update_field", "deprecate_field", "reorder_field", "update_time_config"]
    name: str | None = None
    description: str | None = None
    creation_policy: Literal["immediate", "confirm"] | None = None
    field: FieldDefinition | None = None
    field_key: str | None = None
    changes: dict[str, Any] | None = None
    position: int | None = None
    time_config: TimeConfig | None = None


class UpdateRecordTableInput(StrictModel):
    table_id: str
    expected_schema_version: int = Field(ge=1)
    operations: list[TableUpdateOperation]


class CreateRecordInput(StrictModel):
    table_id: str
    values: dict[str, Any]
    raw_text: str = ""


class RecordFilter(StrictModel):
    field_key: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"]
    value: Any


class RecordSort(StrictModel):
    field_key: str
    direction: Literal["asc", "desc"] = "asc"


class RecordListQuery(StrictModel):
    table_id: str
    statuses: list[Literal["draft", "pending_confirm", "active", "cancelled"]] = Field(default_factory=lambda: ["active"])
    filters: list[RecordFilter] = Field(default_factory=list)
    sort: list[RecordSort] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class UpdateRecordInput(StrictModel):
    record_id: str
    expected_version: int = Field(ge=1)
    values: dict[str, Any] = Field(default_factory=dict)
    unset_fields: list[str] = Field(default_factory=list)


class ReminderTrigger(StrictModel):
    type: Literal["absolute", "relative_to_record"]
    at: str | None = Field(default=None, description="绝对提醒时间，ISO 8601 格式。")
    timezone: str | None = None
    anchor: Literal["schedule_start", "schedule_end"] | None = None
    offset_minutes: int | None = None


class CreateReminderInput(StrictModel):
    content: str
    trigger: ReminderTrigger
    record_id: str | None = None


class ReminderListQuery(StrictModel):
    statuses: list[Literal["active", "sent", "cancelled", "failed"]] = Field(default_factory=lambda: ["active"])
    record_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class UpdateReminderInput(StrictModel):
    reminder_id: str
    expected_version: int = Field(ge=1)
    content: str | None = None
    trigger: ReminderTrigger | None = None


class ReminderPolicyRule(StrictModel):
    type: Literal["day_before", "relative_to_record"]
    anchor: Literal["schedule_start", "schedule_end"] = "schedule_start"
    days_before: int | None = Field(default=None, ge=0)
    at_time: str | None = None
    offset_minutes: int | None = None
    content: str = ""


class SetReminderPolicyInput(StrictModel):
    table_id: str
    rules: list[ReminderPolicyRule]
    expected_version: int | None = Field(default=None, ge=0)
    apply_to: Literal["new_records", "all_active_records"] = "new_records"
