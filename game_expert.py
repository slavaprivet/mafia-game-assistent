"""
Эксперт по игре — индексирует код игры, строит карту проекта,
отвечает на вопросы "где что находится" и "как это связано".
"""

import os
import json
import asyncio
from pathlib import Path
from loguru import logger
from config import GAME_REPO_PATH, BASE_DIR

# Файл индекса проекта
INDEX_FILE = BASE_DIR / "game_index.json"

# Расширения файлов которые индексируем
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".lua", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".gd",  # GDScript (Godot)
    ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".sql"
}

# Папки которые пропускаем
SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "env"
}


async def index_game() -> dict:
    """
    Сканирует папку с игрой и строит индекс.
    Возвращает словарь с информацией о проекте.
    """
    if not GAME_REPO_PATH.exists():
        logger.warning(f"Папка игры не найдена: {GAME_REPO_PATH}")
        return {"error": "Папка игры не найдена", "files": []}

    logger.info(f"📚 Индексирую игру в {GAME_REPO_PATH}...")

    index = {
        "path": str(GAME_REPO_PATH),
        "files": [],
        "functions": [],
        "classes": [],
        "total_lines": 0,
        "file_count": 0,
    }

    # Сканируем все файлы
    for file_path in GAME_REPO_PATH.rglob("*"):
        # Пропускаем папки и не-код
        if file_path.is_dir():
            continue

        # Проверяем что не в запрещённых папках
        if any(skip in file_path.parts for skip in SKIP_DIRS):
            continue

        # Только файлы с нужными расширениями
        if file_path.suffix.lower() not in CODE_EXTENSIONS:
            continue

        try:
            # Читаем файл
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            line_count = len(lines)
            index["total_lines"] += line_count

            # Информация о файле
            file_info = {
                "path": str(file_path.relative_to(GAME_REPO_PATH)),
                "lines": line_count,
                "size": file_path.stat().st_size,
                "extension": file_path.suffix,
            }

            # Извлекаем функции и классы из Python файлов
            if file_path.suffix == ".py":
                functions, classes = _extract_python_symbols(content)
                file_info["functions"] = functions
                file_info["classes"] = classes
                index["functions"].extend([
                    {"name": f, "file": file_info["path"]} for f in functions
                ])
                index["classes"].extend([
                    {"name": c, "file": file_info["path"]} for c in classes
                ])

            index["files"].append(file_info)
            index["file_count"] += 1

        except Exception as e:
            logger.warning(f"Не могу прочитать {file_path}: {e}")

    # Сохраняем индекс
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        f"✅ Индекс готов: {index['file_count']} файлов, "
        f"{index['total_lines']} строк, "
        f"{len(index['functions'])} функций"
    )

    return index


def _extract_python_symbols(content: str) -> tuple[list[str], list[str]]:
    """Извлекает имена функций и классов из Python кода."""
    functions = []
    classes = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("def ") and "(" in stripped:
            func_name = stripped[4:stripped.index("(")]
            functions.append(func_name)
        elif stripped.startswith("class ") and ("(" in stripped or ":" in stripped):
            end = stripped.index("(") if "(" in stripped else stripped.index(":")
            class_name = stripped[6:end]
            classes.append(class_name)

    return functions, classes


def load_index() -> dict | None:
    """Загружает сохранённый индекс с диска."""
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Ошибка загрузки индекса: {e}")
    return None


def get_project_summary() -> str:
    """Возвращает краткое описание проекта."""
    index = load_index()
    if not index or index.get("error"):
        return "📭 Игра не проиндексирована. Скинь папку с игрой и напиши /index"

    return (
        f"📚 Знаю игру: {index['file_count']} файлов, "
        f"{index['total_lines']} строк, "
        f"{len(index['functions'])} функций, "
        f"{len(index['classes'])} классов."
    )


async def search_in_code(query: str) -> list[dict]:
    """
    Ищет текст в коде игры.
    Возвращает список совпадений: [{file, line_num, line_text}]
    """
    if not GAME_REPO_PATH.exists():
        return []

    results = []
    query_lower = query.lower()

    for file_path in GAME_REPO_PATH.rglob("*"):
        if file_path.is_dir() or file_path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        if any(skip in file_path.parts for skip in SKIP_DIRS):
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    results.append({
                        "file": str(file_path.relative_to(GAME_REPO_PATH)),
                        "line_num": i,
                        "line_text": line.strip(),
                    })
                    if len(results) >= 20:  # Не больше 20 результатов
                        return results
        except Exception:
            pass

    return results


async def read_relevant_files(file_paths: list[str], max_chars: int = 30000) -> str:
    """
    Читает содержимое указанных файлов.
    Ограничивает общий размер чтобы не перегрузить контекст AI.
    """
    result = []
    total_chars = 0

    for rel_path in file_paths:
        abs_path = GAME_REPO_PATH / rel_path
        if not abs_path.exists():
            continue

        try:
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
            if total_chars + len(content) > max_chars:
                # Обрезаем если слишком большой
                content = content[:max_chars - total_chars]
                result.append(f"\n--- {rel_path} (обрезан) ---\n{content}")
                break

            result.append(f"\n--- {rel_path} ---\n{content}")
            total_chars += len(content)
        except Exception as e:
            logger.warning(f"Не могу прочитать {abs_path}: {e}")

    return "\n".join(result)


def find_related_files(target_file: str) -> list[str]:
    """
    Находит файлы которые могут быть связаны с указанным.
    Простой анализ по импортам и именованию.
    """
    index = load_index()
    if not index:
        return []

    related = []
    target_name = Path(target_file).stem.lower()

    # Ищем файлы с похожими именами
    for file_info in index.get("files", []):
        file_stem = Path(file_info["path"]).stem.lower()
        if file_stem != target_name and (
            target_name in file_stem or file_stem in target_name
        ):
            related.append(file_info["path"])

    return related[:5]  # Не больше 5 связанных файлов


def format_index_message() -> str:
    """Форматирует сообщение об индексе для показа пользователю."""
    index = load_index()
    if not index or index.get("error"):
        return (
            "📭 *Игра не проиндексирована*\n\n"
            "Положи код игры в папку `game_repo/` рядом с ботом,\n"
            "затем напиши `/index`"
        )

    lines = [
        f"📚 *Игра проиндексирована*",
        f"",
        f"📁 Файлов: {index['file_count']}",
        f"📝 Строк кода: {index['total_lines']:,}",
        f"🔧 Функций: {len(index['functions'])}",
        f"🏗 Классов: {len(index['classes'])}",
        f"",
        f"*Файлы по типам:*",
    ]

    # Считаем по расширениям
    ext_counts: dict[str, int] = {}
    for f in index["files"]:
        ext = f["extension"]
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"  {ext}: {count} файлов")

    return "\n".join(lines)
