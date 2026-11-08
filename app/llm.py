import html
import logging
import re
from typing import Any, Dict, List

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

# Диапазоны Unicode для фильтрации эмодзи и пиктограмм вне цитат
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)

BLOCKQUOTE_SPLIT_RE = re.compile(r"(<blockquote>.*?</blockquote>)", flags=re.DOTALL | re.IGNORECASE)


def strip_emojis_outside_quotes(text: str) -> str:
    """
    Удаляет любые эмодзи и смайлики из текста, сохраняя их только внутри <blockquote>...</blockquote>.
    """
    parts = BLOCKQUOTE_SPLIT_RE.split(text)
    cleaned_parts: List[str] = []
    for part in parts:
        if part.lower().startswith("<blockquote>"):
            cleaned_parts.append(part)
        else:
            cleaned_parts.append(EMOJI_RE.sub("", part))
    return "".join(cleaned_parts)


SYSTEM_INSTRUCTION_SERIOUS = """Ты — главный технический редактор аналитического дайджеста по системному программированию, информационной безопасности и ИТ-инфраструктуре.

ЖЕСТКИЕ ПРАВИЛА ОФОРМЛЕНИЯ:
1. Язык: строгий, грамотный, профессиональный русский язык без воды, кликбейта и разговорных штампов.
2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ любые смайлики, эмодзи и декоративные пиктограммы. Единственное исключение — дословная цитата из первоисточника, оформленная внутри тега <blockquote>...</blockquote>.
3. Разметка: используй ТОЛЬКО поддерживаемый Telegram HTML (RichText):
   - <b>жирный шрифт</b> для заголовков разделов и ключевых тезисов;
   - <i>курсив</i> для акцентов или названий изданий;
   - <code>моноширинный</code> для CVE-идентификаторов, версий пакетов, команд, путей и подсистем ядра;
   - <blockquote>цитата</blockquote> для важных прямых выдержек из оригинала;
   - <a href="URL">Текст ссылки</a> для обязательной привязки каждого пункта к первоисточнику.
4. НЕ используй Markdown (никаких **, __, ``` или [текст](url) — только чистый HTML для Telegram). Не используй теги <p>, <div>, <ul>, <li>, <br>, <h1> — для переносов строк используй обычный перевод строки.
"""


def _build_articles_Input(articles: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, art in enumerate(articles, start=1):
        lines.append(
            f"[{idx}] Категория: {art['category']} | Источник: {art['source_name']}\n"
            f"Заголовок: {art['title']}\n"
            f"Описание: {art['summary']}\n"
            f"URL: {art['url']}\n"
        )
    return "\n".join(lines)


def generate_gradual_digest(articles: List[Dict[str, Any]]) -> str:
    """
    Генерирует очередную сводку свежих новостей («потихоньку через ИИ») в строгом Telegram HTML.
    """
    if not articles:
        return ""

    if not settings.gemini_api_key:
        return _fallback_digest(articles, title="ОЧЕРЕДНАЯ СВОДКА НОВОСТЕЙ (БЕЗ ИИ — НЕ ЗАДАН GEMINI_API_KEY)")

    prompt = (
        "Ниже представлен список новых публикаций из отслеживаемых RSS-источников.\n"
        "Сформируй компактную, выдержанную аналитическую сводку на русском языке.\n\n"
        "Требования:\n"
        "- Сгруппируй материалы по их категориям (например: <b># OpenBSD</b>, <b># Linux</b>, "
        "<b># Windows &amp; Безопасность</b>, <b># Мир &amp; Технологии</b> и другим пользовательским категориям).\n"
        "- Объединяй дублирующиеся инфоповоды.\n"
        "- Для каждой значимой новости дай точный перевод/суть в 1–2 предложениях, технический контекст и "
        "обязательную HTML-ссылку <a href=\"URL\">Источник</a>.\n"
        "- Если в исходном тексте есть показательная короткая фраза, можешь привести её в <blockquote>.\n"
        "- Никаких смайликов и эмодзи.\n\n"
        "СПИСОК МАТЕРИАЛОВ:\n"
        f"{_build_articles_Input(articles)}"
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_SERIOUS,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return _fallback_digest(articles, title="СВОДКА НОВОСТЕЙ")
        header = "<b>АНАЛИТИЧЕСКАЯ СВОДКА</b>\n\n"
        return strip_emojis_outside_quotes(header + text)
    except Exception as exc:
        logger.error("Ошибка обращения к Gemini API (gradual digest): %s", exc)
        return _fallback_digest(articles, title="СВОДКА НОВОСТЕЙ (РЕЗЕРВНЫЙ РЕЖИМ)")


def generate_weekly_digest(articles: List[Dict[str, Any]]) -> str:
    """
    Генерирует итоговый еженедельный отчёт (воскресенье, 18:00 UTC) в строгом Telegram HTML.
    """
    if not articles:
        return ""

    if not settings.gemini_api_key:
        return _fallback_digest(articles, title="ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ (БЕЗ ИИ — НЕ ЗАДАН GEMINI_API_KEY)")

    prompt = (
        "Ниже представлен массив новостей за последние 7 дней.\n"
        "Сформируй итоговый еженедельный технический отчёт на русском языке.\n\n"
        "Структура отчёта:\n"
        "- Раздели отчёт на тематические блоки: <b># OpenBSD</b>, <b># Linux</b>, "
        "<b># Windows &amp; Безопасность</b>, <b># Мир &amp; Технологии</b> (а также дополнительные категории, если они присутствуют во входных данных).\n"
        "- В каждой категории отбери только самые важные и фундаментальные события (по 3–5 ключевых пунктов на категорию).\n"
        "- Исключи мелкий информационный шум, слухи и рекламные заметки.\n"
        "- Каждый пункт должен содержать: жирную суть события, краткое техническое пояснение (2–3 предложения), "
        "коды уязвимостей или версии в тегах <code>...</code> и прямую ссылку <a href=\"URL\">Источник</a>.\n"
        "- Строгий академический/инженерный тон. Полное отсутствие смайликов и эмодзи.\n\n"
        "МАТЕРИАЛЫ ЗА НЕДЕЛЮ:\n"
        f"{_build_articles_Input(articles)}"
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_SERIOUS,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return _fallback_digest(articles, title="ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ")
        header = "<b>ЕЖЕНЕДЕЛЬНЫЙ ИТОГОВЫЙ ОТЧЁТ</b>\n\n"
        return strip_emojis_outside_quotes(header + text)
    except Exception as exc:
        logger.error("Ошибка обращения к Gemini API (weekly digest): %s", exc)
        return _fallback_digest(articles, title="ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ (РЕЗЕРВНЫЙ РЕЖИМ)")


def _fallback_digest(articles: List[Dict[str, Any]], title: str) -> str:
    """Резервное форматирование в RichText HTML на случай недоступности LLM."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for art in articles:
        grouped.setdefault(art["category"], []).append(art)

    lines = [f"<b>{html.escape(title)}</b>", ""]
    for category, items in grouped.items():
        lines.append(f"<b># {html.escape(category)}</b>")
        for item in items[:7]:
            safe_title = html.escape(item["title"])
            safe_url = html.escape(item["url"], quote=True)
            safe_src = html.escape(item["source_name"])
            lines.append(f"— <b>{safe_title}</b> (<i>{safe_src}</i>) — <a href=\"{safe_url}\">Источник</a>")
        lines.append("")
    return "\n".join(lines).strip()
