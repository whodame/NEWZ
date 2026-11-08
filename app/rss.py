import html
import logging
import re
from typing import Any, Dict, List, Optional

import feedparser
import httpx

logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def clean_html_text(raw: str, max_len: int = 500) -> str:
    """Удаляет HTML-теги из RSS-описания и нормализует пробелы."""
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    no_tags = TAG_RE.sub(" ", unescaped)
    compact = WHITESPACE_RE.sub(" ", no_tags).strip()
    if len(compact) > max_len:
        return compact[: max_len - 3].rstrip() + "..."
    return compact


def match_urgent_keyword(title: str, summary: str, keywords: List[str]) -> Optional[str]:
    """
    Проверяет заголовок и краткое описание на наличие ключевых триггеров срочности.
    Возвращает сработавшее ключевое слово или None.
    """
    haystack = f"{title} {summary}".lower()
    for kw in keywords:
        if kw and kw.lower() in haystack:
            return kw
    return None


async def fetch_feed_entries(url: str, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Скачивает и парсит RSS/Atom ленту по URL."""
    headers = {
        "User-Agent": "NEWZ-Monitor/1.0 (+https://koyeb.com; Serious-RSS-Bot)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.text
    except Exception as exc:
        logger.warning("Ошибка загрузки RSS %s: %s", url, exc)
        return []

    parsed = feedparser.parse(content)
    results: List[Dict[str, Any]] = []
    for entry in parsed.entries[:30]:
        link = (entry.get("link") or "").strip()
        guid = (entry.get("id") or link or entry.get("title") or "").strip()
        title = clean_html_text(entry.get("title") or "Без заголовка", max_len=300)
        raw_summary = entry.get("summary") or entry.get("description") or ""
        summary = clean_html_text(raw_summary, max_len=500)
        if not guid or not link:
            continue
        results.append(
            {
                "guid": guid,
                "url": link,
                "title": title,
                "summary": summary,
            }
        )
    return results
