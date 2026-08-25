from __future__ import annotations

import uvicorn

from src.config import settings


def main() -> None:
    uvicorn.run(
        "src.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
