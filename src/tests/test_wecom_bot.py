import asyncio

from src.bot import wecom_bot
from src import main as app_main


def test_mirror_group_ids_for_group_mapping(monkeypatch):
    monkeypatch.setattr(
        wecom_bot.settings,
        "wecom_mirror_groups",
        '{"g1":["g2","g3","g2","g1"],"g2":"g4"}',
    )

    assert wecom_bot._mirror_scopes_for("group", "g1") == [("group", "g2"), ("group", "g3")]
    assert wecom_bot._mirror_scopes_for("group", "g2") == [("group", "g4")]


def test_mirror_group_ids_ignores_non_group_and_invalid_json(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_mirror_groups", '{"g1":["g2"]}')
    assert wecom_bot._mirror_scopes_for("user", "u1") == []

    monkeypatch.setattr(wecom_bot.settings, "wecom_mirror_groups", "{bad")
    assert wecom_bot._mirror_scopes_for("group", "g1") == []


def test_mirror_scopes_support_users_and_groups(monkeypatch):
    monkeypatch.setattr(
        wecom_bot.settings,
        "wecom_mirror_scopes",
        '{"user:u1":["group:g1","user:u2"],"group:g1":["user:u3","group:g2"]}',
    )

    assert wecom_bot._mirror_scopes_for("user", "u1") == [("group", "g1"), ("user", "u2")]
    assert wecom_bot._mirror_scopes_for("group", "g1") == [("user", "u3"), ("group", "g2")]


def test_recent_chat_observations_are_recorded_and_filtered():
    wecom_bot._record_chat_observation("group", "g1", "u1", "m1", "hello")
    wecom_bot._record_chat_observation("single", "u2", "u2", "m2", "hi")

    groups = wecom_bot.list_recent_chat_observations(limit=10, chat_type="group")
    all_items = wecom_bot.list_recent_chat_observations(limit=10, chat_type=None)

    assert groups[0]["chat_id"] == "g1"
    assert all_items[0]["chat_id"] == "u2"


def test_admin_wecom_chatids_endpoint(monkeypatch):
    monkeypatch.setattr(app_main.settings, "admin_init_token", "secret")
    monkeypatch.setattr(
        "src.bot.wecom_bot.list_recent_chat_observations",
        lambda limit, chat_type: [{"chat_id": "g1", "chat_type": "group"}],
    )

    result = asyncio.run(app_main.list_wecom_chatids(x_admin_token="secret", limit=1, chat_type="group"))
    assert result["ok"] is True
    assert result["items"][0]["chat_id"] == "g1"
