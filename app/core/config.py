from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Phase 3: voice transcription via OpenAI Whisper
    # Phase 6: draft generation — leave empty until needed
    openai_api_key: str = ""
    whisper_model: str = "whisper-1"
    editorial_model: str = "gpt-4.1-mini"

    discovery_default_limit: int = 3
    discovery_fetch_multiplier: int = 4
    discovery_enabled_sources: str = "arxiv,hackernews"
    github_token: str = ""
    github_insights_default_limit: int = 5
    github_commits_limit: int = 8

    db_path: str = "data/engine.db"
    debug: bool = False
    log_level: str = "INFO"

    @property
    def enabled_discovery_sources(self) -> tuple[str, ...]:
        return tuple(
            source.strip().lower()
            for source in self.discovery_enabled_sources.split(",")
            if source.strip()
        )


settings = Settings()
