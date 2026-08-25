from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    wecom_bot_id: str = ""
    wecom_bot_secret: str = ""
    enable_wecom_bot: bool = True
    wecom_mirror_groups: str = ""
    wecom_mirror_scopes: str = ""
    wecom_debug_chat_id_log: bool = False

    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3-max"
    llm_enable_thinking: bool = False
    llm_invoke_wall_timeout_sec: float = 90.0

    app_host: str = "0.0.0.0"
    app_port: int = 8100
    app_debug: bool = False
    data_dir: Path = Path("./data")
    model_input_snapshot_dir: Path = Path("./data/model_input_snapshots")
    log_dir: Path = Path("./data/logs")
    log_retention_days: int = 30
    log_console: bool = True
    scheduler_timezone: str = "Asia/Shanghai"

    agent_version: str = "v0.2"
    admin_init_token: str = ""

    logfire_enabled: bool | None = None
    logfire_token: str = ""
    logfire_base_url: str = "https://logfire-us.pydantic.dev"
    logfire_service_name: str = "hall-agent"
    logfire_environment: str = "local"
    logfire_console: bool = False
    logfire_pydantic_record: str = "failure"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()


def ensure_data_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.model_input_snapshot_dir.mkdir(parents=True, exist_ok=True)


def db_path() -> Path:
    return settings.data_dir / "hall_agent.db"
