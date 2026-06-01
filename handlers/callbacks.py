"""
Обработчик inline-кнопок — применение/отклонение изменений кода.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Router
from aiogram.types import CallbackQuery
from loguru import logger

from config import GAME_REPO_PATH
from git_manager import (
    is_git_repo, get_current_branch, create_branch,
    commit_changes, rollback_last_commit, get_diff, merge_to_main
)
from memory import save_code_change
from handlers.text_tasks import pending_changes

router = Router()


def _apply_code_change(file_path: str, old_code: str, new_code: str) -> tuple[bool, str]:
    """
    Применяет изменение кода: находит старый код и заменяет на новый.
    Возвращает (успех, сообщение).
    """
    from pathlib import Path

    abs_path = GAME_REPO_PATH / file_path
    if not abs_path.exists():
        return False, f"Файл не найден: {file_path}"

    try:
        content = abs_path.read_text(encoding="utf-8")

        if old_code and old_code in content:
            new_content = content.replace(old_code, new_code, 1)
            abs_path.write_text(new_content, encoding="utf-8")
            return True, f"Заменил код в {file_path}"
        elif old_code:
            # Попробуем найти похожий код (нечёткий поиск не делаем — просто скажем)
            return False, (
                f"Не нашёл точное совпадение в {file_path}.\n"
                f"Примени изменение вручную."
            )
        else:
            # Нет старого кода — добавляем в конец файла
            abs_path.write_text(content + "\n" + new_code, encoding="utf-8")
            return True, f"Добавил код в {file_path}"

    except Exception as e:
        return False, f"Ошибка применения: {e}"


@router.callback_query(lambda c: c.data.startswith("apply:"))
async def callback_apply(callback: CallbackQuery):
    """Применяет предложенное изменение кода."""
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена или уже обработана")
        return

    await callback.answer("⏳ Применяю...")

    change = change_data["change"]
    user_id = change_data["user_id"]

    # Проверяем есть ли файл для изменения
    if not change.get("file") or not change.get("new_code"):
        await callback.message.edit_reply_markup()
        await callback.message.answer(
            "⚠️ Не могу автоматически применить — файл или код не определён.\n\n"
            "Примени изменение вручную по описанию выше."
        )
        del pending_changes[task_id]
        return

    lines = ["🔄 *Применяю изменения...*\n"]

    # Создаём ветку если это Git репозиторий
    branch_name = None
    if is_git_repo():
        branch_name = f"fix/task-{task_id}"
        ok, out = create_branch(branch_name)
        if ok:
            lines.append(f"🌿 Создана ветка: `{branch_name}`")
        else:
            lines.append(f"⚠️ Ветку создать не удалось: {out}")

    # Применяем изменение
    success, msg = _apply_code_change(
        change["file"],
        change.get("old_code"),
        change["new_code"]
    )

    if success:
        lines.append(f"✅ {msg}")

        # Коммитим если Git
        commit_hash = None
        if is_git_repo() and branch_name:
            commit_msg = f"Fix: {change.get('description', 'automated fix')[:72]}"
            ok, commit_hash = commit_changes(commit_msg, [change["file"]])
            if ok:
                lines.append(f"📦 Коммит: `{commit_hash}`")
            else:
                lines.append(f"⚠️ Коммит не удался: {commit_hash}")

        # Сохраняем в историю
        await save_code_change(
            task_id=task_id,
            file_path=change["file"],
            branch=branch_name or get_current_branch(),
            commit_hash=commit_hash or "",
            description=change.get("description", "")[:100],
            diff=change.get("new_code", "")[:500],
        )

        lines.append("\n✅ *Изменение применено!*")
        lines.append("\n⚙️ Что дальше?")

    else:
        lines.append(f"❌ {msg}")
        lines.append("\n😞 Не удалось применить автоматически.")

    await callback.message.edit_reply_markup()
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    del pending_changes[task_id]


@router.callback_query(lambda c: c.data.startswith("showfile:"))
async def callback_showfile(callback: CallbackQuery):
    """Показывает содержимое файла который будет изменён."""
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена")
        return

    change = change_data["change"]
    file_path = change.get("file")

    if not file_path:
        await callback.answer("⚠️ Файл не определён")
        return

    await callback.answer()

    abs_path = GAME_REPO_PATH / file_path
    if not abs_path.exists():
        await callback.message.answer(f"❌ Файл не найден: `{file_path}`", parse_mode="Markdown")
        return

    try:
        content = abs_path.read_text(encoding="utf-8")
        # Показываем первые 3000 символов
        preview = content[:3000]
        if len(content) > 3000:
            preview += f"\n... (ещё {len(content) - 3000} символов)"

        await callback.message.answer(
            f"📄 *Файл: {file_path}*\n\n```\n{preview}\n```",
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка чтения файла: {e}")


@router.callback_query(lambda c: c.data.startswith("branch:"))
async def callback_branch(callback: CallbackQuery):
    """Применяет изменение в отдельную тест-ветку (не в main)."""
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена")
        return

    await callback.answer("🌿 Создаю тест-ветку...")

    change = change_data["change"]

    if not is_git_repo():
        await callback.message.answer("❌ Репозиторий не является Git проектом")
        return

    # Создаём ветку с именем test/
    branch_name = f"test/task-{task_id}"
    ok, out = create_branch(branch_name)

    if not ok:
        await callback.message.answer(f"❌ Ошибка создания ветки: {out}")
        return

    # Применяем изменение
    if change.get("file") and change.get("new_code"):
        success, msg = _apply_code_change(
            change["file"],
            change.get("old_code"),
            change["new_code"]
        )

        if success:
            commit_changes(f"Test: task-{task_id}", [change["file"]])

    await callback.message.edit_reply_markup()
    await callback.message.answer(
        f"🌿 *Изменение в тест-ветке:* `{branch_name}`\n\n"
        f"Протестируй и если всё ок — сделай merge:\n"
        f"`/git` → смотри ветки\n\n"
        f"Чтобы применить в main: напиши `merge {branch_name}`",
        parse_mode="Markdown"
    )
    del pending_changes[task_id]


@router.callback_query(lambda c: c.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery):
    """Отклоняет предложенное изменение."""
    task_id = int(callback.data.split(":")[1])

    if task_id in pending_changes:
        del pending_changes[task_id]

    await callback.answer("❌ Изменение отклонено")
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "❌ Изменение отклонено.\n\n"
        "Если хочешь другое решение — опиши задачу иначе."
    )
