from __future__ import annotations

from pathlib import Path

from src.records.store import RecordStore


def main() -> None:
    db_paths = sorted({path for path in Path("data").rglob("*.db") if path.is_file()})
    if not db_paths:
        print("未找到数据库文件")
        return

    cleared: list[str] = []
    for path in db_paths:
        store = RecordStore(path)
        with store.connect() as conn:
            tables = {row["name"] for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        if {"conversation_messages", "message_results"} & tables:
            counts = store.clear_conversation_history()
            cleared.append(f"{path} ({counts['conversation_messages']} messages, {counts['message_results']} results)")

    if cleared:
        print("\n".join(cleared))
    else:
        print("未找到可清理的历史表")


if __name__ == "__main__":
    main()
