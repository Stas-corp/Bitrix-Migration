from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bitrix_db_host: str = "localhost"
    bitrix_db_port: int = 3306
    bitrix_db_user: str = "root"
    bitrix_db_password: str = ""
    bitrix_db_name: str = "bitrix24"
    api_base_url: str = "http://localhost:8000"


settings = Settings()
