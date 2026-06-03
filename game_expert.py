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
    """Читает содержимое файла из GitHub (с кешем 5 мин).
    Сначала raw CDN, при неудаче — GitHub Contents API (свежий после пуша)."""
    import base64
    now = time.time()
    if path in _file_cache:
        content, ts = _file_cache[path]
        if now - ts < CACHE_TTL:
            return content

    async with aiohttp.ClientSession(headers=_headers()) as session:
        # 1. Быстрый raw CDN
        try:
            url = f"{RAW_BASE}/{path}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    content = await resp.text(errors="ignore")
                    if content:
                        _file_cache[path] = (content, now)
                        return content
        except Exception:
            pass

        # 2. Фоллбэк: Contents API — всегда актуален, работает для файлов <1MB
        try:
            api_url = f"{API_BASE}/contents/{path}"
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    encoded = data.get("content", "").replace("\n", "")
                    if encoded:
                        content = base64.b64decode(encoded).decode("utf-8", errors="ignore")
                        _file_cache[path] = (content, now)
                        return content
        except Exception:
            pass

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
    """Ищет текст в проиндексированных файлах, возвращает совпадение + имя функции."""
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
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # Находим имя ближайшей функции выше
                func_name = ""
                for j in range(i, max(-1, i - 80), -1):
                    s = lines[j]
                    if "function " in s and "(" in s:
                        idx = s.index("function ") + 9
                        end = s.index("(", idx) if "(" in s[idx:] else len(s)
                        func_name = s[idx:end].strip()
                        break
                results.append({
                    "file": path,
                    "line_num": i + 1,
                    "line_text": line.strip(),
                    "func_name": func_name,
                })
                if len(results) >= 20:
                    return results

    return results


