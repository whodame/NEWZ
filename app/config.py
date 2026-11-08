import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


DEFAULT_SOURCES = [
    {
        "category": "OpenBSD",
        "name": "OpenBSD Journal (Undeadly)",
        "url": "https://undeadly.org/cgi?action=rss",
    },
    {
        "category": "Linux",
        "name": "Phoronix",
        "url": "https://www.phoronix.com/phoronix-rss.php",
    },
    {
        "category": "Windows & Безопасность",
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/",
    },
    {
        "category": "Мир & Технологии",
        "name": "Hacker News",
        "url": "https://news.ycombinator.com/rss",
    },
]

# Ключевые слова для мгновенной отправки важных новостей БЕЗ обработки ИИ
DEFAULT_URGENT_KEYWORDS = [
    "0-day",
    "zero-day",
    "cve-",
    "rce",
    "remote code execution",
    "critical vulnerability",
    "actively exploited",
    "errata",
    "security advisory",
    "openbsd 7.",
    "openbsd 8.",
    "linux 6.",
    "linux 7.",
    "kernel panic",
    "backdoor",
    "global outage",
    "ransomware",
]


@dataclass
class Settings:
    tg_token: str = field(default_factory=lambda: os.getenv("TG_TOKEN", "").strip())
    chat_id: str = field(default_factory=lambda: os.getenv("CHAT_ID", "").strip())
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip())
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "").strip())
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_PATH", "news.db").strip())
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    # Интервал проверки RSS-лент (в минутах)
    poll_interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
    )

    # Постепенная ИИ-сводка свежих новостей (в часах, 0 — отключить промежуточные сводки и оставить только еженедельный дайджест)
    gradual_digest_hours: int = field(
        default_factory=lambda: int(os.getenv("GRADUAL_DIGEST_HOURS", "6"))
    )

    # При первом запуске не засыпать чат старыми новостями из RSS
    seed_silently_on_first_run: bool = field(
        default_factory=lambda: os.getenv("SEED_SILENTLY_ON_FIRST_RUN", "true").lower() in ("1", "true", "yes")
    )


settings = Settings()
