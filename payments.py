"""
Оплата через Telegram Stars (валюта XTR).
"""

from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery, Message, LabeledPrice,
    InlineKeyboardMarkup, InlineKeyboardButton,
    PreCheckoutQuery,
)

import storage
from i18n import t

router = Router()

# user_id → message_id инвойса (чтобы удалить после оплаты)
_invoice_msg: dict[int, int] = {}

# ── Пакеты кредитов ──────────────────────────────────────────────────────────
# credits = кол-во сканов (1 скан = 1 батч из 25 профилей)
# profiles = credits × 25  (сколько профилей можно просмотреть)

CREDIT_PACKAGES: dict[str, dict] = {
    "pkg_scout":  {"emoji": "🔍", "name": "Scout",  "credits": 16,  "profiles": 400,   "stars": 400,  "dollars": 6,  "tag": "+$1 bonus", "badge": ""},
    "pkg_seeker": {"emoji": "🎯", "name": "Seeker", "credits": 45,  "profiles": 1125,  "stars": 1000, "dollars": 15, "tag": "−10%",       "badge": ""},
    "pkg_hunter": {"emoji": "⚡", "name": "Hunter", "credits": 135, "profiles": 3375,  "stars": 2700, "dollars": 40, "tag": "−20%",       "badge": "  🔥"},
    "pkg_elite":  {"emoji": "👑", "name": "Elite",  "credits": 270, "profiles": 6750,  "stars": 4700, "dollars": 70, "tag": "−30%",       "badge": ""},
}


def _plans_text(lang: str) -> str:
    """Список планов для вставки в текстовое сообщение."""
    blocks = []
    for p in CREDIT_PACKAGES.values():
        profiles_fmt = f"{p['profiles']:,}"
        line1 = f"{p['emoji']} <b>{p['name']}</b> — <b>${p['dollars']}</b>  <b>({p['tag']})</b>{p['badge']}"
        line2 = f"<i>{t('pkg_plan_desc', lang, profiles=profiles_fmt)}</i>"
        blocks.append(f"{line1}\n{line2}")
    return "\n\n".join(blocks)


def buy_kb(lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{p['emoji']} {p['name']}  —  {p['stars']:,} ⭐  —  ${p['dollars']}",
            callback_data=k,
        )]
        for k, p in CREDIT_PACKAGES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def credits_text(balance: int, lang: str = "en") -> str:
    plans = _plans_text(lang)
    key = "credits_empty_msg" if balance == 0 else "credits_balance"
    return t(key, lang, balance=balance, plans=plans)


# ── Хендлеры ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_(set(CREDIT_PACKAGES)))
async def on_buy_package(query: CallbackQuery) -> None:
    pkg  = CREDIT_PACKAGES[query.data]
    lang = storage.get_lang(query.from_user.id)
    await query.answer()

    # удаляем сообщение с кнопками выбора пакета
    try:
        await query.message.delete()
    except Exception:
        pass

    label = t("pkg_label",         lang, name=pkg["name"], credits=pkg["credits"])
    title = t("pkg_invoice_title", lang, name=pkg["name"], credits=pkg["credits"])
    desc  = t("pkg_invoice_desc",  lang,
               name=pkg["name"], credits=pkg["credits"],
               profiles=pkg["profiles"], stars=pkg["stars"], dollars=pkg["dollars"])

    inv = await query.message.answer_invoice(
        title=title,
        description=desc,
        payload=query.data,
        provider_token="",      # Telegram Stars — provider_token не нужен
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=pkg["stars"])],
    )
    _invoice_msg[query.from_user.id] = inv.message_id


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Telegram обязательно ждёт ответ в течение 10 секунд."""
    if query.invoice_payload not in CREDIT_PACKAGES:
        await query.answer(ok=False, error_message="Неизвестный пакет. Попробуй снова.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(msg: Message) -> None:
    payload = msg.successful_payment.invoice_payload
    pkg = CREDIT_PACKAGES.get(payload)
    if not pkg:
        return

    # удаляем инвойс и само служебное сообщение об оплате
    inv_id = _invoice_msg.pop(msg.from_user.id, None)
    if inv_id:
        try:
            await msg.bot.delete_message(msg.chat.id, inv_id)
        except Exception:
            pass
    try:
        await msg.delete()
    except Exception:
        pass

    new_balance = await storage.add_credits(msg.from_user.id, pkg["credits"])
    lang = storage.get_lang(msg.from_user.id)
    await msg.answer(
        t("payment_success", lang,
          name=pkg["name"], credits=pkg["credits"],
          profiles=f"{pkg['profiles']:,}", balance=new_balance),
        parse_mode=ParseMode.HTML,
    )
