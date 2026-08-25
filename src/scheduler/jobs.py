from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.records.store import store

logger = logging.getLogger(__name__)
RETRY_DELAYS_MINUTES = (1, 5, 15)


def reminder_sender_job() -> None:
    from src.bot.wecom_bot import push_message_to_scope

    now = datetime.now(UTC).replace(microsecond=0)
    lease_until = now + timedelta(minutes=2)
    deliveries = store.claim_due_deliveries(_iso(now), _iso(lease_until), limit=50)
    for delivery in deliveries:
        try:
            ok = push_message_to_scope(delivery["scope_type"], delivery["scope_id"], delivery["content"])
            if ok:
                store.mark_delivery_sent(delivery["delivery_id"], delivery["reminder_id"])
                store.add_audit("reminder", delivery["reminder_id"], "sent", "scheduler", delivery["scope_type"], delivery["scope_id"], after={"delivery_id": delivery["delivery_id"]})
                continue
            _record_failure(delivery, "企业微信主动推送失败", now)
        except Exception as exc:
            logger.error("提醒发送失败: reminder_id=%s error=%s", delivery["reminder_id"], exc)
            _record_failure(delivery, str(exc), now)


def _record_failure(delivery: dict, error: str, now: datetime) -> None:
    attempt = int(delivery["attempt_count"])
    retry_at = None
    if attempt <= len(RETRY_DELAYS_MINUTES):
        retry_at = _iso(now + timedelta(minutes=RETRY_DELAYS_MINUTES[attempt - 1]))
    store.mark_delivery_failed(delivery["delivery_id"], error, retry_at)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
