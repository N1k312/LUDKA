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
    # Адрес Mini App; по умолчанию — /app/ на том же домене.
    miniapp_url: str | None = None
    env: str = "dev"
    # Только для разработки: токен тестового бота для команды connect-bot.
    test_bot_token: SecretStr | None = None

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")

    # Запуск на своём компьютере: адрес quick-туннеля cloudflared берётся из его
    # метрик (/quicktunnel), и при смене адреса боты перенастраиваются сами.
    tunnel_metrics_url: str | None = None

    # Сколько секунд initData из Mini App считается свежим.
    init_data_max_age_seconds: int = 24 * 3600

    @property
    def webapp_url(self) -> str:
        return self.miniapp_url or f"{self.base_url}/app/"

    def webapp_url_for(self, bot_id: int) -> str:
        """Mini App общий для всех клубов; по ?bot= он узнаёт, через какого бота открыт.

        Подделать параметр бесполезно: initData проверяется токеном именно этого бота.
        """
        return f"{self.webapp_url}?bot={bot_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
