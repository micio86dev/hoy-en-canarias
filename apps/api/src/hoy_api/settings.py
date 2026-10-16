from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from HOY_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="HOY_", env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://hoy:hoy_dev_password@localhost:55432/hoy"
    )
    site_url: str = "http://localhost:3000"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    admin_token: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("admin_token")
    @classmethod
    def _reject_blank_admin_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if not value.get_secret_value().strip():
            raise ValueError("HOY_ADMIN_TOKEN must not be blank when set")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
