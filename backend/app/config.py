"""
Asahi ERP - Configuration Settings
Menggunakan pydantic-settings untuk environment variable management
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Konfigurasi utama aplikasi.
    Semua nilai bisa di-override lewat environment variables atau .env file
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==========================================
    # Application Settings
    # ==========================================
    APP_NAME: str = "Asahi ERP API"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # ==========================================
    # Database Settings
    # ==========================================
    DATABASE_URL: str = "postgresql://asahi_dev:asahi_dev_123@localhost:5432/asahi_erp_dev"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_ECHO: bool = False  # Log SQL queries

    # ==========================================
    # JWT Authentication Settings
    # ==========================================
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # ==========================================
    # CORS Settings
    # ==========================================
    FRONTEND_URL: str = "http://localhost:3000"

    # ==========================================
    # Logging Settings
    # ==========================================
    LOG_LEVEL: str = "INFO"

    # ==========================================
    # Validators
    # ==========================================
    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper().strip()
        if v_upper not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of {valid_levels}")
        return v_upper

    # ==========================================
    # Computed Properties
    # ==========================================
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins(self) -> list[str]:
        """Return list of allowed CORS origins based on environment"""
        if self.is_development:
            return [
                self.FRONTEND_URL,
                "http://127.0.0.1:3000",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ]
        return [self.FRONTEND_URL]


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Menggunakan lru_cache agar settings hanya di-load sekali per process.
    """
    return Settings()


# Export untuk kemudahan import
settings = get_settings()
