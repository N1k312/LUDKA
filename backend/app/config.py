from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env лежит в корне репозитория; в Docker переменные приходят из окружения.
ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    database_url: str
    encryption_key: SecretStr
    public_base_url: str = "http://localhost:8000"
    env: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
