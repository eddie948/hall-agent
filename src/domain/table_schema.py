from __future__ import annotations

import copy
import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


FIELD_TYPES = {
    "text",
    "integer",
    "number",
    "boolean",
    "date",
    "time",
    "datetime",
    "enum",
    "user",
    "location",
    "url",
}
SEMANTIC_ROLES = {
    "",
    "schedule_title",
    "schedule_date",
    "schedule_start_time",
    "schedule_end_time",
    "schedule_start_at",
    "schedule_end_at",
    "schedule_status",
}
FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SchemaValidationError(ValueError):
    def __init__(self, message: str, field_errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.field_errors = field_errors or []


def validate_table_schema(schema: dict[str, Any]) -> dict[str, Any]:
    fields = schema.get("fields") or []
    if not fields:
        raise SchemaValidationError("记录表至少需要一个字段")
    if len(fields) > 100:
        raise SchemaValidationError("记录表最多支持 100 个字段")

    clean_fields: list[dict[str, Any]] = []
    keys: set[str] = set()
    roles: set[str] = set()
    for position, raw in enumerate(fields):
        field = _validate_field(raw, position)
        key = field["key"]
        if key in keys:
            raise SchemaValidationError(f"字段 key 重复：{key}")
        role = field["semantic_role"]
        if role and role in roles:
            raise SchemaValidationError(f"时间语义重复：{role}")
        keys.add(key)
        roles.add(role)
        clean_fields.append(field)

    time_config = _validate_time_config(schema.get("time_config") or {}, clean_fields)
    return {"fields": clean_fields, "time_config": time_config}


def _validate_field(raw: dict[str, Any], position: int) -> dict[str, Any]:
    key = str(raw.get("key") or "").strip()
    name = str(raw.get("name") or "").strip()
    field_type = str(raw.get("type") or "").strip()
    role = str(raw.get("semantic_role") or "").strip()
    if not FIELD_KEY_PATTERN.fullmatch(key):
        raise SchemaValidationError(f"字段 key 无效：{key or '(空)'}")
    if not name:
        raise SchemaValidationError(f"字段 {key} 缺少显示名称")
    if field_type not in FIELD_TYPES:
        raise SchemaValidationError(f"字段 {key} 的类型不受支持：{field_type}")
    if role not in SEMANTIC_ROLES:
        raise SchemaValidationError(f"字段 {key} 的时间语义不受支持：{role}")

    options = [str(item) for item in (raw.get("enum_options") or [])]
    if field_type == "enum" and not options:
        raise SchemaValidationError(f"枚举字段 {key} 必须提供 enum_options")
    if field_type != "enum" and options:
        raise SchemaValidationError(f"非枚举字段 {key} 不能提供 enum_options")

    aliases = list(dict.fromkeys(str(item).strip() for item in (raw.get("aliases") or []) if str(item).strip()))
    return {
        "key": key,
        "name": name,
        "type": field_type,
        "required": bool(raw.get("required", False)),
        "default_value": raw.get("default_value"),
        "description": str(raw.get("description") or "").strip(),
        "aliases": aliases,
        "enum_options": options,
        "minimum": raw.get("minimum"),
        "maximum": raw.get("maximum"),
        "semantic_role": role,
        "position": position,
        "status": str(raw.get("status") or "active"),
    }


def _validate_time_config(config: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    if not config:
        return {}
    by_key = {field["key"]: field for field in fields if field["status"] == "active"}
    clean: dict[str, Any] = {"timezone": str(config.get("timezone") or "Asia/Shanghai")}
    try:
        ZoneInfo(clean["timezone"])
    except Exception as exc:
        raise SchemaValidationError(f"无效时区：{clean['timezone']}") from exc

    for anchor in ("start", "end"):
        value = config.get(anchor)
        if not value:
            continue
        datetime_field = str(value.get("datetime_field") or "")
        date_field = str(value.get("date_field") or "")
        time_field = str(value.get("time_field") or "")
        if datetime_field:
            if date_field or time_field:
                raise SchemaValidationError(f"{anchor} 时间锚点不能同时使用 datetime 和日期/时间字段")
            _require_field_type(by_key, datetime_field, {"datetime"})
            clean[anchor] = {"datetime_field": datetime_field}
        else:
            if not date_field or not time_field:
                raise SchemaValidationError(f"{anchor} 时间锚点必须同时提供 date_field 和 time_field")
            _require_field_type(by_key, date_field, {"date"})
            _require_field_type(by_key, time_field, {"time"})
            clean[anchor] = {"date_field": date_field, "time_field": time_field}
    if len(clean) == 1:
        raise SchemaValidationError("time_config 至少需要 start 或 end 时间锚点")
    return clean


def _require_field_type(fields: dict[str, dict[str, Any]], key: str, allowed: set[str]) -> None:
    field = fields.get(key)
    if not field:
        raise SchemaValidationError(f"时间锚点字段不存在：{key}")
    if field["type"] not in allowed:
        raise SchemaValidationError(f"字段 {key} 不能用作该时间锚点")


def validate_record_values(
    schema: dict[str, Any],
    values: dict[str, Any],
    *,
    partial: bool = False,
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    fields = {field["key"]: field for field in schema["fields"] if field.get("status") == "active"}
    errors: list[dict[str, str]] = []
    clean: dict[str, Any] = {}
    for key in values:
        if key not in fields:
            errors.append(_field_error(key, "UNKNOWN_FIELD", "字段不存在或已停用"))
    for key, field in fields.items():
        if key not in values:
            if not partial and field.get("default_value") is not None:
                clean[key] = field["default_value"]
            continue
        value = values[key]
        if value is None or value == "":
            clean[key] = None
            continue
        try:
            clean[key] = _normalize_value(field, value)
        except (TypeError, ValueError) as exc:
            errors.append(_field_error(key, "INVALID_VALUE", str(exc)))

    merged = clean if not partial else {**values, **clean}
    missing = [
        field["key"]
        for field in fields.values()
        if field.get("required") and merged.get(field["key"]) in (None, "")
    ]
    return clean, missing, errors


def public_schema_view(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "fields": [copy.deepcopy(field) for field in schema.get("fields", []) if field.get("status") == "active"],
        "time_config": copy.deepcopy(schema.get("time_config") or {}),
    }


def _normalize_value(field: dict[str, Any], value: Any) -> Any:
    field_type = field["type"]
    if field_type in {"text", "user", "location"}:
        normalized = str(value).strip()
    elif field_type == "url":
        normalized = str(value).strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是有效的 HTTP(S) URL")
    elif field_type == "integer":
        if isinstance(value, bool):
            raise ValueError("必须是整数")
        normalized = int(value)
    elif field_type == "number":
        if isinstance(value, bool):
            raise ValueError("必须是数字")
        normalized = float(value)
    elif field_type == "boolean":
        normalized = _normalize_boolean(value)
    elif field_type == "date":
        normalized = date.fromisoformat(str(value)).isoformat()
    elif field_type == "time":
        normalized = time.fromisoformat(str(value)).strftime("%H:%M")
    elif field_type == "datetime":
        normalized = datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat(timespec="minutes")
    elif field_type == "enum":
        normalized = str(value)
        if normalized not in field["enum_options"]:
            raise ValueError(f"必须是以下选项之一：{', '.join(field['enum_options'])}")
    else:  # pragma: no cover - schema validation prevents this
        raise ValueError("不支持的字段类型")

    minimum = field.get("minimum")
    maximum = field.get("maximum")
    if minimum is not None and isinstance(normalized, (int, float)) and normalized < minimum:
        raise ValueError(f"必须大于等于 {minimum}")
    if maximum is not None and isinstance(normalized, (int, float)) and normalized > maximum:
        raise ValueError(f"必须小于等于 {maximum}")
    return normalized


def _normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "是", "需要"}:
        return True
    if text in {"false", "0", "no", "否", "不需要"}:
        return False
    raise ValueError("必须是布尔值")


def resolve_record_anchor(table: dict[str, Any], record: dict[str, Any], anchor: str) -> datetime:
    config = table.get("schema", {}).get("time_config") or {}
    anchor_key = "start" if anchor == "schedule_start" else "end" if anchor == "schedule_end" else ""
    if not anchor_key or anchor_key not in config:
        raise SchemaValidationError(f"记录表未配置时间锚点：{anchor}")
    anchor_config = config[anchor_key]
    values = record["values"]
    timezone = ZoneInfo(config.get("timezone") or "Asia/Shanghai")
    if anchor_config.get("datetime_field"):
        raw = values.get(anchor_config["datetime_field"])
        if not raw:
            raise SchemaValidationError("记录缺少提醒所需的日期时间值")
        result = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone) if result.tzinfo is None else result
    date_value = values.get(anchor_config["date_field"])
    time_value = values.get(anchor_config["time_field"])
    if not date_value or not time_value:
        raise SchemaValidationError("记录缺少提醒所需的日期或时间值")
    return datetime.combine(date.fromisoformat(str(date_value)), time.fromisoformat(str(time_value)), timezone)


def _field_error(field_key: str, code: str, message: str) -> dict[str, str]:
    return {"field_key": field_key, "code": code, "message": message}
