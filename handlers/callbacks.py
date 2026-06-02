"""
Обработчик inline-кнопок — применение/откат изменений через GitHub API.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import GITHUB_REPO, GITHUB_BRANCH
from game_expert import push_file_to_github, _fetch_file
from memory import save_code_change, save_rollback, get_rollback, mark_change_rolled_back
from handlers.text_tasks import pending_changes

router = Router()

_owner, _repo = GITHUB_REPO.split("/")
GITHUB_PAGES_BASE = f"https://{_owner}.github.io/{_repo}"


def _preview_url(file_path: str) -> str:
    return f"{GITHUB_PAGES_BASE}/{file_path}"


async def _apply_via_github(file_path: str, old_code: str | None, new_code: str, task_id: int) -> tuple[bool, str, str]:
    """Читает файл с GitHub, заменяет код, пушит обратно. Возвращает (ok, msg, old_content)."""
    content = await _fetch_file(file_path)
    if not content:
        return False, f"Не удалось прочитать {file_path} с GitHub", ""

    old_content = content  # сохраняем для отката

    if old_code and old_code in content:
        new_content = content.replace(old_code, new_code, 1)
    elif not old_code:
        new_content = content + "\n" + new_code
    else:
        return False, (
            f"Не нашёл точный код для замены в {file_path}.\n"
            "Примени вручную — скопируй блок СТАЛО из ответа выше."
        ), ""

    ok, msg = await push_file_to_github(
        file_path, new_content, f"fix: task-{task_id} bot change"
    )
    return ok, msg, old_content


@router.callback_query(lambda c: c.data.startswith("apply:"))
async def callback_apply(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена или уже обработана")
        return

    await callback.answer("⏳ Применяю...")
    change = change_data["change"]

    if not change.get("file") or not change.get("new_code"):
        await callback.message.edit_reply_markup()
        await callback.message.answer(
            "⚠️ Не могу применить — файл или код не определён. Примени вручную."
        )
        del pending_changes[task_id]
        return

    ok, msg, old_content = await _apply_via_github(
        change["file"], change.get("old_code"), change["new_code"], task_id
    )

    await callback.message.edit_reply_markup()

    if ok:
        change_id_row = await save_code_change(
            task_id=task_id,
            file_path=change["file"],
            branch=GITHUB_BRANCH,
            commit_hash="",
            description=change.get("description", "")[:100],
            diff=change.get("new_code", "")[:500],
        )
        # Сохраняем старый контент для отката
        if old_content and change_id_row:
            await save_rollback(change_id_row, change["file"], old_content)

        preview = _preview_url(change["file"])
        file_url = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{change['file']}"

        rollback_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="↩️ Отменить изменение",
                callback_data=f"rollback:{change_id_row}"
            )
        ]]) if change_id_row else None

        await callback.message.answer(
            f"✅ Применено: {change['file']}\n\n"
            f"🌐 Тест (через ~1 мин):\n{preview}\n\n"
            f"🔗 Код: {file_url}",
            reply_markup=rollback_kb
        )
    else:
        await callback.message.answer(f"❌ {msg}")

    del pending_changes[task_id]


@router.callback_query(lambda c: c.data.startswith("rollback:"))
async def callback_rollback(callback: CallbackQuery):
    """Откатывает изменение — восстанавливает старый контент файла."""
    change_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Откатываю...")

    rb = await get_rollback(change_id)
    if not rb:
        await callback.message.edit_text("❌ Данные для отката не найдены.")
        return

    ok, msg = await push_file_to_github(
        rb["file_path"],
        rb["old_content"],
        f"revert: rollback change-{change_id}"
    )

    if ok:
        await mark_change_rolled_back(change_id)
        preview = _preview_url(rb["file_path"])
        await callback.message.edit_text(
            f"↩️ Откат выполнен: {rb['file_path']}\n\n"
            f"🌐 Проверь: {preview}"
        )
    else:
        await callback.message.edit_text(f"❌ Ошибка отката: {msg}")


@router.callback_query(lambda c: c.data.startswith("showfile:"))
async def callback_showfile(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена")
        return

    file_path = change_data["change"].get("file")
    if not file_path:
        await callback.answer("⚠️ Файл не определён")
        return

    await callback.answer()
    content = await _fetch_file(file_path)
    if not content:
        await callback.message.answer(f"❌ Не удалось прочитать {file_path}")
        return

    preview = content[:3000] + (f"\n...ещё {len(content)-3000} символов" if len(content) > 3000 else "")
    await callback.message.answer(f"📄 {file_path}\n\n<pre>{preview}</pre>")


@router.callback_query(lambda c: c.data.startswith("branch:"))
async def callback_branch(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена")
        return

    await callback.answer("⏳ Применяю...")
    change = change_data["change"]

    if change.get("file") and change.get("new_code"):
        ok, msg, _ = await _apply_via_github(
            change["file"], change.get("old_code"), change["new_code"], task_id
        )
        result = f"✅ {msg}\n🌐 {_preview_url(change['file'])}" if ok else f"❌ {msg}"
    else:
        result = "⚠️ Файл или код не определён"

    await callback.message.edit_reply_markup()
    await callback.message.answer(f"🌿 Тест-применение:\n\n{result}")
    del pending_changes[task_id]


@router.callback_query(lambda c: c.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    if task_id in pending_changes:
        del pending_changes[task_id]
    await callback.answer("❌ Отклонено")
    await callback.message.edit_reply_markup()
    await callback.message.answer("❌ Отклонено. Опиши иначе если нужно другое решение.")


@router.callback_query(lambda c: c.data.startswith("todo_done:"))
async def callback_todo_done(callback: CallbackQuery):
    from memory import mark_todo_done
    todo_id = int(callback.data.split(":")[1])
    await mark_todo_done(todo_id)
    await callback.answer("✅ Отмечено выполненным")
    await callback.message.edit_reply_markup()
