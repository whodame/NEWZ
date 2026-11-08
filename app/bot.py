import asyncio
import html
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

import httpx

from app.config import settings
from app.db import db
from app.rss import fetch_feed_entries

logger = logging.getLogger(__name__)

MAX_TG_LENGTH = 4000


def split_html_message(text: str, max_len: int = MAX_TG_LENGTH) -> List[str]:
    """Разбивает длинный HTML-текст по абзацам или строкам, не превышая лимит Telegram (4096 символов)."""
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_idx = remaining.rfind("\n\n", 0, max_len)
        if split_idx == -1:
            split_idx = remaining.rfind("\n", 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        chunks.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


def format_immediate_urgent_alert(
    category: str,
    source_name: str,
    title: str,
    url: str,
    matched_trigger: str,
    summary: str = "",
) -> str:
    """
    Формирует строгое уведомление о важной новости БЕЗ участия ИИ —
    только категория, оригинальный заголовок (и краткая цитата, если есть) и прямая ссылка.
    """
    safe_cat = html.escape(category.upper())
    safe_src = html.escape(source_name)
    safe_title = html.escape(title)
    safe_url = html.escape(url, quote=True)
    safe_trigger = html.escape(matched_trigger)

    parts = [
        f"<b>СРОЧНОЕ СООБЩЕНИЕ | {safe_cat}</b>",
        f"<b>Источник:</b> <i>{safe_src}</i> | <b>Триггер:</b> <code>{safe_trigger}</code>",
        "",
        f"<b>{safe_title}</b>",
    ]
    if summary:
        safe_summary = html.escape(summary[:280])
        parts.append(f"<blockquote>{safe_summary}</blockquote>")
    parts.append("")
    parts.append(f"<a href=\"{safe_url}\">Открыть первоисточник</a>")
    return "\n".join(parts)


class TelegramService:
    """Клиент Telegram Bot API с поддержкой RichText (HTML) и динамических команд управления."""

    def __init__(self) -> None:
        self.offset: int = 0
        self._polling_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self.on_force_check: Optional[Callable[[], Coroutine[Any, Any, int]]] = None
        self.on_force_gradual: Optional[Callable[[], Coroutine[Any, Any, bool]]] = None
        self.on_force_weekly: Optional[Callable[[], Coroutine[Any, Any, bool]]] = None

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{settings.tg_token}"

    async def send_html(
        self,
        text: str,
        chat_id: Optional[str] = None,
        disable_preview: bool = True,
    ) -> bool:
        target_chat = (chat_id or settings.chat_id).strip()
        if not settings.tg_token or not target_chat:
            logger.warning("TG_TOKEN или CHAT_ID не заданы. Сообщение не отправлено.")
            return False

        chunks = split_html_message(text)
        async with httpx.AsyncClient(timeout=25.0) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": target_chat,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": disable_preview,
                }
                try:
                    resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                    if resp.status_code != 200:
                        logger.warning(
                            "Ошибка отправки HTML в Telegram (%s), повтор без parse_mode: %s",
                            resp.status_code,
                            resp.text,
                        )
                        payload.pop("parse_mode", None)
                        await client.post(f"{self.base_url}/sendMessage", json=payload)
                except Exception as exc:
                    logger.error("Сбой сети при отправке в Telegram: %s", exc)
                    return False
                await asyncio.sleep(0.3)
        return True

    async def start_polling(self) -> None:
        if not settings.tg_token:
            logger.info("TG_TOKEN не указан — опрос команд Telegram отключён.")
            return
        self._running = True
        # Удаляем старый webhook на случай, если он был выставлен ранее
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self.base_url}/deleteWebhook")
        except Exception as exc:
            logger.warning("Не удалось сбросить webhook: %s", exc)

        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("Запущен фоновый опрос команд Telegram-бота.")

    async def stop_polling(self) -> None:
        self._running = False
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={"offset": self.offset, "timeout": 25},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for upd in data.get("result", []):
                            self.offset = upd["update_id"] + 1
                            await self._handle_update(upd)
                    else:
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Ожидание переподключения getUpdates: %s", exc)
                await asyncio.sleep(5)

    async def _handle_update(self, upd: Dict[str, Any]) -> None:
        msg = upd.get("message") or upd.get("channel_post")
        if not msg:
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        # Защита: управление ботом разрешено только владельцу CHAT_ID (если CHAT_ID задан)
        if settings.chat_id and chat_id != str(settings.chat_id).strip():
            await self.send_html(
                f"<b>Отказ в доступе.</b> Ваш <code>CHAT_ID</code>: <code>{html.escape(chat_id)}</code>.",
                chat_id=chat_id,
            )
            return

        cmd_parts = text.split(maxsplit=1)
        cmd = cmd_parts[0].split("@")[0].lower()
        arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

        if cmd in ("/start", "/help"):
            await self._cmd_help(chat_id)
        elif cmd == "/sources":
            await self._cmd_list_sources(chat_id)
        elif cmd == "/add_source":
            await self._cmd_add_source(chat_id, arg)
        elif cmd == "/del_source":
            await self._cmd_del_source(chat_id, arg)
        elif cmd == "/keywords":
            await self._cmd_list_keywords(chat_id)
        elif cmd == "/add_keyword":
            await self._cmd_add_keyword(chat_id, arg)
        elif cmd == "/del_keyword":
            await self._cmd_del_keyword(chat_id, arg)
        elif cmd == "/status":
            await self._cmd_status(chat_id)
        elif cmd == "/check":
            await self._cmd_check(chat_id)
        elif cmd == "/gradual":
            await self._cmd_gradual(chat_id)
        elif cmd == "/digest":
            await self._cmd_digest(chat_id)

    async def _cmd_help(self, chat_id: str) -> None:
        help_text = (
            "<b>СИСТЕМА МОНИТОРИНГА НОВОСТЕЙ NEWZ</b>\n\n"
            "<b>Режимы доставки:</b>\n"
            "1. <b>Важные новости (без ИИ):</b> отправляются мгновенно при совпадении с ключевыми словами срочности "
            "или для источников с флагом <code>urgent</code> — только заголовок и прямая ссылка.\n"
            "2. <b>Постепенная ИИ-сводка:</b> регулярная аккуратная аналитика свежих публикаций через Gemini.\n"
            "3. <b>Еженедельный отчёт:</b> каждое воскресенье в <code>18:00 UTC</code>.\n\n"
            "<b>Управление источниками (динамически):</b>\n"
            "<code>/sources</code> — список активных RSS-источников\n"
            "<code>/add_source Категория | Название | URL</code> — добавить RSS-ленту\n"
            "<code>/add_source Категория | Название | URL | urgent</code> — добавить ленту, все новости которой шлются сразу без ИИ\n"
            "<code>/del_source ID_или_URL</code> — удалить RSS-ленту\n\n"
            "<b>Управление триггерами срочности (без ИИ):</b>\n"
            "<code>/keywords</code> — список ключевых слов мгновенной отправки\n"
            "<code>/add_keyword слово</code> — добавить ключевое слово\n"
            "<code>/del_keyword слово</code> — удалить ключевое слово\n\n"
            "<b>Ручной запуск и диагностика:</b>\n"
            "<code>/check</code> — проверить RSS-ленты прямо сейчас\n"
            "<code>/gradual</code> — прислать текущую ИИ-сводку по новым статьям\n"
            "<code>/digest</code> — сформировать еженедельный ИИ-отчёт за 7 дней\n"
            "<code>/status</code> — статистика базы и очереди"
        )
        await self.send_html(help_text, chat_id=chat_id)

    async def _cmd_list_sources(self, chat_id: str) -> None:
        sources = db.list_sources()
        if not sources:
            await self.send_html("<b>Список RSS-источников пуст.</b>", chat_id=chat_id)
            return
        lines = ["<b>ОТСЛЕЖИВАЕМЫЕ RSS-ИСТОЧНИКИ</b>", ""]
        for s in sources:
            mode = " <code>[ВСЕ СРАЗУ БЕЗ ИИ]</code>" if s.get("urgent_only") else ""
            lines.append(
                f"<b>ID <code>{s['id']}</code></b> | <b>{html.escape(s['category'])}</b> — "
                f"<i>{html.escape(s['name'])}</i>{mode}\n"
                f"<code>{html.escape(s['url'])}</code>"
            )
        await self.send_html("\n".join(lines), chat_id=chat_id)

    async def _cmd_add_source(self, chat_id: str, arg: str) -> None:
        parts = [p.strip() for p in arg.split("|") if p.strip()]
        if len(parts) == 1 and parts[0].startswith("http"):
            # Упрощённое добавление просто по URL: /add_source https://example.com/rss
            url = parts[0]
            category = "Пользовательские"
            name = url.split("/")[2] if len(url.split("/")) > 2 else url
            urgent_only = False
        elif len(parts) >= 3:
            category, name, url = parts[0], parts[1], parts[2]
            urgent_only = len(parts) >= 4 and parts[3].lower() in ("urgent", "срочно", "1", "true")
        else:
            await self.send_html(
                "<b>Неверный формат команды.</b>\n"
                "Использование:\n"
                "<code>/add_source Категория | Название | https://site.com/rss</code>\n"
                "или только ссылка:\n"
                "<code>/add_source https://site.com/rss</code>",
                chat_id=chat_id,
            )
            return

        entries = await fetch_feed_entries(url, timeout=12.0)
        added = db.add_source(category=category, name=name, url=url, urgent_only=urgent_only)
        if not added:
            await self.send_html(
                "<b>Не удалось добавить источник.</b> Возможно, такой URL уже существует в базе.",
                chat_id=chat_id,
            )
            return

        # Записываем текущие статьи нового источника как уже просмотренные, чтобы не засыпать чат старым архивом
        for entry in entries:
            db.save_article(
                guid=entry["guid"],
                url=entry["url"],
                title=entry["title"],
                summary=entry["summary"],
                category=category,
                source_name=name,
                is_urgent=False,
                gradual_sent=True,
                weekly_sent=False,
            )

        await self.send_html(
            f"<b>Источник успешно добавлен.</b>\n"
            f"Категория: <b>{html.escape(category)}</b>\n"
            f"Название: <i>{html.escape(name)}</i>\n"
            f"URL: <code>{html.escape(url)}</code>\n"
            f"Проиндексировано стартовых записей: <code>{len(entries)}</code>",
            chat_id=chat_id,
        )

    async def _cmd_del_source(self, chat_id: str, arg: str) -> None:
        if not arg:
            await self.send_html(
                "Укажите ID или URL источника: <code>/del_source 1</code>",
                chat_id=chat_id,
            )
            return
        if db.remove_source(arg):
            await self.send_html(f"<b>Источник <code>{html.escape(arg)}</code> удалён.</b>", chat_id=chat_id)
        else:
            await self.send_html(f"<b>Источник <code>{html.escape(arg)}</code> не найден.</b>", chat_id=chat_id)

    async def _cmd_list_keywords(self, chat_id: str) -> None:
        kws = db.list_keywords()
        if not kws:
            await self.send_html("<b>Список ключевых слов срочности пуст.</b>", chat_id=chat_id)
            return
        formatted = ", ".join(f"<code>{html.escape(k)}</code>" for k in kws)
        await self.send_html(
            "<b>КЛЮЧЕВЫЕ СЛОВА МГНОВЕННОЙ ОТПРАВКИ (БЕЗ ИИ)</b>\n\n"
            f"{formatted}\n\n"
            "При обнаружении любого из этих выражений в заголовке или анонсе "
            "новость отправляется немедленно прямой ссылкой.",
            chat_id=chat_id,
        )

    async def _cmd_add_keyword(self, chat_id: str, arg: str) -> None:
        if not arg:
            await self.send_html("Использование: <code>/add_keyword cve-2026</code>", chat_id=chat_id)
            return
        if db.add_keyword(arg):
            await self.send_html(
                f"Ключевое слово <code>{html.escape(arg.lower())}</code> добавлено в список срочных триггеров.",
                chat_id=chat_id,
            )
        else:
            await self.send_html("Такое ключевое слово уже присутствует в списке.", chat_id=chat_id)

    async def _cmd_del_keyword(self, chat_id: str, arg: str) -> None:
        if not arg:
            await self.send_html("Использование: <code>/del_keyword слово</code>", chat_id=chat_id)
            return
        if db.remove_keyword(arg):
            await self.send_html(
                f"Ключевое слово <code>{html.escape(arg.lower())}</code> удалено.",
                chat_id=chat_id,
            )
        else:
            await self.send_html("Ключевое слово не найдено.", chat_id=chat_id)

    async def _cmd_status(self, chat_id: str) -> None:
        st = db.stats()
        storage_type = "PostgreSQL (внешняя БД)" if db.is_postgres else f"SQLite ({settings.sqlite_path})"
        msg = (
            "<b>СТАТУС СЕРВИСА NEWZ</b>\n\n"
            f"<b>Хранилище:</b> <code>{html.escape(storage_type)}</code>\n"
            f"<b>Модель ИИ:</b> <code>{html.escape(settings.gemini_model)}</code>\n"
            f"<b>Активных источников:</b> <code>{st['sources']}</code>\n"
            f"<b>Всего обработано статей:</b> <code>{st['articles_total']}</code>\n"
            f"<b>В очереди на ближайшую ИИ-сводку:</b> <code>{st['pending_gradual']}</code>\n"
            f"<b>Накоплено для еженедельного отчёта:</b> <code>{st['pending_weekly']}</code>"
        )
        await self.send_html(msg, chat_id=chat_id)

    async def _cmd_check(self, chat_id: str) -> None:
        if not self.on_force_check:
            return
        await self.send_html("<i>Выполняется опрос RSS-источников...</i>", chat_id=chat_id)
        new_count = await self.on_force_check()
        await self.send_html(
            f"<b>Проверка завершена.</b> Новых записей обнаружено: <code>{new_count}</code>.",
            chat_id=chat_id,
        )

    async def _cmd_gradual(self, chat_id: str) -> None:
        if not self.on_force_gradual:
            return
        await self.send_html("<i>Формируется аналитическая сводка по новым публикациям...</i>", chat_id=chat_id)
        sent = await self.on_force_gradual()
        if not sent:
            await self.send_html("<b>Нет новых неопубликованных статей для сводки.</b>", chat_id=chat_id)

    async def _cmd_digest(self, chat_id: str) -> None:
        if not self.on_force_weekly:
            return
        await self.send_html("<i>Формируется итоговый еженедельный отчёт...</i>", chat_id=chat_id)
        sent = await self.on_force_weekly()
        if not sent:
            await self.send_html("<b>За последние 7 дней нет новых статей для отчёта.</b>", chat_id=chat_id)


tg_bot = TelegramService()
