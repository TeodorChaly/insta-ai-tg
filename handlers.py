"""
FSM-состояния и все aiogram-хендлеры.

Порядок состояний:
  Setup.lang → Setup.gender → Scan.username → Scan.mode → Scan.limit → Scan.swiping

/settings и пагинация /liked работают из любого состояния.
"""

import asyncio
import time
import json
import base64
import datetime
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, InputMediaPhoto,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.enums import ParseMode

import config
import filters as flt
import instagram
import storage
import vision
import keyboards as kb
import deep_search
from i18n import t
from logger import scan_context, hiker_log, openai_log, summary_log
from payments import buy_kb, credits_text

router = Router()


# ── Состояния ─────────────────────────────────────────────────────────────────

class Setup(StatesGroup):
    lang          = State()
    terms         = State()
    gender        = State()
    country       = State()
    min_photos    = State()
    max_followers = State()


class Scan(StatesGroup):
    username = State()
    mode     = State()
    limit    = State()
    swiping  = State()


class DeepSearch(StatesGroup):
    count   = State()
    confirm = State()


# ── Вспомогательные функции ───────────────────────────────────────────────────

async def _lang(state: FSMContext, user_id: int) -> str:
    data = await state.get_data()
    return data.get("lang") or storage.get_lang(user_id)


async def _check_terms(
    event: Message | CallbackQuery,
    state: FSMContext,
    user_id: int,
) -> bool:
    """Возвращает True если terms приняты. Иначе отправляет напоминание и False."""
    if storage.get_terms_accepted(user_id):
        return True
    lang = await _lang(state, user_id)
    text    = t("terms_required", lang)
    markup  = kb.terms_required_kb(lang)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    return False


async def _send_card(bot: Bot, chat_id: int, state: FSMContext, idx: int,
                     user_id: int = 0) -> None:
    data     = await state.get_data()
    profiles = data.get("profiles", [])
    total    = len(profiles)
    lang     = data.get("lang") or (storage.get_lang(user_id) if user_id else "en")

    if idx >= total:
        next_pid   = data.get("next_page_id", "")
        mode       = data.get("mode", "")
        scan_likes = data.get("scan_likes", 0)
        has_more   = bool(next_pid)

        text = kb.end_summary(total, scan_likes, lang)
        if not has_more and mode in ("followers", "following"):
            key  = "no_more_followers" if mode == "followers" else "no_more_following"
            text += t(key, lang)

        has_liked = bool(data.get("liked"))
        end_msg = await bot.send_message(
            chat_id, text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=kb.end_kb(has_more, lang, has_liked),
        )
        await state.update_data(end_msg_id=end_msg.message_id)
        return

    u        = profiles[idx]
    username = u.get("username", "")
    caption  = kb.card_caption(u, lang)
    all_pics = [p for p in (u.get("_photos") or []) + [u.get("_pic")] if p][:10]

    msg_ids:   list[int] = []
    kb_msg_id: int | None = None

    counter = f"<b>@{username}</b>  ·  {idx + 1} / {total}"

    try:
        if not all_pics:
            msg = await bot.send_message(
                chat_id, caption, parse_mode=ParseMode.HTML,
            )
            msg_ids = [msg.message_id]

        elif len(all_pics) == 1:
            msg = await bot.send_photo(
                chat_id,
                photo=BufferedInputFile(all_pics[0], "photo.jpg"),
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            msg_ids = [msg.message_id]

        else:
            media = [
                InputMediaPhoto(
                    media=BufferedInputFile(b, f"p{i}.jpg"),
                    caption=(caption if i == 0 else None),
                    parse_mode=(ParseMode.HTML if i == 0 else None),
                )
                for i, b in enumerate(all_pics)
            ]
            group = await bot.send_media_group(chat_id, media=media)
            msg_ids = [m.message_id for m in group]

        # счётчик + кнопки — всегда отдельным сообщением
        km = await bot.send_message(
            chat_id, counter,
            parse_mode=ParseMode.HTML,
            reply_markup=kb.card_kb(idx, username, lang),
        )
        msg_ids.append(km.message_id)
        kb_msg_id = km.message_id

    except Exception:
        try:
            msg = await bot.send_message(
                chat_id, caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb.card_kb(idx, username, lang),
            )
            msg_ids, kb_msg_id = [msg.message_id], msg.message_id
        except Exception as e:
            print(f"[_send_card] {e}")
            return

    await state.update_data(card_msg_ids=msg_ids, kb_msg_id=kb_msg_id)


async def _delete_card(bot: Bot, chat_id: int, data: dict) -> None:
    for mid in data.get("card_msg_ids", []):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


_DEBUG_DIR = Path(__file__).parent / "debug"


def _save_last_links(
    all_profiles: list[dict],
    target: str,
    hiker: int = 0,
    openai: int = 0,
) -> None:
    """Сохраняет все профили скана с пометкой shown/not shown и причиной."""
    _DEBUG_DIR.mkdir(exist_ok=True)
    ts    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shown = sum(1 for u in all_profiles if u.get("_reject_reason") is None)
    total = len(all_profiles)

    # ── шапка ─────────────────────────────────────────────────────────────────
    sep = "─" * 72
    lines = [
        sep,
        f"  Скан: @{target}",
        f"  Дата: {ts}",
        f"  Профилей: {shown} показано  /  {total} всего",
        f"  API:  HikerAPI = {hiker} запросов   OpenAI = {openai} запросов",
        sep,
        "",
    ]

    # ── строки профилей ───────────────────────────────────────────────────────
    for u in all_profiles:
        username = u.get("username", "")
        name     = (u.get("full_name") or "").strip()
        fc       = u.get("follower_count")
        reject   = u.get("_reject_reason")

        url      = f"https://www.instagram.com/{username}/"
        name_str = f"  —  {name}" if name else ""
        fc_str   = f"  ({fc:,} подп.)" if fc is not None else ""

        if reject is None:
            lines.append(f"✅  {url}{name_str}{fc_str}")
        else:
            lines.append(f"❌  [{reject}]  {url}{name_str}{fc_str}")

    lines.append("")
    lines.append(sep)

    path = _DEBUG_DIR / "last_links.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[debug] ссылки сохранены → {path.name} ({shown} shown / {total} total)", flush=True)


