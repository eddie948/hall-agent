from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from src.config import db_path
from src.records.schema import utcnow_iso


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class RecordStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or db_path()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys=on")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _connection(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
            return
        with self.connect() as own_conn:
            yield own_conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists record_tables (
                    table_id text primary key,
                    scope_type text not null check(scope_type in ('user','group')),
                    scope_id text not null,
                    name text not null,
                    description text not null default '',
                    creation_policy text not null check(creation_policy in ('immediate','confirm')),
                    schema_json text not null,
                    schema_version integer not null default 1,
                    status text not null default 'active' check(status in ('active','archived')),
                    created_by text not null,
                    created_at text not null,
                    updated_at text not null
                );
                create unique index if not exists idx_record_tables_active_name
                    on record_tables(scope_type, scope_id, name) where status='active';
                create index if not exists idx_record_tables_scope
                    on record_tables(scope_type, scope_id, status, updated_at);

                create table if not exists record_table_versions (
                    table_id text not null,
                    schema_version integer not null,
                    schema_json text not null,
                    created_by text not null,
                    created_at text not null,
                    primary key(table_id, schema_version),
                    foreign key(table_id) references record_tables(table_id)
                );

                create table if not exists records (
                    record_id text primary key,
                    table_id text not null,
                    scope_type text not null check(scope_type in ('user','group')),
                    scope_id text not null,
                    status text not null check(status in ('draft','pending_confirm','active','cancelled')),
                    values_json text not null default '{}',
                    schema_version integer not null,
                    version integer not null default 1,
                    raw_text text not null default '',
                    created_by text not null,
                    updated_by text not null,
                    created_at text not null,
                    updated_at text not null,
                    foreign key(table_id) references record_tables(table_id)
                );
                create index if not exists idx_records_scope_table
                    on records(scope_type, scope_id, table_id, status, updated_at);
                create unique index if not exists idx_records_actor_draft
                    on records(scope_type, scope_id, table_id, created_by)
                    where status in ('draft','pending_confirm');

                create table if not exists table_reminder_policies (
                    table_id text primary key,
                    scope_type text not null,
                    scope_id text not null,
                    rules_json text not null default '[]',
                    version integer not null default 1,
                    created_by text not null,
                    updated_by text not null,
                    created_at text not null,
                    updated_at text not null,
                    foreign key(table_id) references record_tables(table_id)
                );

                create table if not exists reminders (
                    reminder_id text primary key,
                    scope_type text not null check(scope_type in ('user','group')),
                    scope_id text not null,
                    record_id text,
                    content text not null,
                    trigger_type text not null check(trigger_type in ('absolute','relative_to_record')),
                    trigger_json text not null,
                    next_fire_at text not null,
                    timezone text not null,
                    source_type text not null check(source_type in ('manual','table_policy')),
                    source_rule_id text,
                    status text not null check(status in ('active','sent','cancelled','failed')),
                    version integer not null default 1,
                    created_by text not null,
                    updated_by text not null,
                    created_at text not null,
                    updated_at text not null,
                    foreign key(record_id) references records(record_id)
                );
                create index if not exists idx_reminders_scope
                    on reminders(scope_type, scope_id, status, next_fire_at);
                create index if not exists idx_reminders_record
                    on reminders(record_id, status);
                create unique index if not exists idx_reminders_policy_rule
                    on reminders(record_id, source_rule_id)
                    where source_type='table_policy' and status='active';

                create table if not exists reminder_deliveries (
                    delivery_id text primary key,
                    reminder_id text not null,
                    scheduled_at text not null,
                    claimed_at text,
                    lease_until text,
                    sent_at text,
                    status text not null check(status in ('pending','processing','sent','failed','cancelled')),
                    attempt_count integer not null default 0,
                    last_error text not null default '',
                    idempotency_key text not null unique,
                    created_at text not null,
                    updated_at text not null,
                    foreign key(reminder_id) references reminders(reminder_id)
                );
                create index if not exists idx_reminder_deliveries_due
                    on reminder_deliveries(status, scheduled_at, lease_until);

                create table if not exists reachable_conversations (
                    scope_type text not null,
                    scope_id text not null,
                    last_interacted_at text not null,
                    reachable integer not null default 1,
                    last_delivery_error text not null default '',
                    updated_at text not null,
                    primary key(scope_type, scope_id)
                );

                create table if not exists audit_events (
                    event_id text primary key,
                    resource_type text not null,
                    resource_id text not null,
                    event_type text not null,
                    actor_user_id text not null,
                    scope_type text not null,
                    scope_id text not null,
                    before_json text,
                    after_json text,
                    created_at text not null
                );
                create index if not exists idx_audit_resource
                    on audit_events(resource_type, resource_id, created_at);

                create table if not exists message_results (
                    message_id text primary key,
                    chat_id text,
                    user_id text,
                    response_text text not null,
                    created_at text not null
                );

                create table if not exists conversation_messages (
                    message_id text primary key,
                    chat_id text not null,
                    user_id text not null,
                    conversation_id text,
                    message_json text not null,
                    created_at text not null
                );
                create index if not exists idx_conversation_messages_scope
                    on conversation_messages(chat_id, user_id, created_at);

                create table if not exists local_push_outbox (
                    outbox_id text primary key,
                    scope_type text not null,
                    scope_id text not null,
                    content text not null,
                    created_at text not null,
                    acknowledged_at text
                );
                create index if not exists idx_local_push_outbox_pending
                    on local_push_outbox(acknowledged_at, created_at);
                """
            )

    # Record tables

    def create_table(self, data: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utcnow_iso()
        payload = {
            "table_id": data.get("table_id") or new_id("TBL"),
            "scope_type": data["scope_type"],
            "scope_id": data["scope_id"],
            "name": data["name"],
            "description": data.get("description", ""),
            "creation_policy": data.get("creation_policy", "confirm"),
            "schema_json": _dump(data["schema"]),
            "schema_version": 1,
            "status": "active",
            "created_by": data["created_by"],
            "created_at": now,
            "updated_at": now,
        }
        with self._connection(conn) as db:
            db.execute(
                """insert into record_tables(
                    table_id, scope_type, scope_id, name, description, creation_policy,
                    schema_json, schema_version, status, created_by, created_at, updated_at
                ) values (
                    :table_id, :scope_type, :scope_id, :name, :description, :creation_policy,
                    :schema_json, :schema_version, :status, :created_by, :created_at, :updated_at
                )""",
                payload,
            )
            db.execute(
                "insert into record_table_versions(table_id, schema_version, schema_json, created_by, created_at) values (?,?,?,?,?)",
                (payload["table_id"], 1, payload["schema_json"], payload["created_by"], now),
            )
            return self.get_table(payload["table_id"], conn=db) or {}

    def get_table(self, table_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            return _table_row(db.execute("select * from record_tables where table_id=?", (table_id,)).fetchone())

    def list_tables(self, scope_type: str, scope_id: str, include_archived: bool = False, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        sql = "select * from record_tables where scope_type=? and scope_id=?"
        params: list[Any] = [scope_type, scope_id]
        if not include_archived:
            sql += " and status='active'"
        sql += " order by updated_at desc"
        with self._connection(conn) as db:
            return [_table_row(row) or {} for row in db.execute(sql, params).fetchall()]

    def update_table(self, table_id: str, expected_version: int, data: dict[str, Any], actor_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        now = utcnow_iso()
        values = {
            "name": data["name"],
            "description": data["description"],
            "creation_policy": data["creation_policy"],
            "schema_json": _dump(data["schema"]),
            "new_version": expected_version + 1,
            "updated_at": now,
            "table_id": table_id,
            "expected_version": expected_version,
        }
        with self._connection(conn) as db:
            cursor = db.execute(
                """update record_tables set name=:name, description=:description,
                    creation_policy=:creation_policy, schema_json=:schema_json,
                    schema_version=:new_version, updated_at=:updated_at
                    where table_id=:table_id and schema_version=:expected_version""",
                values,
            )
            if cursor.rowcount != 1:
                return None
            db.execute(
                "insert into record_table_versions(table_id, schema_version, schema_json, created_by, created_at) values (?,?,?,?,?)",
                (table_id, expected_version + 1, values["schema_json"], actor_id, now),
            )
            return self.get_table(table_id, conn=db)

    def archive_table(self, table_id: str, expected_version: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            cursor = db.execute(
                "update record_tables set status='archived', schema_version=schema_version+1, updated_at=? where table_id=? and schema_version=?",
                (utcnow_iso(), table_id, expected_version),
            )
            return self.get_table(table_id, conn=db) if cursor.rowcount == 1 else None

    # Records

    def get_record(self, record_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            return _record_row(db.execute("select * from records where record_id=?", (record_id,)).fetchone())

    def get_actor_draft(self, table_id: str, scope_type: str, scope_id: str, actor_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            row = db.execute(
                """select * from records where table_id=? and scope_type=? and scope_id=?
                    and created_by=? and status in ('draft','pending_confirm') limit 1""",
                (table_id, scope_type, scope_id, actor_id),
            ).fetchone()
            return _record_row(row)

    def create_record(self, data: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utcnow_iso()
        payload = {
            "record_id": data.get("record_id") or new_id("REC"),
            "table_id": data["table_id"],
            "scope_type": data["scope_type"],
            "scope_id": data["scope_id"],
            "status": data["status"],
            "values_json": _dump(data.get("values") or {}),
            "schema_version": data["schema_version"],
            "version": 1,
            "raw_text": data.get("raw_text", ""),
            "created_by": data["actor_id"],
            "updated_by": data["actor_id"],
            "created_at": now,
            "updated_at": now,
        }
        with self._connection(conn) as db:
            db.execute(
                """insert into records(
                    record_id, table_id, scope_type, scope_id, status, values_json,
                    schema_version, version, raw_text, created_by, updated_by, created_at, updated_at
                ) values (
                    :record_id, :table_id, :scope_type, :scope_id, :status, :values_json,
                    :schema_version, :version, :raw_text, :created_by, :updated_by, :created_at, :updated_at
                )""",
                payload,
            )
            return self.get_record(payload["record_id"], conn=db) or {}

    def update_record(self, record_id: str, expected_version: int, data: dict[str, Any], actor_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        assignments = ["version=version+1", "updated_at=?", "updated_by=?"]
        params: list[Any] = [utcnow_iso(), actor_id]
        for key, column in (("status", "status"), ("values", "values_json"), ("schema_version", "schema_version"), ("raw_text", "raw_text")):
            if key in data:
                assignments.append(f"{column}=?")
                params.append(_dump(data[key]) if key == "values" else data[key])
        params.extend([record_id, expected_version])
        with self._connection(conn) as db:
            cursor = db.execute(
                f"update records set {', '.join(assignments)} where record_id=? and version=?",
                params,
            )
            return self.get_record(record_id, conn=db) if cursor.rowcount == 1 else None

    def list_records(self, scope_type: str, scope_id: str, table_id: str, statuses: list[str] | None = None, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        sql = "select * from records where scope_type=? and scope_id=? and table_id=?"
        params: list[Any] = [scope_type, scope_id, table_id]
        if statuses:
            sql += f" and status in ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        sql += " order by updated_at desc"
        with self._connection(conn) as db:
            return [_record_row(row) or {} for row in db.execute(sql, params).fetchall()]

    # Reminder policies and reminders

    def get_reminder_policy(self, table_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            row = db.execute("select * from table_reminder_policies where table_id=?", (table_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["rules"] = _load(result.pop("rules_json"), [])
            return result

    def set_reminder_policy(self, data: dict[str, Any], expected_version: int | None, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        now = utcnow_iso()
        with self._connection(conn) as db:
            existing = self.get_reminder_policy(data["table_id"], conn=db)
            if existing:
                if expected_version is None or existing["version"] != expected_version:
                    return None
                db.execute(
                    """update table_reminder_policies set rules_json=?, version=version+1,
                        updated_by=?, updated_at=? where table_id=?""",
                    (_dump(data["rules"]), data["actor_id"], now, data["table_id"]),
                )
            else:
                if expected_version not in (None, 0):
                    return None
                db.execute(
                    """insert into table_reminder_policies(
                        table_id, scope_type, scope_id, rules_json, version, created_by,
                        updated_by, created_at, updated_at
                    ) values (?,?,?,?,1,?,?,?,?)""",
                    (data["table_id"], data["scope_type"], data["scope_id"], _dump(data["rules"]), data["actor_id"], data["actor_id"], now, now),
                )
            return self.get_reminder_policy(data["table_id"], conn=db)

    def clear_reminder_policy(self, table_id: str, expected_version: int, conn: sqlite3.Connection | None = None) -> bool:
        with self._connection(conn) as db:
            return db.execute("delete from table_reminder_policies where table_id=? and version=?", (table_id, expected_version)).rowcount == 1

    def create_reminder(self, data: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utcnow_iso()
        payload = {
            "reminder_id": data.get("reminder_id") or new_id("REM"),
            "scope_type": data["scope_type"],
            "scope_id": data["scope_id"],
            "record_id": data.get("record_id"),
            "content": data["content"],
            "trigger_type": data["trigger_type"],
            "trigger_json": _dump(data["trigger"]),
            "next_fire_at": data["next_fire_at"],
            "timezone": data["timezone"],
            "source_type": data.get("source_type", "manual"),
            "source_rule_id": data.get("source_rule_id"),
            "status": "active",
            "version": 1,
            "created_by": data["actor_id"],
            "updated_by": data["actor_id"],
            "created_at": now,
            "updated_at": now,
        }
        with self._connection(conn) as db:
            db.execute(
                """insert into reminders(
                    reminder_id, scope_type, scope_id, record_id, content, trigger_type,
                    trigger_json, next_fire_at, timezone, source_type, source_rule_id,
                    status, version, created_by, updated_by, created_at, updated_at
                ) values (
                    :reminder_id, :scope_type, :scope_id, :record_id, :content, :trigger_type,
                    :trigger_json, :next_fire_at, :timezone, :source_type, :source_rule_id,
                    :status, :version, :created_by, :updated_by, :created_at, :updated_at
                )""",
                payload,
            )
            self._create_delivery(payload["reminder_id"], payload["next_fire_at"], 1, db)
            return self.get_reminder(payload["reminder_id"], conn=db) or {}

    def get_reminder(self, reminder_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(conn) as db:
            return _reminder_row(db.execute("select * from reminders where reminder_id=?", (reminder_id,)).fetchone())

    def list_reminders(self, scope_type: str, scope_id: str, statuses: list[str] | None = None, record_id: str | None = None, source_type: str | None = None, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        sql = "select * from reminders where scope_type=? and scope_id=?"
        params: list[Any] = [scope_type, scope_id]
        if statuses:
            sql += f" and status in ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        if record_id:
            sql += " and record_id=?"
            params.append(record_id)
        if source_type:
            sql += " and source_type=?"
            params.append(source_type)
        sql += " order by next_fire_at asc"
        with self._connection(conn) as db:
            return [_reminder_row(row) or {} for row in db.execute(sql, params).fetchall()]

    def update_reminder(self, reminder_id: str, expected_version: int, data: dict[str, Any], actor_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        assignments = ["version=version+1", "updated_at=?", "updated_by=?"]
        params: list[Any] = [utcnow_iso(), actor_id]
        for key, column in (("content", "content"), ("trigger_type", "trigger_type"), ("trigger", "trigger_json"), ("next_fire_at", "next_fire_at"), ("status", "status")):
            if key in data:
                assignments.append(f"{column}=?")
                params.append(_dump(data[key]) if key == "trigger" else data[key])
        params.extend([reminder_id, expected_version])
        with self._connection(conn) as db:
            cursor = db.execute(f"update reminders set {', '.join(assignments)} where reminder_id=? and version=?", params)
            if cursor.rowcount != 1:
                return None
            updated = self.get_reminder(reminder_id, conn=db)
            if updated and updated["status"] == "active" and "next_fire_at" in data:
                db.execute("update reminder_deliveries set status='cancelled', updated_at=? where reminder_id=? and status in ('pending','processing','failed')", (utcnow_iso(), reminder_id))
                self._create_delivery(reminder_id, updated["next_fire_at"], updated["version"], db)
            return updated

    def cancel_reminders_for_record(self, record_id: str, source_type: str | None = None, conn: sqlite3.Connection | None = None) -> int:
        with self._connection(conn) as db:
            sql = "select reminder_id, version from reminders where record_id=? and status='active'"
            params: list[Any] = [record_id]
            if source_type:
                sql += " and source_type=?"
                params.append(source_type)
            rows = db.execute(sql, params).fetchall()
            for row in rows:
                self.update_reminder(row["reminder_id"], row["version"], {"status": "cancelled"}, "system", conn=db)
                db.execute("update reminder_deliveries set status='cancelled', updated_at=? where reminder_id=? and status!='sent'", (utcnow_iso(), row["reminder_id"]))
            return len(rows)

    def _create_delivery(self, reminder_id: str, scheduled_at: str, reminder_version: int, conn: sqlite3.Connection) -> None:
        now = utcnow_iso()
        conn.execute(
            """insert into reminder_deliveries(
                delivery_id, reminder_id, scheduled_at, status, attempt_count,
                last_error, idempotency_key, created_at, updated_at
            ) values (?,?,?,'pending',0,'',?,?,?)""",
            (new_id("DEL"), reminder_id, scheduled_at, f"{reminder_id}:v{reminder_version}", now, now),
        )

    def claim_due_deliveries(self, now: str, lease_until: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                """select d.*, r.scope_type, r.scope_id, r.content, r.status as reminder_status
                   from reminder_deliveries d join reminders r on r.reminder_id=d.reminder_id
                   where r.status='active' and (
                     (d.status in ('pending','failed') and d.scheduled_at<=?) or
                     (d.status='processing' and d.lease_until<?)
                   ) order by d.scheduled_at limit ?""",
                (now, now, limit),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                conn.execute(
                    """update reminder_deliveries set status='processing', claimed_at=?, lease_until=?,
                       attempt_count=attempt_count+1, updated_at=? where delivery_id=?""",
                    (now, lease_until, now, row["delivery_id"]),
                )
                item = dict(row)
                item["attempt_count"] += 1
                claimed.append(item)
            return claimed

    def mark_delivery_sent(self, delivery_id: str, reminder_id: str) -> None:
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute("update reminder_deliveries set status='sent', sent_at=?, updated_at=? where delivery_id=?", (now, now, delivery_id))
            conn.execute("update reminders set status='sent', version=version+1, updated_at=? where reminder_id=? and status='active'", (now, reminder_id))

    def mark_delivery_failed(self, delivery_id: str, error: str, retry_at: str | None) -> None:
        with self.connect() as conn:
            if retry_at:
                conn.execute(
                    "update reminder_deliveries set status='failed', scheduled_at=?, last_error=?, lease_until=null, updated_at=? where delivery_id=?",
                    (retry_at, error, utcnow_iso(), delivery_id),
                )
            else:
                row = conn.execute("select reminder_id from reminder_deliveries where delivery_id=?", (delivery_id,)).fetchone()
                conn.execute("update reminder_deliveries set status='failed', last_error=?, lease_until=null, updated_at=? where delivery_id=?", (error, utcnow_iso(), delivery_id))
                if row:
                    conn.execute("update reminders set status='failed', version=version+1, updated_at=? where reminder_id=?", (utcnow_iso(), row["reminder_id"]))

    # Scope reachability, audit, and conversation history

    def mark_conversation_reachable(self, scope_type: str, scope_id: str) -> None:
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """insert into reachable_conversations(scope_type, scope_id, last_interacted_at, reachable, updated_at)
                   values (?,?,?,1,?) on conflict(scope_type,scope_id) do update set
                   last_interacted_at=excluded.last_interacted_at, reachable=1,
                   last_delivery_error='', updated_at=excluded.updated_at""",
                (scope_type, scope_id, now, now),
            )

    def is_conversation_reachable(self, scope_type: str, scope_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("select reachable from reachable_conversations where scope_type=? and scope_id=?", (scope_type, scope_id)).fetchone()
            return bool(row and row["reachable"])

    def add_audit(self, resource_type: str, resource_id: str, event_type: str, actor_id: str, scope_type: str, scope_id: str, before: Any = None, after: Any = None, conn: sqlite3.Connection | None = None) -> None:
        with self._connection(conn) as db:
            db.execute(
                """insert into audit_events(event_id, resource_type, resource_id, event_type,
                   actor_user_id, scope_type, scope_id, before_json, after_json, created_at)
                   values (?,?,?,?,?,?,?,?,?,?)""",
                (new_id("AUD"), resource_type, resource_id, event_type, actor_id, scope_type, scope_id, _dump(before) if before is not None else None, _dump(after) if after is not None else None, utcnow_iso()),
            )

    def get_message_result(self, message_id: str) -> Optional[str]:
        if not message_id:
            return None
        with self.connect() as conn:
            row = conn.execute("select response_text from message_results where message_id=?", (message_id,)).fetchone()
            return row["response_text"] if row else None

    def save_message_result(self, message_id: str, chat_id: str, user_id: str, response_text: str) -> None:
        if not message_id:
            return
        with self.connect() as conn:
            conn.execute(
                "insert or replace into message_results(message_id, chat_id, user_id, response_text, created_at) values (?,?,?,?,?)",
                (message_id, chat_id, user_id, response_text, utcnow_iso()),
            )

    def list_conversation_message_json(self, chat_id: str, user_id: str, limit: int = 12) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """select message_json from (
                    select message_json, created_at, rowid as sequence from conversation_messages
                    where chat_id=? and user_id=? order by created_at desc, rowid desc limit ?
                ) order by created_at asc, sequence asc""",
                (chat_id, user_id, limit),
            ).fetchall()
            return [row["message_json"] for row in rows]

    def save_conversation_message_json(self, chat_id: str, user_id: str, conversation_id: str | None, messages_json: list[str], source_message_id: str = "") -> None:
        if not messages_json:
            return
        now = utcnow_iso()
        with self.connect() as conn:
            for index, message_json in enumerate(messages_json):
                message_id = f"{source_message_id or new_id('MSG')}:{index}"
                conn.execute(
                    """insert or ignore into conversation_messages(
                        message_id, chat_id, user_id, conversation_id, message_json, created_at
                    ) values (?,?,?,?,?,?)""",
                    (message_id, chat_id, user_id, conversation_id, message_json, now),
                )

    def clear_conversation_history(self, chat_id: str | None = None, user_id: str | None = None) -> dict[str, int]:
        params: list[Any] = []
        message_sql = "delete from conversation_messages"
        result_sql = "delete from message_results"
        conditions: list[str] = []
        if chat_id:
            conditions.append("chat_id=?")
            params.append(chat_id)
        if user_id:
            conditions.append("user_id=?")
            params.append(user_id)
        if conditions:
            clause = " where " + " and ".join(conditions)
            message_sql += clause
            result_sql += clause
        with self.connect() as conn:
            message_count = conn.execute(message_sql, params).rowcount
            result_count = conn.execute(result_sql, params).rowcount
        return {"conversation_messages": message_count, "message_results": result_count}

    def add_local_push_outbox(self, scope_type: str, scope_id: str, content: str) -> dict[str, Any]:
        payload = {
            "outbox_id": new_id("OUT"),
            "scope_type": scope_type,
            "scope_id": scope_id,
            "content": content,
            "created_at": utcnow_iso(),
            "acknowledged_at": None,
        }
        with self.connect() as conn:
            conn.execute(
                """insert into local_push_outbox(
                    outbox_id, scope_type, scope_id, content, created_at, acknowledged_at
                ) values (?,?,?,?,?,?)""",
                (
                    payload["outbox_id"],
                    payload["scope_type"],
                    payload["scope_id"],
                    payload["content"],
                    payload["created_at"],
                    payload["acknowledged_at"],
                ),
            )
        return payload

    def list_local_push_outbox(self, pending_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        sql = "select * from local_push_outbox"
        params: list[Any] = []
        if pending_only:
            sql += " where acknowledged_at is null"
        sql += " order by created_at desc limit ?"
        params.append(max(1, min(limit, 200)))
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def acknowledge_local_push_outbox(self, outbox_id: str | None = None) -> int:
        now = utcnow_iso()
        with self.connect() as conn:
            if outbox_id:
                return conn.execute(
                    "update local_push_outbox set acknowledged_at=? where outbox_id=? and acknowledged_at is null",
                    (now, outbox_id),
                ).rowcount
            return conn.execute(
                "update local_push_outbox set acknowledged_at=? where acknowledged_at is null",
                (now,),
            ).rowcount


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _table_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["schema"] = _load(result.pop("schema_json"), {})
    return result


def _record_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["values"] = _load(result.pop("values_json"), {})
    return result


def _reminder_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["trigger"] = _load(result.pop("trigger_json"), {})
    return result


store = RecordStore()
