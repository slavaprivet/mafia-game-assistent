"""
Обработчик inline-кнопок.

Флоу:
  🎮 Превью       → пушит world_preview.html, даёт ссылку + кнопки "Добавить в игру" / "Отменить"
  ✅ Добавить в игру → пушит изменение прямо в world.html
  ↩️ Отменить     → откатывает (если изменение уже применено) или просто отклоняет предложение
"""

import html
import sys
import os
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import GITHUB_REPO, GITHUB_BRANCH
from game_expert import push_file_to_github, delete_file_from_github, _fetch_file, insert_into_function
from memory import save_code_change, save_rollback, get_rollback, mark_change_rolled_back, save_game_knowledge
from handlers.state import pending_changes, pending_previews

router = Router()

_owner, _repo = GITHUB_REPO.split("/")
GITHUB_PAGES_BASE = f"https://{_owner}.github.io/{_repo}"


def _preview_filename(file_path: str) -> str:
    p = Path(file_path)
    return str(p.with_name(p.stem + "_preview" + p.suffix))


def _pages_url(file_path: str) -> str:
    return f"{GITHUB_PAGES_BASE}/{file_path}"


async def _check_preview_available(bot: Bot, chat_id: int, url: str, task_id: int,
                                   retries: int = 5, interval: int = 30) -> None:
    """Фоновая задача: опрашивает GitHub Pages пока превью не появится (или истечёт таймаут)."""
    await asyncio.sleep(interval)
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            # Превью уже добавлено или отменено — молчим
            if task_id not in pending_previews:
                return
            try:
                async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        if task_id not in pending_previews:
                            return
                        await bot.send_message(
                            chat_id,
                            f"✅ Превью доступно!\n{url}\n\nЕсли всё ок — жми ✅ Добавить в игру выше.",
                        )
                        return
            except Exception:
                pass
            if attempt < retries - 1:
                await asyncio.sleep(interval)

    if task_id not in pending_previews:
        return
    await bot.send_message(
        chat_id,
        f"⚠️ Превью ещё не появилось через {retries * interval // 60} мин.\n"
        f"GitHub Pages может тупить — проверь вручную:\n{url}",
    )


async def _build_new_content(file_path: str, old_code: str | None, new_code: str, task_id: int, func_name: str | None = None) -> tuple[bool, str, str, bool]:
    """Читает файл с GitHub, вставляет изменение.
    Возвращает (ok, new_content, old_content, exact_match)."""
    content = await _fetch_file(file_path)
    if not content:
        return False, "", "", False

    old_content = content

    # Если AI указал функцию — ищем её и вставляем внутрь
    if func_name and not old_code:
        lines = content.splitlines()
        insert_at = -1
        depth = 0
        in_func = False
        for i, line in enumerate(lines):
            if func_name in line and ("function " in line or "=>" in line or "const " in line):
                in_func = True
            if in_func:
                depth += line.count("{") - line.count("}")
                if depth > 0 and depth <= 1 and in_func and i > 0:
                    insert_at = i
                if depth <= 0 and in_func and i > 0:
                    insert_at = i  # перед закрывающей }
                    break

        if insert_at > 0:
            indent = "  "
            new_lines = lines[:insert_at] + [f"{indent}{l}" for l in new_code.splitlines()] + lines[insert_at:]
            new_content = "\n".join(new_lines)
            return True, new_content, old_content, True

    if old_code and old_code in content:
        new_content = content.replace(old_code, new_code, 1)
        return True, new_content, old_content, True
    elif not old_code:
        new_content = content + "\n\n// Добавлено ботом\n" + new_code
        return True, new_content, old_content, True
    else:
        # Exact match failed — appending
        new_content = content + "\n\n// Добавлено ботом\n" + new_code
        return True, new_content, old_content, False


# ── 🎮 Превью ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("mkpreview:"))
async def callback_mkpreview(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена")
        return

    await callback.answer("⏳ Создаю превью...")
    change = change_data["change"]

    if not change.get("file") or not change.get("new_code"):
        await callback.message.answer("⚠️ Не могу создать превью — файл или код не определён.")
        return

    ok, new_content, _, exact = await _build_new_content(
        change["file"], change.get("old_code"), change["new_code"], task_id, change.get("func_name")
    )

    if not ok:
        await callback.message.answer("❌ Не удалось прочитать файл с GitHub.")
        return

    preview_path = _preview_filename(change["file"])
    func_info = f" в функцию {change['func_name']}" if change.get("func_name") and exact else ""
    warn = "" if exact else "\n\n⚠️ Точное место не найдено — код добавлен в конец файла. Проверь что всё ок."
    ok, msg = await push_file_to_github(preview_path, new_content, f"preview: task-{task_id}")

    if not ok:
        await callback.message.answer(f"❌ Не смог создать превью: {msg}")
        return

    pending_previews[task_id] = {
        "user_id": callback.from_user.id,
        "preview_path": preview_path,
        "target_path": change["file"],
        "new_content": new_content,
        "change": change,
    }
    pending_changes.pop(task_id, None)  # чистим, превью уже создано

    preview_url = _pages_url(preview_path)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Добавить в игру", callback_data=f"addtogame:{task_id}"),
            InlineKeyboardButton(text="↩️ Отменить", callback_data=f"cancelpreview:{task_id}"),
        ]
    ])

    await callback.message.edit_reply_markup()
    await callback.message.answer(
        f"🎮 Превью создаётся... Проверяю доступность автоматически.{warn}\n\n"
        f"{preview_url}",
        reply_markup=kb
    )

    asyncio.create_task(_check_preview_available(
        callback.bot, callback.message.chat.id, preview_url, task_id
    ))


