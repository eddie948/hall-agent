from __future__ import annotations

import re
from typing import Any


FIXED_RECEPTION_TABLE_NAME = "外部客户到访登记与接待报备表"
FIXED_RECEPTION_TABLE_DESCRIPTION = (
    "记录外部客户到访日程，保证无人员、地点、时间冲突。"
)
FIXED_RECEPTION_REMINDER_RULE_ID = "RULE_FIXED_RECEPTION_2H"
FIXED_RECEPTION_REMINDER_CONTENT = "【到访提醒】客户即将到访，请做好接待准备。"
RECEPTION_STATUS_FIELD_KEY = "reception_status"
RECEPTION_STATUS_PENDING = "待接待"
RECEPTION_STATUS_IN_PROGRESS = "接待中"
RECEPTION_STATUS_COMPLETED = "已完成"
DEPRECATED_FIXED_RECEPTION_FIELD_KEYS = {"main_visitor"}


FIXED_RECEPTION_SCHEMA: dict[str, Any] = {
    "fields": [
        {
            "key": "reception_location",
            "name": "接待地点",
            "type": "text",
            "required": False,
            "default_value": "深圳福田",
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "visit_date",
            "name": "到访日期",
            "type": "date",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "schedule_date",
        },
        {
            "key": "visit_start_time",
            "name": "到访开始时间",
            "type": "time",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "schedule_start_time",
        },
        {
            "key": "visit_end_time",
            "name": "到访结束时间",
            "type": "time",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "schedule_end_time",
        },
        {
            "key": "visiting_unit",
            "name": "来访单位及主要来访人",
            "type": "text",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": ["来访单位", "主来访人及职务", "主要来访人"],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "visitor_count",
            "name": "来访人数",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "host_department",
            "name": "陪同部门",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "exhibition_hall",
            "name": "展厅接待",
            "type": "enum",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": ["是", "否"],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "meeting_room",
            "name": "预订会议室",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "supporting_materials",
            "name": "配套物料",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "other_requirements",
            "name": "其他需求",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "exhibition_status",
            "name": "展厅准备状态",
            "type": "enum",
            "required": False,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": ["无需展厅", "待准备", "准备中", "准备就绪"],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
        {
            "key": "reception_status",
            "name": "接待状态",
            "type": "enum",
            "required": False,
            "default_value": RECEPTION_STATUS_PENDING,
            "description": "根据到访开始和结束时间自动更新的接待流程状态",
            "aliases": [],
            "enum_options": [RECEPTION_STATUS_PENDING, RECEPTION_STATUS_IN_PROGRESS, RECEPTION_STATUS_COMPLETED],
            "minimum": None,
            "maximum": None,
            "semantic_role": "schedule_status",
        },
        {
            "key": "internal_hosts",
            "name": "内部陪同人",
            "type": "text",
            "required": False,
            "default_value": None,
            "description": "支持多位内部陪同人",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        },
    ],
    "time_config": {
        "timezone": "Asia/Shanghai",
        "start": {"date_field": "visit_date", "time_field": "visit_start_time"},
        "end": {"date_field": "visit_date", "time_field": "visit_end_time"},
    },
}


FIXED_RECEPTION_REMINDER_RULES: list[dict[str, Any]] = [
    {
        "type": "relative_to_record",
        "anchor": "schedule_start",
        "content": FIXED_RECEPTION_REMINDER_CONTENT,
        "offset_minutes": -120,
        "rule_id": FIXED_RECEPTION_REMINDER_RULE_ID,
    }
]


def build_fixed_reception_reminder_content(record: dict[str, Any]) -> str:
    values = record.get("values") or {}
    visiting_unit = str(values.get("visiting_unit") or "").strip()
    company = _extract_company_name(visiting_unit) or visiting_unit or "客户"
    if not company.endswith("客户"):
        company = f"{company}客户"
    visit_date = str(values.get("visit_date") or "").strip()
    visit_start_time = str(values.get("visit_start_time") or "").strip()
    schedule_text = " ".join(part for part in (visit_date, visit_start_time) if part)
    schedule_suffix = f"（{schedule_text}）" if schedule_text else ""
    return f"【到访提醒】{company}还有2小时来访{schedule_suffix}，请做好接待准备。"


def _extract_company_name(visiting_unit: str) -> str:
    if not visiting_unit:
        return ""
    parts = re.split(r"[，,、/|;；\s]+", visiting_unit.strip(), maxsplit=1)
    return parts[0].strip() if parts else visiting_unit.strip()
