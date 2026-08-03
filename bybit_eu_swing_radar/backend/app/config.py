from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    radar_api_key: str
    bybit_base_url: str = "https://api.bybit.eu"
    coinalyze_base_url: str = "https://api.coinalyze.net/v1"
    coinalyze_api_key: str
    data_max_age_minutes: int = 90

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