def _find_function_bounds(lines: list[str], hit: int) -> tuple[int, int]:
    """
    Возвращает (start, end) функции, содержащей строку hit.
    Ищет заголовок функции вверх, затем отслеживает скобки вниз.
    """
    func_start = -1
    for i in range(hit, max(-1, hit - 120), -1):
        s = lines[i]
        if ("function " in s or ("=>" in s and "=" in s) or
                (("const " in s or "let " in s or "var " in s) and "=" in s and "{" in s)):
            func_start = i
            break

    if func_start == -1:
        # Не нашли заголовок — возвращаем ±40 строк
        return max(0, hit - 40), min(len(lines) - 1, hit + 40)

    depth = 0
    for i in range(func_start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0 and i > func_start:
            return func_start, i

    return func_start, min(len(lines) - 1, func_start + 150)


async def read_relevant_files(file_paths: list[str], max_chars: int = 12000, query: str = "") -> str:
    """
    Читает релевантные куски файлов.
    При query — извлекает целые функции с совпадениями, без обрезки посередине.
    Без query — первые 80 строк.
    """
    result = []
    total_chars = 0

    for path in file_paths:
        if total_chars >= max_chars:
            break

        content = await _fetch_file(path)
        if not content:
            continue

        lines = content.splitlines()
        file_budget = min(max_chars - total_chars, max_chars // max(len(file_paths), 1))

        if query:
            query_lower = query.lower()
            keywords = [w for w in query_lower.split() if len(w) > 3]

            # Находим строки-попадания
            hits = [
                i for i, line in enumerate(lines)
                if any(kw in line.lower() for kw in keywords)
            ]

            if hits:
                # Для каждого попадания — целая функция (объединяем пересекающиеся)
                ranges: list[tuple[int, int]] = []
                for h in hits:
                    s, e = _find_function_bounds(lines, h)
                    if ranges and s <= ranges[-1][1] + 5:
                        ranges[-1] = (ranges[-1][0], max(ranges[-1][1], e))
                    else:
                        ranges.append((s, e))

                selected = []
                prev_end = -1
                for s, e in ranges:
                    if prev_end >= 0:
                        selected.append(f"\n... (строки {prev_end+2}–{s}) пропущены ...\n")
                    selected.extend(lines[s:e + 1])
                    prev_end = e
                snippet = "\n".join(selected)
            else:
                snippet = "\n".join(lines[:80])
        else:
            snippet = "\n".join(lines[:80])

        if len(snippet) > file_budget:
            snippet = snippet[:file_budget] + "\n... (обрезано)"

        result.append(f"\n--- {path} ({len(lines)} строк) ---\n{snippet}")
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
    """
    Сохраняет файл в GitHub репозиторий через Git Data API.
    Работает с файлами любого размера (обходит лимит 1MB Contents API).
    """
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN не задан"

    import base64

    async with aiohttp.ClientSession(headers=_headers()) as session:
        try:
            # 1. Получаем SHA последнего коммита ветки
            async with session.get(
                f"{API_BASE}/git/ref/heads/{GITHUB_BRANCH}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return False, f"Не удалось получить ветку: {resp.status}"
                ref_data = await resp.json()
                latest_commit_sha = ref_data["object"]["sha"]

            # 2. Получаем дерево последнего коммита
            async with session.get(
                f"{API_BASE}/git/commits/{latest_commit_sha}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return False, f"Не удалось получить коммит: {resp.status}"
                commit_data = await resp.json()
                base_tree_sha = commit_data["tree"]["sha"]

            # 3. Создаём blob с содержимым файла
            encoded = base64.b64encode(content.encode("utf-8")).decode()
            async with session.post(
                f"{API_BASE}/git/blobs",
                json={"content": encoded, "encoding": "base64"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return False, f"Ошибка создания blob: {resp.status}: {text[:200]}"
                blob_data = await resp.json()
                blob_sha = blob_data["sha"]

            # 4. Создаём новое дерево с изменённым файлом
            async with session.post(
                f"{API_BASE}/git/trees",
                json={
                    "base_tree": base_tree_sha,
                    "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}]
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return False, f"Ошибка создания дерева: {resp.status}: {text[:200]}"
                tree_data = await resp.json()
                new_tree_sha = tree_data["sha"]

            # 5. Создаём коммит
            async with session.post(
                f"{API_BASE}/git/commits",
                json={
                    "message": commit_message,
                    "tree": new_tree_sha,
                    "parents": [latest_commit_sha]
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return False, f"Ошибка создания коммита: {resp.status}: {text[:200]}"
                new_commit_data = await resp.json()
                new_commit_sha = new_commit_data["sha"]

            # 6. Обновляем ссылку ветки
            async with session.patch(
                f"{API_BASE}/git/refs/heads/{GITHUB_BRANCH}",
                json={"sha": new_commit_sha},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return False, f"Ошибка обновления ветки: {resp.status}: {text[:200]}"

            _file_cache[path] = (content, time.time())  # свежий контент сразу в кэш
            return True, f"Файл {path} сохранён на GitHub"

        except Exception as e:
            return False, f"Ошибка пуша: {e}"


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

        payload = {"message": commit_message, "sha": sha, "branch": GITHUB_BRANCH}
        async with session.delete(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return True, f"Файл {path} удалён"
            text = await resp.text()
            return False, f"Ошибка удаления {resp.status}: {text[:200]}"


async def extract_game_patterns(world_path: str = "world.html") -> str:
    """
    Анализирует world.html и извлекает ключевые паттерны игры:
    структуру NPC, зон, зданий, ключевые функции.
    Возвращает строку для системного промпта.
    """
    content = await _fetch_file(world_path)
    if not content:
        return ""

    lines = content.splitlines()
    patterns = []

    # Ищем определения массивов NPC / зон / зданий (первые 5 строк каждого)
    _ARRAY_MARKERS = ["npc", "zone", "district", "building", "gang", "spawn", "cop", "enemy"]
    seen_arrays = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        low = stripped.lower()
        # const/let/var someArray = [ или const NPCS = {
        if any(m in low for m in _ARRAY_MARKERS):
            if ("const " in stripped or "let " in stripped or "var " in stripped) and ("=" in stripped):
                key = stripped[:40]
                if key not in seen_arrays:
                    seen_arrays.add(key)
                    snippet = "\n".join(lines[i:i+6])
                    patterns.append(f"// Структура: {snippet[:300]}")
                    if len(patterns) >= 6:
                        break

    # Ключевые функции связанные с NPC/спавном/персонажами
    _FUNC_MARKERS = ["spawnNpc", "spawnNPC", "addNpc", "createNpc", "initNpc",
                     "spawnEnemy", "addEnemy", "createCharacter", "addGangMember",
                     "spawnCop", "addCop", "createPlayer", "renderNpc", "updateNpc",
                     "initNPCs", "initZones", "initBuildings", "initGangs"]
    seen_funcs = set()
    for i, line in enumerate(lines):
        for marker in _FUNC_MARKERS:
            if marker in line and ("function " in line or "=>" in line or "const " in line):
                if marker not in seen_funcs:
                    seen_funcs.add(marker)
                    snippet = "\n".join(lines[i:i+8])
                    patterns.append(f"// Функция {marker}:\n{snippet[:400]}")
                    break

    if not patterns:
        return ""

    return (
        "\n\n--- ПАТТЕРНЫ КОДА ИГРЫ (используй как образец) ---\n"
        + "\n\n".join(patterns[:8])
        + "\n--- КОНЕЦ ПАТТЕРНОВ ---"
    )


async def find_function_in_file(file_path: str, func_name: str) -> tuple[int, int]:
    """
    Ищет функцию по имени в файле.
    Возвращает (start_line, end_line) или (-1, -1) если не найдено.
    """
    content = await _fetch_file(file_path)
    if not content:
        return -1, -1

    lines = content.splitlines()
    start = -1
    depth = 0

    for i, line in enumerate(lines):
        if func_name in line and ("function " in line or "=>" in line or "const " in line):
            start = i
            depth = 0

        if start >= 0:
            depth += line.count("{") - line.count("}")
            if depth <= 0 and i > start:
                return start, i

    return start, len(lines) - 1 if start >= 0 else -1


async def insert_into_function(file_path: str, func_name: str, new_code: str, commit_msg: str) -> tuple[bool, str]:
    """
    Вставляет новый код в конец указанной функции.
    """
    content = await _fetch_file(file_path)
    if not content:
        return False, f"Не удалось прочитать {file_path}"

    lines = content.splitlines()
    start, end = await find_function_in_file(file_path, func_name)

    if start == -1:
        return False, f"Функция {func_name} не найдена"

    # Вставляем перед закрывающей скобкой функции
    indent = "    "
    insert_lines = [f"{indent}{l}" for l in new_code.splitlines()]
    new_lines = lines[:end] + insert_lines + lines[end:]
    new_content = "\n".join(new_lines)

    return await push_file_to_github(file_path, new_content, commit_msg)


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
