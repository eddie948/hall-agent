from __future__ import annotations

from datetime import UTC, datetime


TABLE_STATUSES = ("active", "archived")
RECORD_STATUSES = ("draft", "pending_confirm", "active", "cancelled")
REMINDER_STATUSES = ("active", "sent", "cancelled", "failed")
DELIVERY_STATUSES = ("pending", "processing", "sent", "failed", "cancelled")


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
