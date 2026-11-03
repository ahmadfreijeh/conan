from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "conan"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/conan"
    redis_url: str = "redis://localhost:6379/0"

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
