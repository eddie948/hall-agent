import asyncio
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.agent import brain
from src.agent.brain import AGENT_FAILURE_MESSAGE
from src.agent.capabilities import build_record_management_capability, build_reminder_management_capability, create_record, list_reminders
from src.agent.history import GROUP_HISTORY_USER_ID, json_items_to_messages
from src.agent.llm_client import AgentDeps, AgentUnavailable, build_agent_deps, build_runtime_prompt
from src.agent.schemas import CreateRecordInput, ReminderListQuery
from src.domain.fixed_reception_table import FIXED_RECEPTION_SCHEMA, FIXED_RECEPTION_TABLE_NAME
from src.domain.table_schema import validate_table_schema
from src.records.store import RecordStore


class FakeRunResult:
    output = "已处理"
    conversation_id = "conv1"

    def new_messages(self):
        return [
            ModelRequest(parts=[UserPromptPart(content="创建访问日程表")]),
            ModelResponse(parts=[TextPart(content="已处理")]),
        ]


def _deps(tmp_path: Path, chat_type: str = "group") -> AgentDeps:
    record_store = RecordStore(tmp_path / "agent.db")
    record_store.init_db()
    return build_agent_deps("u1", "g1" if chat_type == "group" else "", chat_type, "m1", "2026-07-16 18:00", record_store)


def test_runtime_prompt_contains_scope_and_time(tmp_path: Path):
    prompt = build_runtime_prompt("登记客户到访", _deps(tmp_path))
    assert "当前时间（Asia/Shanghai）：2026-07-16 18:00" in prompt
    assert "当前数据空间：当前群（group:g1）" in prompt
    assert "当前空间固定接待表摘要：" in prompt
    assert FIXED_RECEPTION_TABLE_NAME in prompt
    assert prompt.endswith("用户消息：\n登记客户到访")


def test_build_agent_deps_auto_provisions_fixed_table(tmp_path: Path):
    deps = _deps(tmp_path)
    tables = deps.table_service.list_tables(deps.actor)["data"]["tables"]
    assert len(tables) == 1
    assert tables[0]["name"] == FIXED_RECEPTION_TABLE_NAME
    assert {"到访日期", "到访开始时间", "来访单位及主要来访人", "内部陪同人"} <= set(tables[0]["field_names"])
    assert "主来访人及职务" not in set(tables[0]["field_names"])

    table = deps.table_service.get_table(deps.actor, tables[0]["table_id"])["data"]["table"]
    policy = deps.reminder_service.get_policy(deps.actor, table["table_id"])["data"]["policy"]
    assert table["schema"]["time_config"]["start"] == {"date_field": "visit_date", "time_field": "visit_start_time"}
    assert policy["rules"][0]["offset_minutes"] == -120


def test_existing_fixed_table_schema_is_updated(tmp_path: Path):
    deps = _deps(tmp_path)
    table_summary = deps.table_service.list_tables(deps.actor)["data"]["tables"][0]
    table = deps.table_service.get_table(deps.actor, table_summary["table_id"])["data"]["table"]
    old_schema = validate_table_schema({
        **FIXED_RECEPTION_SCHEMA,
        "fields": [
            {
                **field,
                **({"type": "enum", "enum_options": ["深圳福田", "上海张江", "北京"]} if field["key"] == "reception_location" else {}),
                **({"name": "来访单位", "aliases": []} if field["key"] == "visiting_unit" else {}),
                **({"required": True} if field["key"] in {"visitor_count", "internal_hosts"} else {}),
                **({"type": "user"} if field["key"] == "internal_hosts" else {}),
            }
            for field in FIXED_RECEPTION_SCHEMA["fields"]
        ] + [{
            "key": "main_visitor",
            "name": "主来访人及职务",
            "type": "text",
            "required": True,
            "default_value": None,
            "description": "",
            "aliases": [],
            "enum_options": [],
            "minimum": None,
            "maximum": None,
            "semantic_role": "",
        }],
    })
    deps.table_service.store.update_table(
        table["table_id"],
        table["schema_version"],
        {**table, "schema": old_schema},
        deps.actor.actor_user_id,
    )

    updated = deps.table_service.ensure_fixed_table(deps.actor)
    fields = {field["key"]: field for field in updated["schema"]["fields"]}

    assert fields["reception_location"]["type"] == "text"
    assert fields["visiting_unit"]["name"] == "来访单位及主要来访人"
    assert fields["visitor_count"]["required"] is False
    assert fields["internal_hosts"]["type"] == "text"
    assert fields["internal_hosts"]["required"] is False
    assert fields["main_visitor"]["status"] == "deprecated"

    public_table = deps.table_service.get_table(deps.actor, updated["table_id"])["data"]["table"]
    public_field_keys = {field["key"] for field in public_table["schema"]["fields"]}
    assert "main_visitor" not in public_field_keys
    assert "visiting_unit" in public_field_keys


