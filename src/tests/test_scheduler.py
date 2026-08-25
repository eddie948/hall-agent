from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.domain.context import ActorContext, Scope
from src.records.store import RecordStore
from src.services import ReminderService


def test_delivery_claim_is_idempotent(tmp_path: Path):
    store = RecordStore(tmp_path / "scheduler.db")
    store.init_db()
    service = ReminderService(store)
    actor = ActorContext("u1", Scope("user", "u1"), "single", "m1")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    result = service.create_reminder(actor, "测试", {"type": "absolute", "at": past})
    assert result["error_code"] == "REMINDER_TIME_IN_PAST"

    future = (datetime.now(UTC) + timedelta(minutes=5)).replace(microsecond=0)
    result = service.create_reminder(actor, "测试", {"type": "absolute", "at": future.isoformat()})
    reminder = result["data"]["reminder"]
    claimed = store.claim_due_deliveries((future + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"), (future + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"))
    claimed_again = store.claim_due_deliveries((future + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"), (future + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"))
    assert len(claimed) == 1
    assert claimed[0]["reminder_id"] == reminder["reminder_id"]
    assert claimed_again == []
