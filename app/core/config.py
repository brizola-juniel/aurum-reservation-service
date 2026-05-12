from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "aurum-reservation-service"
    database_url: str = "postgresql+asyncpg://reservations:reservations@localhost:5434/reservationsdb"
    jwt_secret: str = Field(min_length=32)
    jwt_issuer: str = "aurum-auth-service"
    jwt_audience: str = "aurum-reservation-system"
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
