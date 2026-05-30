from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    anthropic_api_key: str = Field("", env="ANTHROPIC_API_KEY")
    github_token: str = Field("", env="GITHUB_TOKEN")
    kimi_api_key: str = Field("", env="KIMI_API_KEY")
    kimi_model: str = Field("moonshot-v1-8k", env="KIMI_MODEL")
    kimi_api_url: str = Field("https://api.moonshot.ai/v1/chat/completions", env="KIMI_API_URL")
    webhook_secret: str = Field(..., env="WEBHOOK_SECRET")
    discord_webhook_url: str = Field("", env="DISCORD_WEBHOOK_URL")
    discord_enabled: bool = Field(False, env="DISCORD_ENABLED")
    clickup_enabled: bool = Field(False, env="CLICKUP_ENABLED")
    ngrok_authtoken: str = Field("", env="NGROK_AUTHTOKEN")
    clickup_api_token: str = Field("", env="CLICKUP_API_TOKEN")
    clickup_list_id: str = Field("", env="CLICKUP_LIST_ID")
    mongodb_uri: str = Field("mongodb://localhost:27017", env="MONGODB_URI")
    mongodb_db_name: str = Field("aria", env="MONGODB_DB_NAME")
    base_url_frontend: str = Field("http://localhost:3000", env="BASE_URL_FRONTEND")
    base_url_api: str = Field("http://localhost:8080", env="BASE_URL_API")
    playwright_headless: bool = Field(True, env="PLAYWRIGHT_HEADLESS")
    load_test_requests: int = Field(50, env="LOAD_TEST_REQUESTS")
    load_test_concurrency: int = Field(10, env="LOAD_TEST_CONCURRENCY")
    load_test_path: str = Field("/health", env="LOAD_TEST_PATH")
    load_test_max_p95_ms: float = Field(1000.0, env="LOAD_TEST_MAX_P95_MS")
    load_test_min_success_rate: float = Field(0.95, env="LOAD_TEST_MIN_SUCCESS_RATE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
