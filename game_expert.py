"""
Эксперт по игре — читает код из GitHub, строит индекс, ищет по коду.
"""

import json
import aiohttp
from pathlib import Path
from loguru import logger
from config import BASE_DIR, GITHUB_REPO, GITHUB_BRANCH

INDEX_FILE = BASE_DIR / "game_index.json"

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".lua", ".cs",
    ".json", ".yaml", ".yml", ".html", ".css", ".sql"
}

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist", "build"}

RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"


async def _fetch_tree() -> list[dict]:
    """Получает список всех файлов репозитория через GitHub API."""
    url = f"{API_BASE}/git/trees/{GITHUB_BRANCH}?recursive=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.error(f"GitHub API error: {resp.status}")
                return []
            data = await resp.json()
            return [
                item for item in data.get("tree", [])
                if item["type"] == "blob"
                and Path(item["path"]).suffix.lower() in CODE_EXTENSIONS
                and not any(skip in item["path"].split("/") for skip in SKIP_DIRS)
            ]


async def _fetch_file(path: str) -> str:
    """Читает содержимое файла из GitHub."""
    url = f"{RAW_BASE}/{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.text(errors="ignore")
            return ""


async def index_game() -> dict:
    """Индексирует репозиторий с GitHub."""
    logger.info(f"📚 Индексирую {GITHUB_REPO} с GitHub...")

    try:
        tree = await _fetch_tree()
    except Exception as e:
        logger.error(f"Ошибка получения дерева GitHub: {e}")
        return {"error": str(e), "files": []}

    if not tree:
        return {"error": "Репозиторий пуст или недоступен", "files": []}

    index = {
        "repo": GITHUB_REPO,
        "branch": GITHUB_BRANCH,
        "files": [],
        "functions": [],
        "classes": [],
        "total_lines": 0,
        "file_count": 0,
    }

    for item in tree[:50]:  # Не больше 50 файлов чтобы не перегрузить
        path = item["path"]
        content = await _fetch_file(path)
        if not content:
            continue

        lines = content.splitlines()
        line_count = len(lines)
        index["total_lines"] += line_count

        file_info = {
            "path": path,
            "lines": line_count,
            "size": item.get("size", 0),
            "extension": Path(path).suffix,
        }

        if Path(path).suffix == ".py":
            functions, classes = _extract_python_symbols(content)
            file_info["functions"] = functions
            file_info["classes"] = classes
            index["functions"].extend([{"name": f, "file": path} for f in functions])
            index["classes"].extend([{"name": c, "file": path} for c in classes])

        index["files"].append(file_info)
        index["file_count"] += 1

    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"✅ Индекс готов: {index['file_count']} файлов, {index['total_lines']} строк")
    return index


def _extract_python_symbols(content: str) -> tuple[list, list]:
    functions, classes = [], []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("def ") and "(" in s:
            functions.append(s[4:s.index("(")])
        elif s.startswith("class ") and ("(" in s or ":" in s):
            end = s.index("(") if "(" in s else s.index(":")
            classes.append(s[6:end])
    return functions, classes


def load_index() -> dict | None:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Ошибка загрузки индекса: {e}")
    return None


def get_project_summary() -> str:
    index = load_index()
    if not index or index.get("error"):
        return "📭 Игра не проиндексирована. Напиши /index"
    return (
        f"📚 Знаю игру ({index.get('repo', '')}): {index['file_count']} файлов, "
        f"{index['total_lines']} строк, {len(index['functions'])} функций."
    )


async def search_in_code(query: str) -> list[dict]:
    """Ищет текст в проиндексированных файлах через GitHub."""
    index = load_index()
    if not index:
        return []

    results = []
    query_lower = query.lower()

    for file_info in index.get("files", []):
        path = file_info["path"]
        content = await _fetch_file(path)
        if not content:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if query_lower in line.lower():
                results.append({"file": path, "line_num": i, "line_text": line.strip()})
                if len(results) >= 20:
                    return results

    return results


async def read_relevant_files(file_paths: list[str], max_chars: int = 30000) -> str:
    """Читает содержимое файлов из GitHub."""
    result = []
    total_chars = 0

    for path in file_paths:
        content = await _fetch_file(path)
        if not content:
            continue
        if total_chars + len(content) > max_chars:
            content = content[:max_chars - total_chars]
            result.append(f"\n--- {path} (обрезан) ---\n{content}")
            break
        result.append(f"\n--- {path} ---\n{content}")
        total_chars += len(content)

    return "\n".join(result)


def find_related_files(target_file: str) -> list[str]:
    index = load_index()
    if not index:
        return []
    target_name = Path(target_file).stem.lower()
    related = []
    for file_info in index.get("files", []):
        file_stem = Path(file_info["path"]).stem.lower()
        if file_stem != target_name and (target_name in file_stem or file_stem in target_name):
            related.append(file_info["path"])
    return related[:5]


def format_index_message() -> str:
    index = load_index()
    if not index or index.get("error"):
        return "📭 Игра не проиндексирована\n\nНапиши /index чтобы загрузить код с GitHub"

    lines = [
        f"✅ Игра проиндексирована",
        f"",
        f"Репо: {index.get('repo', '?')}",
        f"Файлов: {index['file_count']}",
        f"Строк кода: {index['total_lines']:,}",
        f"Функций: {len(index['functions'])}",
        f"Классов: {len(index['classes'])}",
    ]

    ext_counts: dict[str, int] = {}
    for f in index["files"]:
        ext = f["extension"]
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    lines.append("\nФайлы по типам:")
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"  {ext}: {count} файлов")

    return "\n".join(lines)
