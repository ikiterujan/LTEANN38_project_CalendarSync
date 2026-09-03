# app/core/config.py
from typing import Optional, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App General Config
    APP_NAME: str = "Teams-Outlook Sync Engine"
    ENV: str = "development"
    DEBUG: bool = True
    
    # DB 초기화 및 재설정 플래그
    RESET_DB: bool = False

    # Database (Oracle Wallet)
    DB_USER: str
    DB_PASSWORD: str
    DB_DSN: str
    WALLET_DIR: str = "/home/ubuntu/wallet"

    # Security
    DATA_ENCRYPTION_KEY: str

    # OpenAI / LLM Config
    OPENAI_API_KEY: str
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_CONFIDENCE_THRESHOLD: float = 0.7
    GRADE_OVERRIDES: Optional[Dict[str, int]] = None

    # MS Graph / Azure AD Credentials
    AZURE_TENANT_ID: str
    AZURE_CLIENT_ID: str
    AZURE_CLIENT_SECRET: str

    # Sync Pipeline Schedules
    CHANNEL_SYNC_INTERVAL_HOURS: int = 1
    MESSAGE_SYNC_INTERVAL_HOURS: int = 1
    MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()