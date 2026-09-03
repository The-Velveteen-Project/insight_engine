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
    # Sub-phase B.7: redirect the OpenAI client to any OpenAI-compatible
    # endpoint. Empty = OpenAI default. Examples:
    #   OpenRouter: https://openrouter.ai/api/v1
    #   Groq:       https://api.groq.com/openai/v1
    #   Local Ollama: http://localhost:11434/v1
    # Whisper transcription stays on OpenAI native; everything else (editorial,
    # draft, weekly thesis, linkedin, handoff match) honors this override.
    openai_base_url: str = ""
    whisper_model: str = "whisper-1"
    editorial_model: str = "gpt-4.1-mini"
    # Small, fast model for utilities (query normalization, probes). Empty
    # means "same as editorial_model".
    utility_model: str = ""

    # Query normalization via Claude Haiku (optional — falls back to raw query)
    anthropic_api_key: str = ""
    normalizer_model: str = "claude-haiku-4-5"
    normalizer_timeout_seconds: float = 8.0
    normalizer_cache_size: int = 128

    discovery_default_limit: int = 3
    discovery_fetch_multiplier: int = 4
    # Default switched from "arxiv,hackernews" to "arxiv,exa" in Sub-phase B.6.
    # HN remains in the registry for opt-in if you want community-validated
    # filtering layered on top of semantic search.
    discovery_enabled_sources: str = "arxiv,exa,rss"
    exa_api_key: str = ""
    # Editorial feeds (RSS 2.0 or Atom) polled by the "rss" discovery source.
    discovery_rss_feeds: str = (
        "https://research.google/blog/rss/,https://openai.com/news/rss.xml"
    )
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

    # Default language for generated LinkedIn posts ("en" or "es"). Carlos's
    # strongest posts and target roles are English, so English is the default.
    linkedin_language: str = "en"

    # Phase 2 job radar: Exa queries (semicolon-separated), job-board domains,
    # dream companies, freshness window and the minimum fit to surface a lead.
    job_radar_queries: str = (
        "research engineer scientific machine learning remote;"
        "applied scientist stochastic modeling forecasting;"
        "machine learning engineer bioinformatics foundation models;"
        "AI research engineer LLM agents remote Latin America"
    )
    job_radar_domains: str = (
        "jobs.lever.co,boards.greenhouse.io,job-boards.greenhouse.io,"
        "jobs.ashbyhq.com,wellfound.com,remoteok.com,weworkremotely.com,"
        "linkedin.com,ycombinator.com,apply.workable.com,jobs.smartrecruiters.com"
    )
    job_target_companies: str = (
        "Anthropic,OpenAI,Google DeepMind,Mistral,Cohere,Hugging Face,"
        "Allen Institute,Isomorphic Labs,Recursion,Arc Institute,Chan Zuckerberg"
    )
    job_radar_days: int = 21
    job_min_fit: float = 0.3

    # Phase 1 cadence: how many published posts per week the operator expects,
    # and how long it waits before nagging again.
    post_cadence_per_week: int = 2
    cadence_reminder_min_gap_hours: int = 60

    # Operator personalization (Sub-phase A — env-driven; full goal model is
    # planned for Sub-phase B with persistence and a /goal command).
    active_goal_text: str = ""
    weekly_focus_label: str = ""
    weekly_use_llm_thesis: bool = True
    weekly_thesis_timeout_seconds: float = 30.0
    handoff_followup_delay_hours: int = 48
    handoff_match_timeout_seconds: float = 20.0
    linkedin_writer_timeout_seconds: float = 30.0

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
    def job_radar_query_list(self) -> tuple[str, ...]:
        return tuple(q.strip() for q in self.job_radar_queries.split(";") if q.strip())

    @property
    def job_radar_domain_list(self) -> tuple[str, ...]:
        return tuple(
            d.strip().lower() for d in self.job_radar_domains.split(",") if d.strip()
        )

    @property
    def job_target_company_list(self) -> tuple[str, ...]:
        return tuple(
            c.strip() for c in self.job_target_companies.split(",") if c.strip()
        )

    @property
    def resolved_utility_model(self) -> str:
        return self.utility_model.strip() or self.editorial_model

    @property
    def discovery_rss_feed_list(self) -> tuple[str, ...]:
        return tuple(
            url.strip() for url in self.discovery_rss_feeds.split(",") if url.strip()
        )

    @property
    def priority_github_repo_list(self) -> tuple[str, ...]:
        return tuple(
            repo.strip()
            for repo in self.priority_github_repos.split(",")
            if repo.strip()
        )


settings = Settings()
