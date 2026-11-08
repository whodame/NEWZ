import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.config import DEFAULT_SOURCES, DEFAULT_URGENT_KEYWORDS, settings

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore


class Database:
    """Универсальное хранилище (SQLite по умолчанию или PostgreSQL через DATABASE_URL)."""

    def __init__(self) -> None:
        self.db_url = settings.database_url
        self.is_postgres = bool(
            self.db_url and urlparse(self.db_url).scheme.startswith("postgres")
        )
        if self.is_postgres and psycopg is None:
            raise RuntimeError(
                "Задан DATABASE_URL для PostgreSQL, но пакет psycopg не установлен."
            )

    def _connect(self):
        if self.is_postgres:
            return psycopg.connect(self.db_url, row_factory=dict_row)
        conn = sqlite3.connect(settings.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ph(self) -> str:
        return "%s" if self.is_postgres else "?"

    def init_db(self) -> bool:
        """
        Инициализирует таблицы и базовые источники.
        Возвращает True, если база была только что создана (пуста по статьям).
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sources (
                        id SERIAL PRIMARY KEY,
                        category TEXT NOT NULL,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL UNIQUE,
                        urgent_only BOOLEAN NOT NULL DEFAULT FALSE,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS urgent_keywords (
                        id SERIAL PRIMARY KEY,
                        keyword TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS articles (
                        id SERIAL PRIMARY KEY,
                        guid TEXT NOT NULL UNIQUE,
                        url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        category TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        is_urgent BOOLEAN NOT NULL DEFAULT FALSE,
                        gradual_sent BOOLEAN NOT NULL DEFAULT FALSE,
                        weekly_sent BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TEXT NOT NULL
                    );
                    """
                )
            else:
                cur.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sources (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL UNIQUE,
                        urgent_only INTEGER NOT NULL DEFAULT 0,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS urgent_keywords (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        keyword TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS articles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guid TEXT NOT NULL UNIQUE,
                        url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        category TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        is_urgent INTEGER NOT NULL DEFAULT 0,
                        gradual_sent INTEGER NOT NULL DEFAULT 0,
                        weekly_sent INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    """
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            ph = self._ph()

            cur.execute("SELECT COUNT(*) AS cnt FROM sources")
            row = cur.fetchone()
            sources_cnt = row["cnt"] if row else 0
            if sources_cnt == 0:
                for src in DEFAULT_SOURCES:
                    cur.execute(
                        f"INSERT INTO sources (category, name, url, created_at) VALUES ({ph}, {ph}, {ph}, {ph})",
                        (src["category"], src["name"], src["url"], now_iso),
                    )
                logger.info("Загружены базовые RSS-источники по умолчанию.")

            cur.execute("SELECT COUNT(*) AS cnt FROM urgent_keywords")
            row_kw = cur.fetchone()
            kw_cnt = row_kw["cnt"] if row_kw else 0
            if kw_cnt == 0:
                for kw in DEFAULT_URGENT_KEYWORDS:
                    cur.execute(
                        f"INSERT INTO urgent_keywords (keyword, created_at) VALUES ({ph}, {ph})",
                        (kw.lower(), now_iso),
                    )
                logger.info("Загружены ключевые слова срочности по умолчанию.")

            cur.execute("SELECT COUNT(*) AS cnt FROM articles")
            row_art = cur.fetchone()
            art_cnt = row_art["cnt"] if row_art else 0

            conn.commit()
            return art_cnt == 0

    # --- Управление источниками ---

    def list_sources(self, only_enabled: bool = False) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            if only_enabled:
                val = True if self.is_postgres else 1
                cur.execute(
                    f"SELECT * FROM sources WHERE enabled = {self._ph()} ORDER BY id ASC",
                    (val,),
                )
            else:
                cur.execute("SELECT * FROM sources ORDER BY id ASC")
            return [dict(r) for r in cur.fetchall()]

    def add_source(self, category: str, name: str, url: str, urgent_only: bool = False) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        ph = self._ph()
        u_val = urgent_only if self.is_postgres else (1 if urgent_only else 0)
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO sources (category, name, url, urgent_only, created_at)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (category.strip(), name.strip(), url.strip(), u_val, now_iso),
                )
                conn.commit()
            return True
        except Exception as exc:
            logger.warning("Не удалось добавить источник %s: %s", url, exc)
            return False

    def remove_source(self, identifier: str) -> bool:
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            if identifier.isdigit():
                cur.execute(f"DELETE FROM sources WHERE id = {ph}", (int(identifier),))
            else:
                cur.execute(f"DELETE FROM sources WHERE url = {ph}", (identifier.strip(),))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted

    # --- Управление ключевыми словами срочности ---

    def list_keywords(self) -> List[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT keyword FROM urgent_keywords ORDER BY keyword ASC")
            return [r["keyword"] for r in cur.fetchall()]

    def add_keyword(self, keyword: str) -> bool:
        kw = keyword.strip().lower()
        if not kw:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        ph = self._ph()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO urgent_keywords (keyword, created_at) VALUES ({ph}, {ph})",
                    (kw, now_iso),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def remove_keyword(self, keyword: str) -> bool:
        kw = keyword.strip().lower()
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM urgent_keywords WHERE keyword = {ph}", (kw,))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted

    # --- Работа со статьями ---

    def is_article_seen(self, guid: str) -> bool:
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT 1 FROM articles WHERE guid = {ph}", (guid,))
            return cur.fetchone() is not None

    def save_article(
        self,
        guid: str,
        url: str,
        title: str,
        summary: str,
        category: str,
        source_name: str,
        is_urgent: bool = False,
        gradual_sent: bool = False,
        weekly_sent: bool = False,
    ) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        ph = self._ph()
        u_val = is_urgent if self.is_postgres else int(is_urgent)
        g_val = gradual_sent if self.is_postgres else int(gradual_sent)
        w_val = weekly_sent if self.is_postgres else int(weekly_sent)
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO articles (
                        guid, url, title, summary, category, source_name,
                        is_urgent, gradual_sent, weekly_sent, created_at
                    )
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        guid,
                        url,
                        title,
                        summary,
                        category,
                        source_name,
                        u_val,
                        g_val,
                        w_val,
                        now_iso,
                    ),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def get_unsent_gradual_articles(self, limit: int = 30) -> List[Dict[str, Any]]:
        ph = self._ph()
        false_val = False if self.is_postgres else 0
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT * FROM articles
                WHERE gradual_sent = {ph}
                ORDER BY created_at ASC
                LIMIT {ph}
                """,
                (false_val, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_gradual_sent(self, article_ids: List[int]) -> None:
        if not article_ids:
            return
        ph = self._ph()
        true_val = True if self.is_postgres else 1
        placeholders = ", ".join([ph] * len(article_ids))
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE articles SET gradual_sent = {ph} WHERE id IN ({placeholders})",
                tuple([true_val] + article_ids),
            )
            conn.commit()

    def get_weekly_articles(self, days: int = 7) -> List[Dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        ph = self._ph()
        false_val = False if self.is_postgres else 0
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT * FROM articles
                WHERE created_at >= {ph} AND weekly_sent = {ph}
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (cutoff, false_val),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_weekly_sent(self, article_ids: List[int]) -> None:
        if not article_ids:
            return
        ph = self._ph()
        true_val = True if self.is_postgres else 1
        placeholders = ", ".join([ph] * len(article_ids))
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE articles SET weekly_sent = {ph} WHERE id IN ({placeholders})",
                tuple([true_val] + article_ids),
            )
            conn.commit()

    def stats(self) -> Dict[str, int]:
        false_val = False if self.is_postgres else 0
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM sources")
            sources_cnt = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM articles")
            articles_cnt = cur.fetchone()["cnt"]
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM articles WHERE gradual_sent = {ph}",
                (false_val,),
            )
            pending_gradual = cur.fetchone()["cnt"]
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM articles WHERE weekly_sent = {ph}",
                (false_val,),
            )
            pending_weekly = cur.fetchone()["cnt"]
        return {
            "sources": sources_cnt,
            "articles_total": articles_cnt,
            "pending_gradual": pending_gradual,
            "pending_weekly": pending_weekly,
        }


db = Database()