# ── ✅ Добавить в игру (из превью) ───────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("addtogame:"))
async def callback_addtogame(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    preview_data = pending_previews.get(task_id)

    if not preview_data:
        await callback.answer("❌ Превью не найдено")
        return

    await callback.answer("⏳ Добавляю в игру...")

    # Пушим в основной файл
    old_content = await _fetch_file(preview_data["target_path"])
    ok, msg = await push_file_to_github(
        preview_data["target_path"],
        preview_data["new_content"],
        f"feat: task-{task_id} from preview"
    )

    if not ok:
        await callback.message.edit_text(f"❌ Ошибка: {msg}")
        return

    # Сохраняем в историю с возможностью отката
    change = preview_data["change"]
    change_id = await save_code_change(
        task_id=task_id,
        file_path=preview_data["target_path"],
        branch=GITHUB_BRANCH,
        commit_hash="",
        description=change.get("description", "")[:100],
        diff=change.get("new_code", "")[:500],
    )
    if old_content and change_id:
        await save_rollback(change_id, preview_data["target_path"], old_content)

    # Сохраняем успешный пример как знание для будущих задач
    change = preview_data["change"]
    if change.get("new_code"):
        from ai_client import get_user_model
        model = get_user_model(callback.from_user.id)
        await save_game_knowledge(
            topic=change.get("description", "")[:100],
            request=change.get("description", "")[:200],
            working_code=change.get("new_code", "")[:2000],
            func_name=change.get("func_name", ""),
            file_path=preview_data["target_path"],
            model=model,
        )

    # Удаляем превью-файл
    await delete_file_from_github(preview_data["preview_path"], f"cleanup: preview task-{task_id}")
    del pending_previews[task_id]

    game_url = _pages_url(preview_data["target_path"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Отменить изменение", callback_data=f"rollback:{change_id}")
    ]])

    await callback.message.edit_text(
        f"✅ Добавлено в игру!\n\n"
        f"🌐 Живая версия (через ~1 мин):\n{game_url}",
        reply_markup=kb
    )


# ── ✅ Добавить в игру (напрямую, без превью) ─────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("apply:"))
async def callback_apply(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    change_data = pending_changes.get(task_id)

    if not change_data:
        await callback.answer("❌ Задача не найдена или уже обработана")
        return

    await callback.answer("⏳ Добавляю в игру...")
    change = change_data["change"]

    if not change.get("file") or not change.get("new_code"):
        await callback.message.edit_reply_markup()
        await callback.message.answer("⚠️ Не могу применить — файл или код не определён. Примени вручную.")
        del pending_changes[task_id]
        return

    ok, new_content, old_content, exact = await _build_new_content(
        change["file"], change.get("old_code"), change["new_code"], task_id, change.get("func_name")
    )

    if not ok:
        await callback.message.edit_reply_markup()
        await callback.message.answer("❌ Не удалось прочитать файл с GitHub.")
        del pending_changes[task_id]
        return

    push_ok, msg = await push_file_to_github(
        change["file"], new_content, f"feat: task-{task_id} bot change"
    )

    await callback.message.edit_reply_markup()

    if push_ok:
        change_id = await save_code_change(
            task_id=task_id,
            file_path=change["file"],
            branch=GITHUB_BRANCH,
            commit_hash="",
            description=change.get("description", "")[:100],
            diff=change.get("new_code", "")[:500],
        )
        if old_content and change_id:
            await save_rollback(change_id, change["file"], old_content)

        game_url = _pages_url(change["file"])
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Отменить изменение", callback_data=f"rollback:{change_id}")
        ]]) if change_id else None

        await callback.message.answer(
            f"✅ Добавлено в игру!\n\n"
            f"🌐 Живая версия (через ~1 мин):\n{game_url}",
            reply_markup=kb
        )
    else:
        await callback.message.answer(f"❌ {msg}")

    del pending_changes[task_id]


# ── ↩️ Отменить превью ───────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("cancelpreview:"))
async def callback_cancelpreview(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    preview_data = pending_previews.get(task_id)

    await callback.answer("↩️ Отменяю...")

    if preview_data:
        await delete_file_from_github(preview_data["preview_path"], f"cleanup: cancelled preview task-{task_id}")
        del pending_previews[task_id]

    await callback.message.edit_text("↩️ Превью удалено. Опиши задачу иначе если нужно другое решение.")


# ── ↩️ Отменить предложение (без превью) ─────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    if task_id in pending_changes:
        del pending_changes[task_id]
    await callback.answer("↩️ Отменено")
    await callback.message.edit_reply_markup()
    await callback.message.answer("↩️ Отменено. Опиши задачу иначе если нужно другое решение.")


# ── ↩️ Откат применённого изменения ──────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("rollback:"))
async def callback_rollback(callback: CallbackQuery):
    change_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Откатываю...")

    rb = await get_rollback(change_id)
    if not rb:
        await callback.message.edit_text("❌ Данные для отката не найдены.")
        return

    ok, msg = await push_file_to_github(
        rb["file_path"], rb["old_content"], f"revert: rollback change-{change_id}"
    )

    if ok:
        await mark_change_rolled_back(change_id)
        game_url = _pages_url(rb["file_path"])
        await callback.message.edit_text(
            f"↩️ Изменение отменено!\n\n"
            f"🌐 Игра восстановлена (через ~1 мин):\n{game_url}"
        )
    else:
        await callback.message.edit_text(f"❌ Ошибка отката: {msg}")


# ── 📝 Показать файл ─────────────────────────────────────────────────────────

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

    safe_content = html.escape(content[:3000])
    suffix = f"\n...ещё {len(content)-3000} символов" if len(content) > 3000 else ""
    await callback.message.answer(
        f"📄 <code>{html.escape(file_path)}</code>\n\n<pre>{safe_content}{suffix}</pre>"
    )
