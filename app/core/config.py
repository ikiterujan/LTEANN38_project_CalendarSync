import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App General Config
    APP_NAME: str = "Teams-Outlook Sync Engine"
    ENV: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()