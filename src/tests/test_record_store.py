from pathlib import Path
import json
from datetime import datetime

import httpx
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.agent.history import json_items_to_messages, messages_to_json_items
from src.agent.model_input_snapshot import capture_model_input_snapshot, model_input_snapshot_context, write_model_input_snapshot
from src.records.store import RecordStore


def test_store_initializes_only_current_business_tables(tmp_path: Path):
    store = RecordStore(tmp_path / "test.db")
    store.init_db()
    with store.connect() as conn:
        names = {row["name"] for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
    assert {"record_tables", "record_table_versions", "records", "table_reminder_policies", "reminders", "reminder_deliveries"} <= names
    assert "visit_records" not in names
    assert "cloud_sync_events" not in names
    assert "reminder_tasks" not in names


def test_store_conversation_messages_round_trip(tmp_path: Path):
    store = RecordStore(tmp_path / "test.db")
    store.init_db()
    messages = [ModelRequest(parts=[UserPromptPart(content="创建表")]), ModelResponse(parts=[TextPart(content="请确认")])]
    store.save_conversation_message_json("g1", "u1", "conv1", messages_to_json_items(messages), "m1")
    loaded = json_items_to_messages(store.list_conversation_message_json("g1", "u1"))
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "创建表"


def test_clear_conversation_history(tmp_path: Path):
    store = RecordStore(tmp_path / "test.db")
    store.init_db()
    messages = [ModelRequest(parts=[UserPromptPart(content="创建表")]), ModelResponse(parts=[TextPart(content="请确认")])]
    store.save_conversation_message_json("g1", "u1", "conv1", messages_to_json_items(messages), "m1")
    store.save_message_result("m1", "g1", "u1", "已处理")

    counts = store.clear_conversation_history()

    assert counts == {"conversation_messages": 2, "message_results": 1}
    assert store.list_conversation_message_json("g1", "u1") == []
    assert store.get_message_result("m1") is None


def test_write_model_input_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.config.settings.model_input_snapshot_dir", tmp_path / "snapshots")
    monkeypatch.setattr("src.config.settings.scheduler_timezone", "Asia/Shanghai")

    class FixedDatetime:
        @classmethod
        def now(cls, timezone):
            return datetime(2026, 7, 16, 14, 5, 9, 123456, tzinfo=timezone)

    monkeypatch.setattr("src.agent.model_input_snapshot.datetime", FixedDatetime)

    class D:
        chat_id = "g1"
        message_id = "m1"

    raw_input = b'{"model":"qwen3-max","messages":[{"role":"user","content":"hello"}]}'
    with model_input_snapshot_context(D()):
        path = write_model_input_snapshot(raw_input)
    assert "_g1_m1_" in path.name
    assert json.loads(path.read_bytes()) == json.loads(raw_input)


def test_capture_model_input_snapshot_only_for_chat_completions(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.config.settings.model_input_snapshot_dir", tmp_path / "snapshots")
    request = httpx.Request("POST", "https://example.test/v1/chat/completions", content=b'{"messages":[]}')
    import asyncio
    asyncio.run(capture_model_input_snapshot(request))
    assert len(list((tmp_path / "snapshots").iterdir())) == 1