def test_capabilities_hide_dynamic_table_and_reminder_write_tools():
    record_tool_names = {tool.name for tool in build_record_management_capability().tools}
    reminder_tool_names = {tool.name for tool in build_reminder_management_capability().tools}

    assert "create_record_table" not in record_tool_names
    assert "update_record_table" not in record_tool_names
    assert "archive_record_table" not in record_tool_names
    assert {"list_record_tables", "get_record_table", "create_record", "confirm_record"} <= record_tool_names
    assert {"set_table_reminder_policy", "clear_table_reminder_policy", "create_reminder", "update_reminder", "cancel_reminder"}.isdisjoint(reminder_tool_names)
    assert {"list_reminders", "get_reminder"}.isdisjoint(reminder_tool_names)


def test_fixed_table_record_generates_two_hour_reminder(tmp_path: Path):
    deps = _deps(tmp_path)
    ctx = SimpleNamespace(deps=deps)
    table_id = deps.table_service.list_tables(deps.actor)["data"]["tables"][0]["table_id"]
    draft = asyncio.run(create_record(ctx, CreateRecordInput(
        table_id=table_id,
        values={
            "visit_date": "2030-01-01",
            "visit_start_time": "10:00",
            "visit_end_time": "11:00",
            "visiting_unit": "腾讯科技，张三 总监",
            "exhibition_hall": "是",
        },
    )))
    record = draft["data"]["record"]
    assert record["status"] == "pending_confirm"

    confirmed = deps.record_service.confirm_record(deps.actor, record["record_id"], record["version"])
    reminders = asyncio.run(list_reminders(ctx, ReminderListQuery(record_id=record["record_id"])))["data"]["reminders"]

    assert confirmed["success"] is True
    assert len(reminders) == 1
    assert reminders[0]["source_type"] == "table_policy"
    assert reminders[0]["next_fire_at"] == "2030-01-01T00:00:00Z"
    assert reminders[0]["content"] == "【到访提醒】腾讯科技客户还有2小时来访（2030-01-01 10:00），请做好接待准备。"


def test_runtime_prompt_refreshes_and_includes_unfinished_records(tmp_path: Path):
    record_store = RecordStore(tmp_path / "agent.db")
    record_store.init_db()
    deps = build_agent_deps("u1", "g1", "group", "m1", "2030-01-01 10:30", record_store)
    table_id = deps.table_service.list_tables(deps.actor)["data"]["tables"][0]["table_id"]
    created = deps.record_service.create_record(deps.actor, table_id, {
        "visit_date": "2030-01-01",
        "visit_start_time": "10:00",
        "visit_end_time": "11:00",
        "visiting_unit": "腾讯科技，张三 总监",
        "exhibition_hall": "是",
    })["data"]["record"]
    deps.record_service.confirm_record(deps.actor, created["record_id"], created["version"])

    prompt = build_runtime_prompt("现在有哪些未完成接待", deps)
    record = deps.record_service.get_record(deps.actor, created["record_id"])["data"]["record"]

    assert "当前未完成接待记录快照：" in prompt
    assert "腾讯科技" in prompt
    assert "接待状态=接待中" in prompt
    assert f"record_id={created['record_id']}" in prompt
    assert record["values"]["reception_status"] == "接待中"


