from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHALKBOARD_")

    # Local-development defaults only; real values come from the environment.
    database_url: str = (
        "postgresql+psycopg://chalkboard:chalkboard@localhost:5432/chalkboard"
    )
    secret_key: str = "dev-not-secret"
    data_dir: Path = Path("data")
    backup_dir: Path = Path("backups")
