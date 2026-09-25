"""Configuración centralizada de la aplicación, leída desde variables de entorno."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_seconds: int = 60 * 60 * 24

    model_provider: str = "anthropic"
    model_name: str = "claude-sonnet-5"

    observability_backend: str = "langsmith"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
