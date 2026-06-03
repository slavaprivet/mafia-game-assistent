# game-dev-bot — CLAUDE.md

## Назначение
Telegram-бот-помощник для разработки игры «Мафиози».
Слава пишет идеи с телефона → бот анализирует код через GitHub API → предлагает изменения → нажимает «Применить» → изменения уходят в репо.

## Проект, которому служит бот
- Репо игры: https://github.com/slavaprivet/mafiozy
- Главный файл игры: `world.html`
- Папка игры на компе: `C:\Users\Слава\Desktop\Мафиози\`

## Стек бота
- Python + aiogram 3.x
- OpenRouter API (мультимодельный fallback: GPT OSS → Qwen → Nemotron → Gemma → DeepSeek)
- SQLite (aiosqlite) — память, история задач, откаты
- GitHub Contents API — чтение и запись файлов репо без git

## Архитектура
```
main.py          — точка входа, polling, фоновые задачи (напоминания, отчёты)
config.py        — настройки из .env
ai_client.py     — OpenRouter с авто-fallback по моделям
game_expert.py   — индекс репо через GitHub API, поиск по коду, push изменений
memory.py        — SQLite: tasks, code_changes, rollbacks, reminders, todos, conversation
limit_manager.py — учёт токенов, предупреждения
git_manager.py   — локальный git (если game_repo/ склонирован), не основной путь
teacher.py       — объяснение ошибок, ревью кода
vision.py        — анализ скриншотов/видео через Gemini Flash
voice.py         — голосовые сообщения (Whisper)
handlers/
  commands.py    — /start /help /stats /git /index /search /todo /changes /find
  text_tasks.py  — главный обработчик: NLP → AI → pending_changes
  media_tasks.py — фото, видео, документы
  callbacks.py   — кнопки apply/reject/rollback/showfile
```

## Поток задачи
1. Сообщение от пользователя → `text_tasks.py`
2. NLP-распознавание (поиск кода, статистика, смена модели и т.д.)
3. Если не распознано → AI через `ask_code_model()` с контекстом кода из GitHub
4. Если AI вернул БЫЛО/СТАЛО/Файл → кнопки применить/отклонить
5. При нажатии «Применить» → `callbacks.py` → `push_file_to_github()` → GitHub API

## .env обязательные ключи
- `BOT_TOKEN` — токен бота от @BotFather
- `OPENROUTER_API_KEY` — ключ OpenRouter (бесплатно: openrouter.ai)
- `GITHUB_TOKEN` — токен с правом push в репо slavaprivet/mafiozy
- `ALLOWED_USERS` — Telegram ID владельца (453201199)

## Правила работы
- Отвечаем по-русски
- Не смешивать код бота с кодом игры Мафиози
- Модели меняются через NLP ("смени модель") или /model — не хардкодить
- GitHub token для бота тот же что у Мафиози: хранится в .env (не в .token)
- Запуск: `python main.py` из папки game-dev-bot
