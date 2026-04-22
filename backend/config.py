from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ACLED_USERNAME: str = ""
    ACLED_PASSWORD: str = ""
    ACLED_TOKEN_URL: str = "https://acleddata.com/oauth/token"
    REDIS_URL: str = "redis://localhost:6379"
    GOOGLE_AI_STUDIO_API_KEY: str = ""


settings = Settings()
