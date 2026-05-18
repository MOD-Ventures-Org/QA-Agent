from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    webhook_secret: str = Field(..., env="WEBHOOK_SECRET")
    discord_webhook_url: str = Field(..., env="DISCORD_WEBHOOK_URL")
    ngrok_authtoken: str = Field("", env="NGROK_AUTHTOKEN")
    clickup_api_token: str = Field("", env="CLICKUP_API_TOKEN")
    clickup_list_id: str = Field("", env="CLICKUP_LIST_ID")
    mongodb_uri: str = Field("mongodb://localhost:27017", env="MONGODB_URI")
    mongodb_db_name: str = Field("aria", env="MONGODB_DB_NAME")
    base_url_frontend: str = Field("http://localhost:3000", env="BASE_URL_FRONTEND")
    base_url_api: str = Field("http://localhost:8080", env="BASE_URL_API")
    playwright_headless: bool = Field(True, env="PLAYWRIGHT_HEADLESS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
