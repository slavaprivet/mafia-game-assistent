"""
Работа с Git — создание веток, коммиты, откаты, diff.
"""

import subprocess
from pathlib import Path
from loguru import logger
from config import GAME_REPO_PATH


def _run_git(args: list[str], cwd: Path = None) -> tuple[bool, str]:
    """
    Запускает git команду.
    Возвращает (успех, вывод).
    """
    cwd = cwd or GAME_REPO_PATH
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        output = result.stdout.strip() or result.stderr.strip()
        success = result.returncode == 0
        if not success:
            logger.warning(f"git {' '.join(args)} вернул ошибку: {output}")
        return success, output
    except FileNotFoundError:
        return False, "Git не установлен"
    except Exception as e:
        return False, str(e)


def is_git_repo() -> bool:
    """Проверяет что папка с игрой — Git репозиторий."""
    return (GAME_REPO_PATH / ".git").exists()


def get_current_branch() -> str:
    """Возвращает имя текущей ветки."""
    ok, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return branch if ok else "unknown"


def get_status() -> str:
    """Возвращает статус изменений (git status)."""
    ok, status = _run_git(["status", "--short"])
    return status if ok else "Ошибка получения статуса"


def create_branch(branch_name: str) -> tuple[bool, str]:
    """Создаёт новую ветку и переключается на неё."""
    ok, out = _run_git(["checkout", "-b", branch_name])
    if ok:
        logger.info(f"✅ Создана ветка {branch_name}")
    return ok, out


def switch_branch(branch_name: str) -> tuple[bool, str]:
    """Переключается на существующую ветку."""
    return _run_git(["checkout", branch_name])


def commit_changes(message: str, files: list[str] = None) -> tuple[bool, str]:
    """
    Делает коммит.
    Если files не указан — коммитит все изменения (git add -A).
    """
    if files:
        # Добавляем конкретные файлы
        ok, out = _run_git(["add"] + files)
        if not ok:
            return False, f"Ошибка git add: {out}"
    else:
        # Добавляем всё
        ok, out = _run_git(["add", "-A"])
        if not ok:
            return False, f"Ошибка git add: {out}"

    # Коммитим
    ok, out = _run_git(["commit", "-m", message])
    if ok:
        # Получаем хэш коммита
        _, commit_hash = _run_git(["rev-parse", "--short", "HEAD"])
        logger.info(f"✅ Коммит {commit_hash}: {message}")
        return True, commit_hash
    return False, out


def get_diff(file_path: str = None) -> str:
    """
    Возвращает diff изменений.
    Если file_path не указан — diff всех файлов.
    """
    args = ["diff", "HEAD"]
    if file_path:
        args.append(file_path)

    ok, diff = _run_git(args)
    return diff if ok else "Нет изменений"


def rollback_file(file_path: str) -> tuple[bool, str]:
    """Откатывает изменения в конкретном файле."""
    ok, out = _run_git(["checkout", "HEAD", "--", file_path])
    if ok:
        logger.info(f"↩️ Откатил {file_path}")
    return ok, out


def rollback_last_commit() -> tuple[bool, str]:
    """Отменяет последний коммит (сохраняя изменения в рабочей папке)."""
    ok, out = _run_git(["reset", "--soft", "HEAD~1"])
    if ok:
        logger.info("↩️ Отменил последний коммит")
    return ok, out


def merge_to_main(branch_name: str) -> tuple[bool, str]:
    """Мержит ветку в main/master."""
    # Переключаемся на основную ветку
    ok, out = switch_branch("main")
    if not ok:
        ok, out = switch_branch("master")
    if not ok:
        return False, "Не могу найти ветку main или master"

    # Делаем merge
    ok, out = _run_git(["merge", branch_name, "--no-ff", "-m", f"Merge: {branch_name}"])
    return ok, out


def get_log(limit: int = 10) -> list[dict]:
    """Возвращает последние коммиты."""
    ok, out = _run_git([
        "log",
        f"-{limit}",
        "--pretty=format:%h|%an|%ai|%s"
    ])
    if not ok or not out:
        return []

    commits = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2][:10],  # только дата
                "message": parts[3],
            })
    return commits


def apply_diff_patch(diff_content: str, file_path: str) -> tuple[bool, str]:
    """
    Применяет diff патч к файлу.
    Используется для безопасного применения изменений.
    """
    import tempfile

    # Записываем патч во временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.patch',
                                     delete=False, encoding='utf-8') as f:
        f.write(diff_content)
        patch_file = f.name

    ok, out = _run_git(["apply", patch_file])
    Path(patch_file).unlink(missing_ok=True)
    return ok, out


def format_git_status() -> str:
    """Форматирует статус Git для показа пользователю."""
    if not is_git_repo():
        return "📭 Не Git репозиторий"

    branch = get_current_branch()
    status = get_status()
    log = get_log(3)

    lines = [f"🌿 Ветка: `{branch}`"]

    if status:
        lines.append(f"\n📝 Изменения:\n```\n{status}\n```")
    else:
        lines.append("\n✅ Нет изменений")

    if log:
        lines.append("\n📋 Последние коммиты:")
        for c in log:
            lines.append(f"  `{c['hash']}` {c['date']} — {c['message']}")

    return "\n".join(lines)
