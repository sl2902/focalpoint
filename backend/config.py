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

    # Background alert scheduler
    WATCH_ZONES: list[str] = [
        "Palestine", "Israel", "Iran", "Ukraine", "Sudan", "Myanmar", "Yemen", "Syria"
    ]
    ALERTS_DB_PATH: str = "backend/data/alerts.db"
    SCHEDULER_ENABLED: bool = True

    # Countries where GDELT Cloud's has_fatalities=true filter returns 0 events.
    # The connector will omit the filter for these and rely on the fatalities
    # field being None rather than absent. Confirmed via verify_watch_zones.py.
    NO_FATALITIES_FILTER_COUNTRIES: set[str] = {
        "Iran", "Sudan", "Myanmar", "Yemen", "Syria"
    }

    # Maps FocalPoint region names to the country string the GDELT Cloud API
    # recognises. Add entries here when the API returns 0 events for a region
    # that has confirmed activity under a different name.
    # Note: "Syrian Arab Republic" tested and rejected with 400 — "Syria" is correct.
    GDELT_CLOUD_ALIASES: dict[str, str] = {
        "Gaza": "Palestine",
        "Gaza Strip": "Palestine",
    }


settings = Settings()
