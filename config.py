"""
Конфигурация бота — читает настройки из файла .env
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# === TELEGRAM ===
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

ALLOWED_USERS_RAW: str = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS: list[int] = [
    int(uid.strip())
    for uid in ALLOWED_USERS_RAW.split(",")
    if uid.strip().isdigit()
]

# === AI API ===
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")  # оставлен для совместимости

# === ПУТИ ===
BASE_DIR: Path = Path(__file__).parent
GAME_REPO_PATH: Path = Path(os.getenv("GAME_REPO_PATH", str(BASE_DIR / "game_repo")))

# === GITHUB ===
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "slavaprivet/mafiozy")
GITHUB_BRANCH: str = os.getenv("GITHUB_BRANCH", "main")
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
TEMP_DIR: Path = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
DB_PATH: Path = BASE_DIR / "memory.db"

# === ЛИМИТЫ ===
DAILY_TOKEN_LIMIT: int = int(os.getenv("DAILY_TOKEN_LIMIT", "0"))
TOKEN_WARN_LEVELS: list[int] = [80, 90, 100]

# === ПРОЧЕЕ ===
MAX_CONTEXT_SIZE: int = int(os.getenv("MAX_CONTEXT_SIZE", "50000"))
CONVERSATION_HISTORY_SIZE: int = int(os.getenv("CONVERSATION_HISTORY_SIZE", "20"))
HOURLY_REPORTS: bool = os.getenv("HOURLY_REPORTS", "false").lower() == "true"


def validate_config() -> list[str]:
    errors = []
    if not BOT_TOKEN:
        errors.append("!!! BOT_TOKEN не задан в .env файле")
    if not GROQ_API_KEY:
        errors.append("!!! GROQ_API_KEY не задан в .env файле")
    if not ALLOWED_USERS:
        errors.append("... ALLOWED_USERS не задан — бот будет открыт для всех!")
    return errors
