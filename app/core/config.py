#app/core/config.py
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App General Config
    APP_NAME: str = "Teams-Outlook Sync Engine"
    ENV: str = "development"
    DEBUG: bool = True

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

    # MS Graph / Azure AD Credentials
    AZURE_TENANT_ID: str
    AZURE_CLIENT_ID: str
    AZURE_CLIENT_SECRET: str

    # Sync Pipeline Schedules
    CHANNEL_SYNC_INTERVAL_HOURS: int = 4
    MESSAGE_SYNC_INTERVAL_HOURS: int = 1
    # 메시지 조회 시 폴링 주기보다 넉넉하게 잡는 여유(분) - 실행 지연으로 인한 누락 방지
    MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()