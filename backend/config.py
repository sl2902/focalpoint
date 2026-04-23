from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GDELT_CLOUD_API_KEY: str = ""
    REDIS_URL: str = "redis://localhost:6379"
    GOOGLE_AI_STUDIO_API_KEY: str = ""

    # ACLED credentials — kept for reactivation if API access is granted.
    # See backend/ingestion/acled_connector_disabled.py.
    ACLED_USERNAME: str = ""
    ACLED_PASSWORD: str = ""
    ACLED_TOKEN_URL: str = "https://acleddata.com/oauth/token"


settings = Settings()
