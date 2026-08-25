from src.records.store import store


def main() -> None:
    store.init_db()
    print("数据库已初始化")


if __name__ == "__main__":
    main()
