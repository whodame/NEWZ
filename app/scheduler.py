import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot import format_immediate_urgent_alert, tg_bot
from app.config import settings
from app.db import db
from app.llm import generate_gradual_digest, generate_weekly_digest
from app.rss import fetch_feed_entries, match_urgent_keyword

logger = logging.getLogger(__name__)


async def poll_rss_feeds(silent_seed: bool = False) -> int:
    """
    Опрашивает все активные RSS-ленты из БД.
    Важные новости отправляет МГНОВЕННО БЕЗ ИИ (прямой ссылкой в строгом HTML).
    Остальные ставит в очередь на плавную ИИ-сводку и еженедельный дайджест.
    """
    sources = db.list_sources(only_enabled=True)
    keywords = db.list_keywords()
    new_articles_count = 0

    for src in sources:
        entries = await fetch_feed_entries(src["url"])
        # Обрабатываем от старых к новым
        for entry in reversed(entries):
            guid = entry["guid"]
            if db.is_article_seen(guid):
                continue

            title = entry["title"]
            summary = entry["summary"]
            url = entry["url"]
            category = src["category"]
            source_name = src["name"]

            matched_kw = match_urgent_keyword(title, summary, keywords)
            is_urgent = bool(src.get("urgent_only")) or (matched_kw is not None)
            trigger_label = "источник целиком" if src.get("urgent_only") else (matched_kw or "")

            if silent_seed:
                # При самом первом старте индексируем текущие ленты без спама в чат
                db.save_article(
                    guid=guid,
                    url=url,
                    title=title,
                    summary=summary,
                    category=category,
                    source_name=source_name,
                    is_urgent=is_urgent,
                    gradual_sent=True,
                    weekly_sent=False,
                )
                new_articles_count += 1
                continue

            if is_urgent:
                # Мгновенная отправка БЕЗ обработки ИИ — сразу заголовок и прямая ссылка
                alert_html = format_immediate_urgent_alert(
                    category=category,
                    source_name=source_name,
                    title=title,
                    url=url,
                    matched_trigger=trigger_label,
                    summary=summary,
                )
                await tg_bot.send_html(alert_html, disable_preview=False)
                db.save_article(
                    guid=guid,
                    url=url,
                    title=title,
                    summary=summary,
                    category=category,
                    source_name=source_name,
                    is_urgent=True,
                    gradual_sent=True,  # Уже отправлено мгновенно
                    weekly_sent=False,  # Останется для итогового недельного отчёта
                )
            else:
                db.save_article(
                    guid=guid,
                    url=url,
                    title=title,
                    summary=summary,
                    category=category,
                    source_name=source_name,
                    is_urgent=False,
                    gradual_sent=False,
                    weekly_sent=False,
                )
            new_articles_count += 1

    logger.info("Опрос RSS завершён. Новых записей: %d", new_articles_count)
    return new_articles_count


async def run_gradual_ai_digest() -> bool:
    """
    Отправляет очередную порцию накопившихся новостей через ИИ («потихоньку»),
    красиво оформленную в RichText (HTML) без смайликов.
    """
    articles = db.get_unsent_gradual_articles(limit=25)
    if not articles:
        logger.info("Нет новых статей для промежуточной ИИ-сводки.")
        return False

    digest_html = await asyncio.to_thread(generate_gradual_digest, articles)
    if not digest_html:
        return False

    sent = await tg_bot.send_html(digest_html, disable_preview=True)
    if sent:
        db.mark_gradual_sent([a["id"] for a in articles])
        logger.info("Отправлена промежуточная ИИ-сводка (%d статей).", len(articles))
    return sent


async def run_weekly_ai_digest() -> bool:
    """
    Еженедельный итоговый отчёт (каждое воскресенье в 18:00 UTC).
    """
    articles = db.get_weekly_articles(days=7)
    if not articles:
        logger.info("За последние 7 дней нет статей для еженедельного дайджеста.")
        return False

    digest_html = await asyncio.to_thread(generate_weekly_digest, articles)
    if not digest_html:
        return False

    sent = await tg_bot.send_html(digest_html, disable_preview=True)
    if sent:
        db.mark_weekly_sent([a["id"] for a in articles])
        logger.info("Отправлен еженедельный итоговый дайджест (%d статей).", len(articles))
    return sent


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # 1. Проверка RSS каждые N минут (по умолчанию 15 минут)
    scheduler.add_job(
        poll_rss_feeds,
        trigger=IntervalTrigger(minutes=settings.poll_interval_minutes),
        id="rss_poll_job",
        replace_existing=True,
        max_instances=1,
    )

    # 2. Плавная ИИ-сводка накопившихся новостей («потихоньку», по умолчанию каждые 6 часов)
    if settings.gradual_digest_hours > 0:
        scheduler.add_job(
            run_gradual_ai_digest,
            trigger=IntervalTrigger(hours=settings.gradual_digest_hours),
            id="gradual_digest_job",
            replace_existing=True,
            max_instances=1,
        )

    # 3. Еженедельный итоговый дайджест — каждое воскресенье в 18:00 UTC
    scheduler.add_job(
        run_weekly_ai_digest,
        trigger=CronTrigger(day_of_week="sun", hour=18, minute=0, timezone="UTC"),
        id="weekly_digest_job",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
