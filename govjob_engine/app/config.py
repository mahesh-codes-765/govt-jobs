from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/govjobs.db"
    request_timeout: int = 45
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    crawl_delay_seconds: float = 0.4
    max_listing_pages: int = 50

    # LLM (Anthropic) extraction layer — off by default, fails closed on any
    # missing key/model/budget rather than silently skipping or overspending.
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_monthly_usd_cap: float = 5.0
    llm_spend_file: str = "./data/llm_spend.json"

    # Web-search discovery — a broader net than the fixed site adapters,
    # for boards whose listing pages are JS-only (SSC, RRB) or simply not
    # wired up yet. Off by default: it's a different, less predictable
    # cost profile (per-search fees on top of tokens), so it gets its own
    # cap, separate from the per-document extraction budget above.
    web_discovery_enabled: bool = False
    web_discovery_model: str = "claude-haiku-4-5-20251001"
    web_discovery_monthly_usd_cap: float = 3.0

    # Telegram human-review bot (long-poll, no webhook/port needed).
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_interval_seconds: float = 2.0
    crawl_poll_interval_seconds: float = 1800.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
