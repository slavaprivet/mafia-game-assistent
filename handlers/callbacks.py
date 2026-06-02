"""
Обработчик inline-кнопок — применение/отклонение изменений через GitHub API.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Router
from aiogram.types import CallbackQuery
from loguru import logger

from config import GITHUB_REPO, GITHUB_BRANCH
from game_expert import push_file_to_github, _fetch_file
from memory import save_code_change
from handlers.text_tasks import pending_changes

router = Router()

GITHUB_PAGES_URL = f"https://{GITHUB_REPO.split('/')[0]}.github.io/{GITHUB_REPO.split('/')[1]}/"


async def _apply_via_github(file_path: str, old_code: str | None, new_code: str, task_id: int) -> tuple[bool, str]:
    """Читает файл с GitHub, заменяет код, пушит обратно."""
    content = await _fetch_file(file_path)
    if not content:
        return False, f"Не удалось прочитать {file_path} с GitHub"

    if old_code and old_code in content:
        new_content = content.replace(old_code, new_code, 1)
    elif not old_code:
        new_content = content + "\n" + new_code
    else:
        return False, (
            f"Не нашёл точный код для замены в {file_path}.\n"
            "Примени изменение вручную — скопируй блок СТАЛО из ответа."
        )

    return await push_file_to_github(
        file_path,
        new_content,
        f"fix: task-{task_id} bot automated change",
    )


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
            "⚠️ Не могу применить автоматически — файл или код не определён.\n"
            "Примени изменение вручную."
        )
        del pending_changes[task_id]
        return

    ok, msg = await _apply_via_github(
        change["file"],
        change.get("old_code"),
        change["new_code"],
        task_id,
    )

    await callback.message.edit_reply_markup()

    if ok:
        await save_code_change(
            task_id=task_id,
            file_path=change["file"],
            branch=GITHUB_BRANCH,
            commit_hash="",
            description=change.get("description", "")[:100],
            diff=change.get("new_code", "")[:500],
        )
        file_url = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{change['file']}"
        await callback.message.answer(
            f"✅ Изменение применено!\n\n"
            f"📄 Файл: {change['file']}\n"
            f"🔗 Код на GitHub: {file_url}\n\n"
            f"🌐 Проверь игру:\n{GITHUB_PAGES_URL}"
        )
    else:
        await callback.message.answer(f"❌ {msg}")

    del pending_changes[task_id]


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
        await callback.message.answer(f"❌ Не удалось прочитать {file_path} с GitHub")
        return

    preview = content[:3000]
    if len(content) > 3000:
        preview += f"\n... (ещё {len(content) - 3000} символов)"

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
        ok, msg = await _apply_via_github(
            change["file"], change.get("old_code"), change["new_code"], task_id
        )
        if ok:
            file_url = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{change['file']}"
            result = f"✅ {msg}\n🔗 {file_url}\n🌐 {GITHUB_PAGES_URL}"
        else:
            result = f"❌ {msg}"
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
    await callback.message.answer("❌ Изменение отклонено. Опиши задачу иначе если нужно другое решение.")
