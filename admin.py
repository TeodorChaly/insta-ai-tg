"""
Админ-команды (только для ADMIN_ID):

  /addcredits <user_id> <amount>    — добавить кредиты
  /removecredits <user_id> <amount> — снять кредиты
  /setcredits <user_id> <amount>    — установить точное значение
  /userinfo <user_id>               — баланс и фильтр пользователя
  /users                            — список последних 20 пользователей
"""

import json
from pathlib import Path

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import config
import storage

router = Router()

_USERS_FILE = Path(__file__).parent / "users.json"


def _is_admin(msg: Message) -> bool:
    return msg.from_user.id == config.ADMIN_ID


def _load_users() -> list[dict]:
    if not _USERS_FILE.exists():
        return []
    users = []
    for line in _USERS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            users.append(json.loads(line))
        except Exception:
            pass
    return users


# ── /addcredits ───────────────────────────────────────────────────────────────

@router.message(Command("addcredits"))
async def cmd_add_credits(msg: Message) -> None:
    if not _is_admin(msg):
        return
    parts = (msg.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await msg.answer("Использование: /addcredits [user_id] [amount]", parse_mode=None)
        return
    user_id = int(parts[1])
    amount  = int(parts[2])
    new_bal = await storage.add_credits(user_id, amount)
    await msg.answer(
        f"✅ Пользователю <code>{user_id}</code> добавлено <b>{amount}</b>.\n"
        f"Новый баланс: <b>{new_bal}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /removecredits ────────────────────────────────────────────────────────────

@router.message(Command("removecredits"))
async def cmd_remove_credits(msg: Message) -> None:
    if not _is_admin(msg):
        return
    parts = (msg.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await msg.answer("Использование: /removecredits [user_id] [amount]", parse_mode=None)
        return
    user_id = int(parts[1])
    amount  = int(parts[2])
    current = storage.get_credits(user_id)
    deduct  = min(amount, current)
    new_bal = await storage.add_credits(user_id, -deduct)
    await msg.answer(
        f"✅ У пользователя <code>{user_id}</code> снято <b>{deduct}</b>.\n"
        f"Новый баланс: <b>{new_bal}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /setcredits ───────────────────────────────────────────────────────────────

@router.message(Command("setcredits"))
async def cmd_set_credits(msg: Message) -> None:
    if not _is_admin(msg):
        return
    parts = (msg.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await msg.answer("Использование: /setcredits [user_id] [amount]", parse_mode=None)
        return
    user_id = int(parts[1])
    amount  = max(0, int(parts[2]))
    current = storage.get_credits(user_id)
    diff    = amount - current
    new_bal = await storage.add_credits(user_id, diff)
    await msg.answer(
        f"✅ Баланс пользователя <code>{user_id}</code> установлен: <b>{new_bal}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /userinfo ─────────────────────────────────────────────────────────────────

@router.message(Command("userinfo"))
async def cmd_user_info(msg: Message) -> None:
    if not _is_admin(msg):
        return
    parts = (msg.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await msg.answer("Использование: /userinfo [user_id]", parse_mode=None)
        return
    user_id = int(parts[1])
    data    = storage.load(user_id)
    if not data:
        await msg.answer(f"❌ Пользователь <code>{user_id}</code> не найден.", parse_mode=ParseMode.HTML)
        return

    cfg     = data.get("filter", {})
    credits = data.get("credits", 0)
    liked   = len(data.get("liked", []))
    lang    = data.get("lang", "en")
    deleted = data.get("was_deleted", False)

    lines = [
        f"👤 <b>User {user_id}</b>",
        f"Язык: <b>{lang}</b>",
        f"Кредиты: <b>{credits}</b>",
        f"Лайков: <b>{liked}</b>",
    ]
    if cfg:
        lines.append(
            f"Фильтр: пол={cfg.get('gender','?')}  страна={cfg.get('country','?')}  "
            f"мин.фото={cfg.get('min_photos','?')}  макс.подп={cfg.get('max_followers','?')}"
        )
    if deleted:
        lines.append("⚠️ Профиль удалён")

    await msg.answer("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /users ────────────────────────────────────────────────────────────────────

@router.message(Command("users"))
async def cmd_users(msg: Message) -> None:
    if not _is_admin(msg):
        return
    users = _load_users()
    if not users:
        await msg.answer("Пользователей нет.")
        return

    last20 = users[-20:][::-1]
    lines  = [f"👥 <b>Последние {len(last20)} из {len(users)} пользователей:</b>\n"]
    for u in last20:
        uid     = u.get("id", "?")
        uname   = f"@{u['username']}" if u.get("username") else "—"
        fname   = u.get("first_name", "")
        creds   = u.get("credits", "?")
        joined  = u.get("joined_at", "")[:10]
        deleted = " 🗑" if u.get("deleted_at") else ""
        lines.append(f"<code>{uid}</code>  {uname}  {fname}  💳{creds}  <i>{joined}</i>{deleted}")

    await msg.answer("\n".join(lines), parse_mode=ParseMode.HTML)