def _write_debug_json(profiles: list[dict], target: str) -> None:
    _DEBUG_DIR.mkdir(exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _DEBUG_DIR / f"{target}_{ts}.json"

    # оставляем только последние 5 файлов
    existing = sorted(_DEBUG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in existing[:-4]:  # удаляем всё кроме 4 последних (5-й — текущий)
        old.unlink(missing_ok=True)

    out = []
    for u in profiles:
        entry = {k: v for k, v in u.items() if k not in ("_pic", "_photos")}
        entry["profile_url"] = f"https://www.instagram.com/{u.get('username', '')}/"
        if u.get("_pic"):
            entry["pic_base64"] = "data:image/jpeg;base64," + base64.b64encode(u["_pic"]).decode()
        out.append(entry)

    path.write_text(json.dumps({
        "scanned_at": ts,
        "target": target,
        "total": len(profiles),
        "profiles": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[debug] сохранено {len(out)} профилей → {path.name}", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    tg_user    = msg.from_user.id
    is_new     = storage.register_user(tg_user, msg.from_user.username, msg.from_user.first_name)
    liked      = storage.get_liked(tg_user)
    cfg        = storage.get_filter(tg_user)
    lang       = storage.get_lang(tg_user)
    has_filter = storage.load(tg_user).get("filter") is not None

    await state.update_data(liked=liked, filter=cfg, lang=lang,
                            card_msg_ids=[], kb_msg_id=None)

    if has_filter:
        balance = storage.get_credits(tg_user)
        await state.set_state(Scan.username)
        await msg.answer(
            t("welcome_back", lang,
              settings=kb.settings_text(cfg, lang),
              credits=balance),
            parse_mode=ParseMode.HTML,
        )
    else:
        await state.set_state(Setup.lang)
        await msg.answer(
            t("lang_select", lang),
            reply_markup=kb.lang_kb(),
        )


# ══════════════════════════════════════════════════════════════════════════════
# /lang — смена языка из любого состояния
# ══════════════════════════════════════════════════════════════════════════════

@router.message(Command("lang"))
async def cmd_lang(msg: Message, state: FSMContext) -> None:
    lang = await _lang(state, msg.from_user.id)
    await msg.answer(t("lang_current", lang), reply_markup=kb.lang_kb())


@router.callback_query(F.data.startswith("set_lang:"))
async def on_set_lang(query: CallbackQuery, state: FSMContext) -> None:
    from i18n import SUPPORTED
    new_lang = query.data.split(":")[1]
    if new_lang not in SUPPORTED:
        await query.answer()
        return

    storage.save_lang(query.from_user.id, new_lang)
    await state.update_data(lang=new_lang)

    await query.message.edit_text(t("lang_updated", new_lang))
    await query.answer()

    # если пользователь в Setup.lang — показываем terms
    current_state = await state.get_state()
    if current_state == Setup.lang:
        await state.set_state(Setup.terms)
        await query.message.answer(
            t("terms_text", new_lang),
            parse_mode=ParseMode.HTML,
            reply_markup=kb.terms_kb(new_lang),
        )


# ══════════════════════════════════════════════════════════════════════════════
# TERMS
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "terms:show")
async def on_terms_show(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang = await _lang(state, query.from_user.id)
    await query.message.answer(
        t("terms_text", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.terms_kb(lang),
    )


@router.callback_query(F.data == "terms:accept")
async def on_terms_accept(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang = await _lang(state, query.from_user.id)
    storage.set_terms_accepted(query.from_user.id)
    await state.update_data(terms_accepted=True)
    await query.message.delete()

    current_state = await state.get_state()
    if current_state == Setup.terms:
        await state.set_state(Setup.gender)
        await query.message.answer(
            t("setup_welcome", lang) + t("setup_q1_gender", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=kb.setup_gender_kb(lang),
        )


@router.callback_query(F.data == "terms:decline")
async def on_terms_decline(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state, query.from_user.id)
    await query.answer(t("terms_declined", lang), show_alert=True)


# ══════════════════════════════════════════════════════════════════════════════
# SETUP — 4 вопроса
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(Setup.gender, F.data.startswith("setup_gender:"))
async def setup_gender(query: CallbackQuery, state: FSMContext) -> None:
    gender = query.data.split(":")[1]
    data   = await state.get_data()
    lang   = data.get("lang", "en")
    cfg    = flt.DEFAULT_FILTER.copy()
    cfg["gender"] = gender
    await state.update_data(filter=cfg)
    storage.save_filter(query.from_user.id, cfg)
    await state.set_state(Scan.username)

    await query.message.edit_text(
        t("setup_complete", lang),
        parse_mode=ParseMode.HTML,
    )
    await query.message.answer(
        t("setup_prompt", lang),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    await query.answer()


# ══════════════════════════════════════════════════════════════════════════════
# /settings — просмотр и изменение фильтра
# ══════════════════════════════════════════════════════════════════════════════

@router.message(Command("settings"))
@router.message(Command("profile"))
async def cmd_settings(msg: Message, state: FSMContext) -> None:
    lang    = await _lang(state, msg.from_user.id)
    data    = await state.get_data()
    cfg     = data.get("filter") or storage.get_filter(msg.from_user.id)
    balance = storage.get_credits(msg.from_user.id)
    await msg.answer(
        kb.settings_text(cfg, lang, credits=balance) + "\n\n" + t("settings_note", lang),
        reply_markup=kb.settings_main_kb(lang),
    )


@router.callback_query(F.data.startswith("settings:"))
async def on_settings_nav(query: CallbackQuery, state: FSMContext) -> None:
    param = query.data.split(":")[1]
    lang  = await _lang(state, query.from_user.id)
    data  = await state.get_data()
    cfg   = data.get("filter", flt.DEFAULT_FILTER.copy())

    if param == "back":
        balance = storage.get_credits(query.from_user.id)
        await query.message.edit_text(
            kb.settings_text(cfg, lang, credits=balance) + "\n\n" + t("settings_note", lang),
            reply_markup=kb.settings_main_kb(lang),
        )
    elif param == "delete":
        await query.message.edit_text(
            t("delete_confirm_text", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=kb.delete_profile_kb(lang),
        )
    else:
        title_key = {
            "gender":        "settings_edit_gender",
            "country":       "settings_edit_country",
            "min_photos":    "settings_edit_minphotos",
            "max_followers": "settings_edit_maxfollowers",
        }.get(param, "")
        balance = storage.get_credits(query.from_user.id)
        await query.message.edit_text(
            kb.settings_text(cfg, lang, credits=balance) + f"\n\n<b>{t(title_key, lang)}</b>",
            reply_markup=kb.settings_option_kb(param, lang),
        )
    await query.answer()


@router.callback_query(F.data == "delete_profile:confirm")
async def on_delete_confirm(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang    = await _lang(state, query.from_user.id)
    storage.delete_user(query.from_user.id)
    await state.clear()
    await query.message.edit_text(
        t("delete_done", lang),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("set_"))
async def on_set_param(query: CallbackQuery, state: FSMContext) -> None:
    raw   = query.data[4:]
    param, value = raw.split(":", 1)
    if param in ("min_photos", "max_followers"):
        value = int(value)  # type: ignore[assignment]

    lang = await _lang(state, query.from_user.id)
    data = await state.get_data()
    cfg  = data.get("filter", flt.DEFAULT_FILTER.copy())
    cfg[param] = value
    await state.update_data(filter=cfg)
    storage.save_filter(query.from_user.id, cfg)

    balance = storage.get_credits(query.from_user.id)
    await query.message.edit_text(
        kb.settings_text(cfg, lang, credits=balance) + "\n\n" + t("settings_note", lang),
        reply_markup=kb.settings_main_kb(lang),
    )
    await query.answer(t("settings_saved", lang))


# ══════════════════════════════════════════════════════════════════════════════
# Утилиты: /new, /liked, /cancel
# ══════════════════════════════════════════════════════════════════════════════

@router.message(Command("new"))
async def cmd_new(msg: Message, state: FSMContext) -> None:
    tg_user = msg.from_user.id
    if not await _check_terms(msg, state, tg_user):
        return
    lang    = await _lang(state, tg_user)
    data    = await state.get_data()
    liked   = data.get("liked") or storage.get_liked(tg_user)
    cfg     = data.get("filter") or storage.get_filter(tg_user)
    await state.set_state(Scan.username)
    await state.update_data(profiles=[], target=None, liked=liked, filter=cfg,
                            card_msg_ids=[], kb_msg_id=None)
    await msg.answer(t("new_prompt", lang))


@router.message(Command("liked"))
async def cmd_liked(msg: Message, state: FSMContext) -> None:
    lang  = await _lang(state, msg.from_user.id)
    data  = await state.get_data()
    liked = data.get("liked") or storage.get_liked(msg.from_user.id)
    if not liked:
        await msg.answer(t("liked_empty", lang))
        return
    text, markup = kb.liked_page(liked, page=0, lang=lang)
    await msg.answer(text, parse_mode=ParseMode.HTML,
                     disable_web_page_preview=True, reply_markup=markup)


# liked_page:{prev_page}:{new_page}:{edit_mode}
@router.callback_query(F.data.startswith("liked_page:"))
async def on_liked_page(query: CallbackQuery, state: FSMContext) -> None:
    parts     = query.data.split(":")
    new_page  = int(parts[2]) if len(parts) > 2 else int(parts[1])
    edit_mode = bool(int(parts[3])) if len(parts) > 3 else False
    lang      = await _lang(state, query.from_user.id)
    data      = await state.get_data()
    liked     = data.get("liked") or storage.get_liked(query.from_user.id)
    if not liked:
        await query.message.edit_text(t("liked_empty", lang))
        await query.answer()
        return
    text, markup = kb.liked_page(liked, new_page, lang, edit_mode=edit_mode)
    await query.message.edit_text(text, parse_mode=ParseMode.HTML,
                                  disable_web_page_preview=True, reply_markup=markup)
    await query.answer()


@router.callback_query(F.data.startswith("liked_del:"))
async def on_liked_del(query: CallbackQuery, state: FSMContext) -> None:
    parts    = query.data.split(":")
    idx      = int(parts[1])
    page     = int(parts[2]) if len(parts) > 2 else 0
    lang     = await _lang(state, query.from_user.id)
    data     = await state.get_data()
    liked    = list(data.get("liked") or storage.get_liked(query.from_user.id))

    if 0 <= idx < len(liked):
        removed = liked.pop(idx)
        await state.update_data(liked=liked)
        storage.save_liked(query.from_user.id, liked)
        await query.answer(t("liked_deleted_one", lang, username=removed.get("username", "")))
    else:
        await query.answer()
        return

    if not liked:
        await query.message.edit_text(t("liked_empty", lang))
        return

    # остаёмся на той же странице, но не выходим за пределы
    total_pages = max(1, (len(liked) + kb.PAGE_SIZE - 1) // kb.PAGE_SIZE)
    page = min(page, total_pages - 1)
    text, markup = kb.liked_page(liked, page, lang, edit_mode=True)
    await query.message.edit_text(text, parse_mode=ParseMode.HTML,
                                  disable_web_page_preview=True, reply_markup=markup)


@router.callback_query(F.data == "liked_clear:ask")
async def on_liked_clear_ask(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang  = await _lang(state, query.from_user.id)
    liked = (await state.get_data()).get("liked") or storage.get_liked(query.from_user.id)
    await query.message.edit_text(
        t("liked_clear_confirm", lang, n=len(liked)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.liked_clear_kb(lang),
    )


@router.callback_query(F.data == "liked_clear:confirm")
async def on_liked_clear_confirm(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer(t("liked_cleared", await _lang(state, query.from_user.id)))
    lang = await _lang(state, query.from_user.id)
    await state.update_data(liked=[])
    storage.save_liked(query.from_user.id, [])
    await query.message.edit_text(t("liked_empty", lang))


@router.message(Command("credits"))
@router.message(Command("buy"))
async def cmd_credits(msg: Message, state: FSMContext) -> None:
    lang    = await _lang(state, msg.from_user.id)
    balance = storage.get_credits(msg.from_user.id)
    await msg.answer(
        credits_text(balance, lang),
        parse_mode=ParseMode.HTML,
        reply_markup=buy_kb(lang),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Прямая отправка Instagram-ссылки
# ══════════════════════════════════════════════════════════════════════════════

@router.message(
    ~StateFilter(Setup.lang, Setup.terms, Setup.gender, Scan.username),
    F.text.contains("instagram.com") | F.text.regexp(r"^@\w+"),
)
async def on_direct_url(msg: Message, state: FSMContext) -> None:
    tg_user  = msg.from_user.id
    if deep_search.is_running(tg_user):
        lang = await _lang(state, tg_user)
        await msg.answer(t("deep_busy", lang))
        return
    if not await _check_terms(msg, state, tg_user):
        return
    lang     = await _lang(state, tg_user)
    username = instagram.extract_username(msg.text.strip())
    wait     = await msg.answer(t("searching", lang, username=username))

    info = await instagram.resolve_user(username)
    if not info or not info.get("user_id"):
        await wait.edit_text(t("not_found", lang, username=username))
        return

    data  = await state.get_data()
    liked = data.get("liked") or storage.get_liked(tg_user)
    cfg   = data.get("filter") or storage.get_filter(tg_user)
    storage.register_user(tg_user, msg.from_user.username, msg.from_user.first_name)

    await state.set_state(Scan.mode)
    await state.update_data(target=info, liked=liked, filter=cfg,
                            card_msg_ids=[], kb_msg_id=None)

    priv = t("privacy_private", lang) if info.get("is_private") else t("privacy_public", lang)
    fn   = f"  —  {info['full_name']}" if info.get("full_name") else ""
    await wait.edit_text(
        t("user_found", lang,
          username=info["username"], fullname=fn,
          followers=kb._fmt(info.get("followers")),
          following=kb._fmt(info.get("following")),
          privacy=priv),
        reply_markup=kb.mode_kb(lang),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCAN — username → mode → limit → swiping
# ══════════════════════════════════════════════════════════════════════════════

@router.message(Scan.username, F.text)
async def on_username(msg: Message, state: FSMContext) -> None:
    if not await _check_terms(msg, state, msg.from_user.id):
        return
    lang     = await _lang(state, msg.from_user.id)
    username = instagram.extract_username(msg.text.strip())
    wait     = await msg.answer(t("searching", lang, username=username))

    info = await instagram.resolve_user(username)
    if not info or not info.get("user_id"):
        await wait.edit_text(t("not_found_retry", lang, username=username))
        return

    await state.update_data(target=info)
    await state.set_state(Scan.mode)

    priv = t("privacy_private", lang) if info.get("is_private") else t("privacy_public", lang)
    fn   = f"  —  {info['full_name']}" if info.get("full_name") else ""
    await wait.edit_text(
        t("user_found", lang,
          username=info["username"], fullname=fn,
          followers=kb._fmt(info.get("followers")),
          following=kb._fmt(info.get("following")),
          privacy=priv),
        reply_markup=kb.mode_kb(lang),
    )


@router.callback_query(Scan.mode, F.data.startswith("mode:"))
async def on_mode(query: CallbackQuery, state: FSMContext) -> None:
    mode = query.data.split(":")[1]
    lang = await _lang(state, query.from_user.id)
    await state.update_data(mode=mode)

    if mode == "deep":
        await state.set_state(DeepSearch.count)
        await query.message.edit_text(
            t("deep_count_prompt", lang),
            reply_markup=kb.deep_count_kb(),
        )
        await query.answer()
        return

    await state.set_state(Scan.limit)
    await query.message.edit_text(
        t("mode_confirmed", lang, mode=t(f"mode_{mode}", lang)),
        reply_markup=kb.limit_kb(),
    )
    await query.answer()


@router.callback_query(DeepSearch.count, F.data.startswith("deep_count:"))
async def on_deep_count(query: CallbackQuery, state: FSMContext) -> None:
    lang  = await _lang(state, query.from_user.id)
    count = int(query.data.split(":")[1])
    await state.update_data(deep_count=count)
    await state.set_state(DeepSearch.confirm)
    await query.message.edit_text(
        t("deep_confirm_text", lang, count=count, max_credits=deep_search.MAX_CREDITS),
        reply_markup=kb.deep_confirm_kb(lang),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(DeepSearch.confirm, F.data == "deep_confirm:no")
async def on_deep_confirm_no(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state, query.from_user.id)
    data = await state.get_data()
    info = data.get("target", {})
    await state.set_state(Scan.mode)
    priv = t("privacy_private", lang) if info.get("is_private") else t("privacy_public", lang)
    fn   = f"  —  {info['full_name']}" if info.get("full_name") else ""
    await query.message.edit_text(
        t("user_found", lang,
          username=info.get("username", ""),
          fullname=fn,
          followers=kb._fmt(info.get("followers")),
          following=kb._fmt(info.get("following")),
          privacy=priv),
        reply_markup=kb.mode_kb(lang),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(DeepSearch.confirm, F.data == "deep_confirm:yes")
async def on_deep_confirm_yes(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    tg_user = query.from_user.id
    if not await _check_terms(query, state, tg_user):
        return
    lang = await _lang(state, tg_user)

    if deep_search.is_running(tg_user):
        await query.answer(t("deep_busy", lang), show_alert=True)
        return

    if not storage.get_credits(tg_user) > 0:
        await query.message.edit_text(
            credits_text(0, lang), parse_mode=ParseMode.HTML,
            reply_markup=buy_kb(lang),
        )
        await query.answer()
        return

    data   = await state.get_data()
    target = data["target"]
    cfg    = data.get("filter", flt.DEFAULT_FILTER.copy())
    count  = data.get("deep_count", 25)

    target_country = ""
    if cfg.get("country") == "target":
        target_country = await instagram.fetch_user_country(target["user_id"])
        if not target_country:
            await query.answer(t("deep_no_country_alert", lang), show_alert=True)
            return

    sess = deep_search.DeepSession(
        tg_user=tg_user,
        target_uid=target["user_id"],
        target_username=target["username"],
        target_count=count,
        user_filter=cfg,
        lang=lang,
        target_country=target_country,
    )

    prog = await query.message.edit_text(
        t("deep_running", lang, count=count, target=target["username"]),
        reply_markup=kb.deep_stop_kb(lang),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()

    deep_search._sessions[tg_user] = sess  # register before task starts to prevent double-launch
    asyncio.create_task(
        deep_search.run(sess, bot, query.message.chat.id, prog.message_id, state)
    )


@router.callback_query(F.data == "deep_stop:ask")
async def on_deep_stop_ask(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state, query.from_user.id)
    await query.message.edit_reply_markup(reply_markup=kb.deep_stop_confirm_kb(lang))
    await query.answer()


@router.callback_query(F.data == "deep_stop:cancel")
async def on_deep_stop_cancel(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state, query.from_user.id)
    await query.message.edit_reply_markup(reply_markup=kb.deep_stop_kb(lang))
    await query.answer()


@router.callback_query(F.data == "deep_stop:confirm")
async def on_deep_stop_confirm(query: CallbackQuery) -> None:
    deep_search.stop_session(query.from_user.id)
    await query.answer()


@router.callback_query(Scan.limit, F.data.startswith("limit:"))
async def on_limit(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await query.answer()
    tg_user = query.from_user.id
    if not await _check_terms(query, state, tg_user):
        return
    lang    = await _lang(state, tg_user)

    limit  = int(query.data.split(":")[1])
    cost   = max(1, limit // 25)   # 25→1, 50→2, 100→4 …

    # ── проверка кредитов ─────────────────────────────────────────────────────
    if not storage.deduct_credit(tg_user, cost):
        await query.message.edit_text(
            credits_text(storage.get_credits(tg_user), lang),
            parse_mode=ParseMode.HTML,
            reply_markup=buy_kb(lang),
        )
        return
    data   = await state.get_data()
    target = data["target"]
    mode   = data["mode"]
    cfg    = data.get("filter", flt.DEFAULT_FILTER.copy())

    await state.update_data(limit=limit, current_idx=0)

    prog = query.message
    await prog.edit_text(
        t("scan_launched", lang,
          username=target["username"],
          mode=t(f"mode_{mode}", lang),
          limit=limit,
          settings=kb.settings_text(cfg, lang)),
    )

    _last = [0.0]

    async def on_progress(done: int, total: int) -> None:
        import time as _t
        now = _t.monotonic()
        if now - _last[0] < 3 and done < total:
            return
        _last[0] = now
        pct = int(done / total * 10)
        bar = "█" * pct + "░" * (10 - pct)
        try:
            await prog.edit_text(
                t("scan_progress", lang,
                  done=done, total=total, bar=bar, pct=int(done / total * 100),
                  username=target["username"], mode=t(f"mode_{mode}", lang)),
            )
        except Exception:
            pass

    target_country = ""
    if cfg.get("country") == "target":
        target_country = await instagram.fetch_user_country(target["user_id"])
        print(f"[filter] страна цели: «{target_country}»", flush=True)

    hiker_snap  = hiker_log.total
    openai_snap = openai_log.total

    ctx_token = scan_context.set({"tg_user": tg_user, "target": target["username"]})
    try:
        profiles, next_pid = await instagram.elite_scan(
            user_id=target["user_id"],
            mode=mode,
            limit=limit,
            skip_followers=cfg.get("max_followers", config.SKIP_FOLLOWERS),
            analyze_fn=vision.analyze_profile,
            on_progress=on_progress,
            country_filter=cfg.get("country", "all"),
            target_country=target_country,
        )
    except Exception as e:
        await prog.edit_text(t("scan_error", lang, error=str(e)[:200]))
        return
    finally:
        scan_context.reset(ctx_token)

    summary_log.log_scan(
        tg_user  = tg_user,
        target   = target["username"],
        hiker    = hiker_log.total  - hiker_snap,
        openai   = openai_log.total - openai_snap,
        profiles = len(profiles),
    )

    filtered, stats = flt.apply(profiles, cfg, target_country)
    print(f"[filter] {flt.filter_summary(len(profiles), stats)}", flush=True)
    _write_debug_json(profiles, target["username"])
    _save_last_links(profiles, target["username"],
                     hiker=hiker_log.total - hiker_snap,
                     openai=openai_log.total - openai_snap)

    await state.update_data(profiles=filtered, next_page_id=next_pid, scan_likes=0)
    await state.set_state(Scan.swiping)

    last_batch = not bool(next_pid) and mode in ("followers", "following")
    extra      = t(f"scan_done_last_{mode}", lang) if last_batch else ""

    await prog.edit_text(
        t("scan_done", lang,
          count=len(profiles),
          mode=t(f"mode_{mode}", lang),
          filter_summary=flt.filter_summary(len(profiles), stats, lang)) + extra,
    )
    await _send_card(bot, query.message.chat.id, state, 0, user_id=tg_user)


# ══════════════════════════════════════════════════════════════════════════════
# SWIPING — лайк / скип
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(Scan.swiping, F.data.regexp(r"^(like|skip):\d+"))
async def on_swipe(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await query.answer()
    action, idx_str = query.data.split(":")
    idx     = int(idx_str)
    chat_id = query.message.chat.id

    data     = await state.get_data()
    profiles = data.get("profiles", [])

    if idx < len(profiles):
        u  = profiles[idx]
        un = u.get("username", "")
        if action == "like":
            liked = data.get("liked", [])
            if not any(x.get("username") == un for x in liked):
                liked.append(u)
                scan_likes = data.get("scan_likes", 0) + 1
                await state.update_data(liked=liked, scan_likes=scan_likes)
                storage.save_liked(query.from_user.id, liked)

    await _delete_card(bot, chat_id, data)
    next_idx = idx + 1
    await state.update_data(current_idx=next_idx)
    await _send_card(bot, chat_id, state, next_idx, user_id=query.from_user.id)


@router.callback_query(F.data == "noop")
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data == "action:new_scan")
async def on_action_new_scan(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang    = await _lang(state, query.from_user.id)
    data    = await state.get_data()
    liked   = data.get("liked") or storage.get_liked(query.from_user.id)
    cfg     = data.get("filter") or storage.get_filter(query.from_user.id)
    try:
        await query.message.delete()
    except Exception:
        pass
    await state.set_state(Scan.username)
    await state.update_data(profiles=[], target=None, liked=liked, filter=cfg,
                            card_msg_ids=[], kb_msg_id=None)
    await query.message.answer(t("new_prompt", lang))


async def _send_random_profile(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    lang: str,
    liked: list[dict],
    delete_msg_id: int | None = None,
) -> None:
    import random
    if delete_msg_id:
        try:
            await bot.delete_message(chat_id, delete_msg_id)
        except Exception:
            pass

    u        = random.choice(liked)
    caption  = kb.card_caption(u, lang)
    title    = t("random_liked_title", lang)
    pic      = u.get("_pic")

    try:
        if pic:
            msg = await bot.send_photo(
                chat_id,
                photo=BufferedInputFile(pic, "photo.jpg"),
                caption=f"{title}\n\n{caption}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb.random_profile_kb(lang),
            )
        else:
            msg = await bot.send_message(
                chat_id,
                f"{title}\n\n{caption}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=kb.random_profile_kb(lang),
            )
    except Exception:
        msg = await bot.send_message(
            chat_id,
            f"{title}\n\n{caption}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=kb.random_profile_kb(lang),
        )

    await state.update_data(random_profile=u, random_msg_id=msg.message_id)


@router.callback_query(F.data == "action:random_liked")
async def on_action_random_liked(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await query.answer()
    lang  = await _lang(state, query.from_user.id)
    data  = await state.get_data()
    liked = data.get("liked") or storage.get_liked(query.from_user.id)
    if not liked:
        return
    try:
        await query.message.delete()
    except Exception:
        pass
    await _send_random_profile(bot, query.message.chat.id, state, lang, liked)


@router.callback_query(F.data == "random:skip")
async def on_random_skip(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await query.answer()
    lang  = await _lang(state, query.from_user.id)
    data  = await state.get_data()
    liked = data.get("liked") or storage.get_liked(query.from_user.id)
    if not liked:
        return
    # удаляем текущую карточку, показываем новую
    await _send_random_profile(
        bot, query.message.chat.id, state, lang, liked,
        delete_msg_id=data.get("random_msg_id"),
    )


@router.callback_query(F.data == "random:approve")
async def on_random_approve(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    lang = await _lang(state, query.from_user.id)
    data = await state.get_data()
    profile = data.get("random_profile")
    if not profile:
        return

    # строим target из сохранённого лайкнутого профиля
    target = {
        "user_id":   profile.get("id", ""),
        "username":  profile.get("username", ""),
        "full_name": profile.get("full_name", ""),
        "is_private": profile.get("is_private", False),
        "followers":  profile.get("follower_count"),
        "following":  profile.get("following_count"),
        "pic_url":    "",
    }
    await state.update_data(target=target)
    await state.set_state(Scan.mode)

    await query.message.answer(
        t("random_choose_mode", lang, username=target["username"]),
        parse_mode="HTML",
        reply_markup=kb.mode_kb(lang),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Продолжить скан
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(Scan.swiping, F.data == "more_profiles")
async def on_more_profiles(query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await query.answer()
    tg_user  = query.from_user.id
    lang     = await _lang(state, tg_user)
    data     = await state.get_data()
    target   = data["target"]
    mode     = data["mode"]
    limit    = data["limit"]
    cfg      = data.get("filter", flt.DEFAULT_FILTER.copy())
    next_pid = data.get("next_page_id", "")
    chat_id  = query.message.chat.id
    cost     = max(1, limit // 25)   # 25→1, 50→2, 100→4 …

    # ── проверка кредитов ─────────────────────────────────────────────────────
    if not storage.deduct_credit(tg_user, cost):
        await bot.send_message(
            chat_id,
            credits_text(storage.get_credits(tg_user), lang),
            parse_mode=ParseMode.HTML,
            reply_markup=buy_kb(lang),
        )
        return

    # удаляем сообщение «🎉 Готово!»
    end_msg_id = data.get("end_msg_id")
    if end_msg_id:
        try:
            await bot.delete_message(chat_id, end_msg_id)
        except Exception:
            pass

    prog = await bot.send_message(
        chat_id,
        t("more_loading", lang,
          username=target["username"],
          mode=t(f"mode_{mode}", lang),
          limit=limit),
        parse_mode=ParseMode.HTML,
    )

    _last = [0.0]

    async def on_progress(done: int, total: int) -> None:
        import time as _t
        now = _t.monotonic()
        if now - _last[0] < 3 and done < total:
            return
        _last[0] = now
        pct = int(done / total * 10)
        bar = "█" * pct + "░" * (10 - pct)
        try:
            await prog.edit_text(
                t("more_scanning", lang,
                  done=done, total=total, bar=bar, pct=int(done / total * 100),
                  username=target["username"], mode=t(f"mode_{mode}", lang)),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    target_country = ""
    if cfg.get("country") == "target":
        target_country = await instagram.fetch_user_country(target["user_id"])

    hiker_snap  = hiker_log.total
    openai_snap = openai_log.total

    ctx_token = scan_context.set({"tg_user": tg_user, "target": target["username"]})
    try:
        profiles, new_pid = await instagram.elite_scan(
            user_id=target["user_id"],
            mode=mode,
            limit=limit,
            skip_followers=cfg.get("max_followers", config.SKIP_FOLLOWERS),
            analyze_fn=vision.analyze_profile,
            on_progress=on_progress,
            page_id=next_pid,
            country_filter=cfg.get("country", "all"),
            target_country=target_country,
        )
    except Exception as e:
        await prog.edit_text(t("scan_error", lang, error=str(e)[:200]))
        return
    finally:
        scan_context.reset(ctx_token)

    summary_log.log_scan(
        tg_user  = tg_user,
        target   = target["username"],
        hiker    = hiker_log.total  - hiker_snap,
        openai   = openai_log.total - openai_snap,
        profiles = len(profiles),
    )

    filtered, stats = flt.apply(profiles, cfg, target_country)
    _write_debug_json(profiles, target["username"])
    _save_last_links(profiles, target["username"],
                     hiker=hiker_log.total - hiker_snap,
                     openai=openai_log.total - openai_snap)

    await state.update_data(profiles=filtered, next_page_id=new_pid,
                            current_idx=0, scan_likes=0, end_msg_id=None)

    last_batch = not bool(new_pid) and mode in ("followers", "following")
    extra      = t(f"scan_done_last_{mode}", lang) if last_batch else ""

    await prog.edit_text(
        t("scan_done", lang,
          count=len(profiles),
          mode=t(f"mode_{mode}", lang),
          filter_summary=flt.filter_summary(len(profiles), stats, lang)) + extra,
        parse_mode=ParseMode.HTML,
    )
    await _send_card(bot, chat_id, state, 0, user_id=tg_user)
