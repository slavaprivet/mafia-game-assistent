"""
Эксперт по игре — читает код из GitHub, строит индекс, ищет по коду.
"""

import json
import time
import aiohttp
from pathlib import Path
from loguru import logger
from config import BASE_DIR, GITHUB_REPO, GITHUB_BRANCH, GITHUB_TOKEN

# Кеш файлов в памяти: path -> (content, timestamp)
_file_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 300  # 5 минут

INDEX_FILE = BASE_DIR / "game_index.json"

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".lua", ".cs",
    ".json", ".yaml", ".yml", ".html", ".css", ".sql"
}

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist", "build"}

RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


async def _fetch_tree() -> list[dict]:
    """Получает список всех файлов репозитория через GitHub API."""
    url = f"{API_BASE}/git/trees/{GITHUB_BRANCH}?recursive=1"
    async with aiohttp.ClientSession(headers=_headers()) as session:
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
    """Читает содержимое файла из GitHub (с кешем 5 мин)."""
    now = time.time()
    if path in _file_cache:
        content, ts = _file_cache[path]
        if now - ts < CACHE_TTL:
            return content

    url = f"{RAW_BASE}/{path}"
    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                content = await resp.text(errors="ignore")
                _file_cache[path] = (content, now)
                return content
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


async def read_relevant_files(file_paths: list[str], max_chars: int = 12000, query: str = "") -> str:
    """
    Читает только релевантные куски файлов (не весь файл).
    Если есть query — вырезает контекст вокруг совпадений.
    Без query — берёт первые N строк.
    """
    result = []
    total_chars = 0
    context_lines = 60  # строк вокруг совпадения

    for path in file_paths:
        if total_chars >= max_chars:
            break

        content = await _fetch_file(path)
        if not content:
            continue

        lines = content.splitlines()
        file_budget = min(max_chars - total_chars, max_chars // len(file_paths))

        if query:
            # Находим строки с совпадениями
            query_lower = query.lower()
            keywords = [w for w in query_lower.split() if len(w) > 3]
            hit_lines = set()
            for i, line in enumerate(lines):
                line_lower = line.lower()
                if any(kw in line_lower for kw in keywords):
                    # Берём контекст вокруг совпадения
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines)
                    hit_lines.update(range(start, end))

            if hit_lines:
                # Собираем нужные строки с разделителями
                selected = []
                prev = -2
                for i in sorted(hit_lines):
                    if i > prev + 1:
                        selected.append(f"... (строка {i+1}) ...")
                    selected.append(lines[i])
                    prev = i
                snippet = "\n".join(selected)
            else:
                # Нет совпадений — берём начало файла
                snippet = "\n".join(lines[:80])
        else:
            # Без query — первые 80 строк
            snippet = "\n".join(lines[:80])

        # Обрезаем по бюджету символов
        if len(snippet) > file_budget:
            snippet = snippet[:file_budget] + "\n... (обрезано)"

        result.append(f"\n--- {path} ({len(lines)} строк, показан фрагмент) ---\n{snippet}")
        total_chars += len(snippet)

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


async def push_file_to_github(path: str, content: str, commit_message: str) -> tuple[bool, str]:
    """Сохраняет файл в GitHub репозиторий. Возвращает (успех, сообщение)."""
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN не задан"

    url = f"{API_BASE}/contents/{path}"

    # Получаем текущий SHA файла (нужен для обновления)
    sha = None
    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")

        # Загружаем файл
        import base64
        encoded = base64.b64encode(content.encode("utf-8")).decode()
        payload = {
            "message": commit_message,
            "content": encoded,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        async with session.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status in (200, 201):
                return True, f"Файл {path} сохранён на GitHub"
            else:
                text = await resp.text()
                return False, f"Ошибка GitHub API {resp.status}: {text[:200]}"


async def delete_file_from_github(path: str, commit_message: str) -> tuple[bool, str]:
    """Удаляет файл из GitHub репозитория."""
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN не задан"

    url = f"{API_BASE}/contents/{path}"

    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return False, f"Файл {path} не найден"
            data = await resp.json()
            sha = data.get("sha")

        import base64 as _b64
        payload = {"message": commit_message, "sha": sha, "branch": GITHUB_BRANCH}
        async with session.delete(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return True, f"Файл {path} удалён"
            text = await resp.text()
            return False, f"Ошибка удаления {resp.status}: {text[:200]}"


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