def test_run_agent_uses_scope_history_and_message_cache(monkeypatch, tmp_path: Path):
    test_store = RecordStore(tmp_path / "brain.db")
    calls = []

    async def fake_loop(content: str, deps: AgentDeps, message_history=None):
        calls.append((content, deps, message_history))
        return FakeRunResult()

    monkeypatch.setattr(brain, "store", test_store)
    monkeypatch.setattr(brain, "run_agent_loop", fake_loop)
    first = asyncio.run(brain.run_agent("创建访问日程表", "u1", "g1", "group", "m1"))
    cached = asyncio.run(brain.run_agent("不会执行", "u1", "g1", "group", "m1"))

    assert first == cached == "已处理"
    assert len(calls) == 1
    assert calls[0][1].actor.scope_type == "group"
    assert calls[0][1].actor.scope_id == "g1"
    assert test_store.is_conversation_reachable("group", "g1") is True
    assert test_store.list_conversation_message_json("g1", "u1") == []
    assert len(test_store.list_conversation_message_json("g1", GROUP_HISTORY_USER_ID)) == 2


def test_run_agent_shares_group_history_across_speakers(monkeypatch, tmp_path: Path):
    test_store = RecordStore(tmp_path / "brain.db")
    calls = []

    async def fake_loop(content: str, deps: AgentDeps, message_history=None):
        calls.append((content, deps, message_history))
        return FakeRunResult()

    monkeypatch.setattr(brain, "store", test_store)
    monkeypatch.setattr(brain, "run_agent_loop", fake_loop)
    asyncio.run(brain.run_agent("下周二腾讯来访", "u1", "g1", "group", "m1"))
    asyncio.run(brain.run_agent("主来访人是张三", "u2", "g1", "group", "m2"))

    shared_messages = json_items_to_messages(test_store.list_conversation_message_json("g1", GROUP_HISTORY_USER_ID))

    assert len(calls[0][2]) == 0
    assert len(calls[1][2]) == 2
    assert shared_messages[0].parts[0].content == "[u1] 下周二腾讯来访"
    assert shared_messages[2].parts[0].content == "[u2] 主来访人是张三"


def test_run_agent_saves_raw_user_message_without_runtime_snapshot(monkeypatch, tmp_path: Path):
    test_store = RecordStore(tmp_path / "brain.db")

    class RuntimePromptResult:
        output = "已处理"
        conversation_id = "conv1"

        def __init__(self, prompt: str) -> None:
            self.prompt = prompt

        def new_messages(self):
            return [
                ModelRequest(parts=[UserPromptPart(content=self.prompt)]),
                ModelResponse(parts=[TextPart(content="已处理")]),
            ]

    async def fake_loop(content: str, deps: AgentDeps, message_history=None):
        return RuntimePromptResult(build_runtime_prompt(content, deps))

    monkeypatch.setattr(brain, "store", test_store)
    monkeypatch.setattr(brain, "run_agent_loop", fake_loop)
    asyncio.run(brain.run_agent("现在有哪些未完成接待", "u1", "g1", "group", "m1"))

    messages = json_items_to_messages(test_store.list_conversation_message_json("g1", GROUP_HISTORY_USER_ID))

    assert messages[0].parts[0].content == "[u1] 现在有哪些未完成接待"
    assert "当前未完成接待记录快照" not in messages[0].parts[0].content


def test_run_agent_returns_failure_when_model_unavailable(monkeypatch, tmp_path: Path):
    test_store = RecordStore(tmp_path / "brain.db")

    async def fake_loop(content: str, deps: AgentDeps, message_history=None):
        raise AgentUnavailable("未配置模型")

    monkeypatch.setattr(brain, "store", test_store)
    monkeypatch.setattr(brain, "run_agent_loop", fake_loop)
    reply = asyncio.run(brain.run_agent("你好", "u1", "", "single", "m2"))
    assert reply == AGENT_FAILURE_MESSAGE
    assert test_store.is_conversation_reachable("user", "u1") is True
