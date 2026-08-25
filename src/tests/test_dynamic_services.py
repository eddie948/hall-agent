from pathlib import Path

from src.domain.context import ActorContext, Scope
from src.records.store import RecordStore
from src.services import RecordService, ReminderService, TableService


def _actor(user="u1", scope_type="group", scope_id="g1"):
    return ActorContext(user, Scope(scope_type, scope_id), scope_type, "m1")


def _services(tmp_path: Path):
    store = RecordStore(tmp_path / "service.db")
    store.init_db()
    reminders = ReminderService(store)
    return store, TableService(store), RecordService(store, reminders), reminders


def _create_schedule_table(tables: TableService, actor: ActorContext, creation_policy="confirm"):
    result = tables.create_table(actor, {
        "name": "公司访问日程",
        "description": "访问记录样例",
        "creation_policy": creation_policy,
        "fields": [
            {"key": "title", "name": "标题", "type": "text", "required": True, "semantic_role": "schedule_title"},
            {"key": "start_at", "name": "开始时间", "type": "datetime", "required": True, "semantic_role": "schedule_start_at"},
            {"key": "visitor_count", "name": "人数", "type": "integer", "minimum": 1},
        ],
        "time_config": {"timezone": "Asia/Shanghai", "start": {"datetime_field": "start_at"}},
    })
    assert result["success"] is True
    return result["data"]["table"]


def test_dynamic_schema_draft_confirm_and_automatic_reminders(tmp_path: Path):
    _, tables, records, reminders = _services(tmp_path)
    actor = _actor()
    table = _create_schedule_table(tables, actor)
    policy = reminders.set_policy(actor, table["table_id"], [
        {"type": "day_before", "anchor": "schedule_start", "days_before": 1, "at_time": "18:00"},
        {"type": "relative_to_record", "anchor": "schedule_start", "offset_minutes": -60},
    ], None)
    assert policy["success"] is True

    draft = records.create_record(actor, table["table_id"], {"start_at": "2030-07-20T10:00:00+08:00"})
    assert draft["data"]["record"]["status"] == "draft"
    assert draft["data"]["missing_fields"] == ["title"]

    pending = records.create_record(actor, table["table_id"], {"title": "客户来访", "visitor_count": 3})
    assert pending["data"]["record"]["status"] == "pending_confirm"
    record = pending["data"]["record"]
    confirmed = records.confirm_record(actor, record["record_id"], record["version"])
    assert confirmed["success"] is True
    generated = reminders.list_reminders(actor, record_id=record["record_id"])["data"]["reminders"]
    assert len(generated) == 2
    assert {item["source_type"] for item in generated} == {"table_policy"}
    assert {item["next_fire_at"] for item in generated} == {"2030-07-19T10:00:00Z", "2030-07-20T01:00:00Z"}


def test_record_update_recalculates_and_cancel_stops_reminders(tmp_path: Path):
    _, tables, records, reminders = _services(tmp_path)
    actor = _actor()
    table = _create_schedule_table(tables, actor, "immediate")
    reminders.set_policy(actor, table["table_id"], [{"type": "relative_to_record", "offset_minutes": -60}], None)
    created = records.create_record(actor, table["table_id"], {"title": "访问", "start_at": "2030-01-01T10:00:00+08:00"})["data"]["record"]
    first = reminders.list_reminders(actor, record_id=created["record_id"])["data"]["reminders"][0]
    assert first["next_fire_at"] == "2030-01-01T01:00:00Z"

    updated = records.update_record(actor, created["record_id"], created["version"], {"start_at": "2030-01-01T12:00:00+08:00"})["data"]["record"]
    second = reminders.list_reminders(actor, record_id=created["record_id"])["data"]["reminders"][0]
    assert second["reminder_id"] == first["reminder_id"]
    assert second["next_fire_at"] == "2030-01-01T03:00:00Z"

    cancelled = records.cancel_record(actor, updated["record_id"], updated["version"])
    assert cancelled["success"] is True
    assert reminders.list_reminders(actor, ["active"], record_id=created["record_id"])["data"]["reminders"] == []


def test_policy_apply_all_active_and_clear_does_not_cancel_manual_reminder(tmp_path: Path):
    _, tables, records, reminders = _services(tmp_path)
    actor = _actor()
    table = _create_schedule_table(tables, actor, "immediate")
    record = records.create_record(actor, table["table_id"], {"title": "访问", "start_at": "2030-01-01T10:00:00+08:00", "visitor_count": 2})["data"]["record"]
    manual = reminders.create_reminder(actor, "手工提醒", {"type": "relative_to_record", "anchor": "schedule_start", "offset_minutes": -30}, record["record_id"])
    policy = reminders.set_policy(actor, table["table_id"], [{"type": "relative_to_record", "offset_minutes": -60}], None, "all_active_records")
    active = reminders.list_reminders(actor, ["active"], record["record_id"])["data"]["reminders"]
    assert len(active) == 2

    cleared = reminders.clear_policy(actor, table["table_id"], policy["data"]["policy"]["version"])
    remaining = reminders.list_reminders(actor, ["active"], record["record_id"])["data"]["reminders"]
    assert cleared["success"] is True
    assert [item["reminder_id"] for item in remaining] == [manual["data"]["reminder"]["reminder_id"]]


def test_scope_isolation_and_group_schema_permissions(tmp_path: Path):
    _, tables, records, _ = _services(tmp_path)
    creator = _actor("u1", "group", "g1")
    member = _actor("u2", "group", "g1")
    outsider = _actor("u1", "group", "g2")
    table = _create_schedule_table(tables, creator, "immediate")

    member_record = records.create_record(member, table["table_id"], {"title": "成员创建", "start_at": "2030-01-01T10:00:00+08:00"})
    denied_schema = tables.update_table(member, table["table_id"], table["schema_version"], [{"action": "update_description", "description": "x"}])
    hidden = tables.get_table(outsider, table["table_id"])

    assert member_record["success"] is True
    assert denied_schema["error_code"] == "PERMISSION_DENIED"
    assert hidden["error_code"] == "RESOURCE_NOT_FOUND"


def test_schema_validation_and_record_version_conflict(tmp_path: Path):
    _, tables, records, _ = _services(tmp_path)
    actor = _actor(scope_type="user", scope_id="u1")
    invalid = tables.create_table(actor, {"name": "坏表", "fields": [{"key": "中文", "name": "字段", "type": "text"}]})
    assert invalid["error_code"] == "VALIDATION_FAILED"

    table = _create_schedule_table(tables, actor, "immediate")
    bad_record = records.create_record(actor, table["table_id"], {"title": "访问", "start_at": "2030-01-01T10:00:00+08:00", "visitor_count": 0})
    assert bad_record["error_code"] == "VALIDATION_FAILED"

    record = records.create_record(actor, table["table_id"], {"title": "访问", "start_at": "2030-01-01T10:00:00+08:00", "visitor_count": 2})["data"]["record"]
    records.update_record(actor, record["record_id"], record["version"], {"title": "新标题"})
    conflict = records.update_record(actor, record["record_id"], record["version"], {"title": "旧版本覆盖"})
    assert conflict["error_code"] == "RECORD_VERSION_CONFLICT"

    invalid_filter = records.list_records(actor, table["table_id"], filters=[{"field_key": "visitor_count", "operator": "gt", "value": "不是数字"}])
    assert invalid_filter["error_code"] == "INVALID_FILTER"
