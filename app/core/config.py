from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_admin_chat_id: int = 0
    telegram_command_limit: int = 3
    telegram_max_message_chars: int = 4096
    telegram_send_retries: int = 2
    internal_cron_secret: str = ""

    # Phase 3: voice transcription via OpenAI Whisper
    # Phase 6: draft generation — leave empty until needed
    openai_api_key: str = ""
    whisper_model: str = "whisper-1"
    editorial_model: str = "gpt-4.1-mini"

    # Query normalization via Claude Haiku (optional — falls back to raw query)
    anthropic_api_key: str = ""
    normalizer_model: str = "claude-haiku-4-5"
    normalizer_timeout_seconds: float = 3.0
    normalizer_cache_size: int = 128

    discovery_default_limit: int = 3
    discovery_fetch_multiplier: int = 4
    discovery_enabled_sources: str = "arxiv,hackernews"
    github_token: str = ""
    github_insights_default_limit: int = 5
    github_commits_limit: int = 8
    priority_github_repos: str = (
        "The-Velveteen-Project/StochastoGreen,The-Velveteen-Project/EcoAgent"
    )
    # PLANNED: Phase 11 - Supabase migration.
    # Declared here so deployment configuration can be prepared early,
    # but not yet wired into runtime persistence.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    enable_scheduler: bool = False
    weekly_discovery_query: str = (
        "agentic workflows climate risk health ai latam applied research"
    )
    weekly_summary_cron: str = "0 9 * * 0"
    weekly_mvp_scan_cron: str = "0 9 * * 4"

    # Operator personalization (Sub-phase A — env-driven; full goal model is
    # planned for Sub-phase B with persistence and a /goal command).
    active_goal_text: str = ""
    weekly_focus_label: str = ""
    weekly_use_llm_thesis: bool = True
    weekly_thesis_timeout_seconds: float = 12.0
    handoff_followup_delay_hours: int = 48
    handoff_match_timeout_seconds: float = 10.0
    linkedin_writer_timeout_seconds: float = 14.0

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

    @property
    def priority_github_repo_list(self) -> tuple[str, ...]:
        return tuple(
            repo.strip()
            for repo in self.priority_github_repos.split(",")
            if repo.strip()
        )


settings = Settings()
