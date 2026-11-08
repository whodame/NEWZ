пш# NEWZ — Микросервис мониторинга новостей для Telegram (Koyeb Eco Micro)

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=https://github.com/whodame/NEWZ&branch=main&name=newz-monitor&instance_type=free&builder=buildpack&ports=8000;http;/&env[PORT]=8000&env[POLL_INTERVAL_MINUTES]=15&env[GRADUAL_DIGEST_HOURS]=6)

Легковесный сервис мониторинга RSS-источников на базе **FastAPI**, **APScheduler** и **Google Gemini API (`google-genai`)** со строгим оформлением сообщений в Telegram RichText (`HTML`) без смайликов и информационного шума.

## Ключевые особенности
1. **Важные новости сразу и без участия ИИ:** если в заголовке или анонсе статьи встречается ключевой триггер срочности (`0-day`, `CVE-`, `errata`, `RCE`, `OpenBSD 7.`, `kernel panic` и др.) либо у источника включён режим `urgent`, бот **мгновенно** присылает заголовок и прямую ссылку на первоисточник без задержки и без пересказа ИИ.
2. **Постепенная аналитическая ИИ-сводка («потихоньку»):** каждые `GRADUAL_DIGEST_HOURS` часов (по умолчанию раз в 6 часов) накопившиеся обычные новости передаются в Gemini и приходят аккуратной структурированной сводкой по категориям.
3. **Еженедельный итоговый отчёт:** каждое воскресенье в `18:00 UTC` бот формирует фундаментальный разбор главных событий за 7 дней по рубрикам (`# OpenBSD`, `# Linux`, `# Windows & Безопасность`, `# Мир & Технологии` и вашим собственным категориям).
4. **Динамическое управление источниками прямо из Telegram:** добавляйте и удаляйте RSS-ленты и ключевые слова срочности командами в чате с ботом без пересборки кода.
5. **Строгий стиль:** используется только Telegram HTML (`<b>`, `<i>`, `<code>`, `<blockquote>`, `<a href="...">`). Любые смайлики и эмодзи запрещены системным промптом и дополнительно вырезаются пост-фильтром везде, кроме прямых цитат `<blockquote>`.

---

## Команды бота в Telegram

- `/help` — справка по всем командам.
- `/sources` — список всех отслеживаемых RSS-лент.
- `/add_source Категория | Название | https://site.com/rss` — динамически добавить новый источник.
- `/add_source Категория | Название | https://site.com/rss | urgent` — добавить источник, **все** новости из которого будут приходить мгновенно прямой ссылкой без ИИ.
- `/add_source https://site.com/rss` — быстрое добавление по одной ссылке.
- `/del_source ID_или_URL` — удалить источник по его номеру или ссылке.
- `/keywords` — показать список ключевых слов для мгновенной отправки без ИИ.
- `/add_keyword слово` — добавить новый триггер срочности.
- `/del_keyword слово` — удалить триггер срочности.
- `/check` — принудительно опросить все RSS-ленты прямо сейчас.
- `/gradual` — сформировать и прислать ИИ-сводку по накопившимся статьям прямо сейчас.
- `/digest` — сформировать и прислать еженедельный ИИ-отчёт за последние 7 дней прямо сейчас.
- `/status` — состояние базы данных и очередей.

---

## Пошаговая инструкция по деплою на Koyeb (Eco Micro — Free)

### Шаг 1. Подготовка токенов
1. **Telegram Bot:** откройте `@BotFather` в Telegram, выполните `/newbot` и скопируйте `TG_TOKEN`. Напишите вашему новому боту `/start`.
2. **Chat ID:** узнайте свой личный ID через `@userinfobot` (или ID канала/группы, куда добавлен бот) — это значение `CHAT_ID`.
3. **Gemini API Key:** создайте бесплатный ключ в [Google AI Studio](https://aistudio.google.com/) — это значение `GEMINI_API_KEY`.
4. *(Рекомендуется для сохранения динамических источников при рестартах контейнера)* Создайте бесплатную облачную БД PostgreSQL на [Neon.tech](https://neon.tech/) или [Supabase](https://supabase.com/) и скопируйте строку подключения `postgresql://...` в `DATABASE_URL`. Если `DATABASE_URL` не задан, бот автоматически использует локальный файл `news.db` (SQLite).

### Шаг 2. Публикация кода в GitHub
Инициализируйте репозиторий в папке `NEWZ` и отправьте код на GitHub:
```bash
cd c:\Users\Георгий\Desktop\NEWZ
git init
git add .
git commit -m "feat: complete Telegram news monitoring service for Koyeb"
git branch -M main
git remote add origin https://github.com/whodame/NEWZ.git
git push -u origin main
```

### Шаг 3. Создание сервиса в панели Koyeb
1. Войдите в панель [Koyeb](https://app.koyeb.com/) и нажмите **Create Web Service**.
2. Выберите способ деплоя **GitHub** и укажите ваш репозиторий и ветку `main`.
3. В разделе **Builder** оставьте **Buildpack** (Koyeb автоматически обнаружит `requirements.txt` и `Procfile` и запустит `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`).
4. В разделе **Instance** выберите тариф **Eco** -> **eNano / Eco Micro (Free)**.
5. В разделе **Environment variables** добавьте переменные:
   - `TG_TOKEN` = ваш токен от BotFather
   - `CHAT_ID` = ваш ID чата
   - `GEMINI_API_KEY` = ваш ключ Gemini API
   - `PORT` = `8000`
   - `GRADUAL_DIGEST_HOURS` = `6` (или `0`, если хотите получать ИИ-отчёт только раз в неделю по воскресеньям)
   - `DATABASE_URL` = строка подключения PostgreSQL (необязательно, по умолчанию работает локальная SQLite `news.db`)
6. В разделе **Health checks** проверьте, что порт выставлен на `8000`, протокол **HTTP**, путь **`/`** (сервис отдаёт `200 OK` `{"status": "healthy"}`).
7. Нажмите **Deploy**. После сборки напишите боту в Telegram `/status` или `/help`.
