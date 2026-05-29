"""
Inline-клавиатуры и форматирование текста: карточки, setup, settings, лайки, пагинация.
"""

import re
import html as _html

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from i18n import t

PAGE_SIZE = 5


# ── Форматирование чисел ──────────────────────────────────────────────────────

def _fmt(n: int | None) -> str:
    if n is None: return "—"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


def _fmt_date(d: str) -> str:
    """YYYY-MM-DD → YYYY-MM. Текстовые форматы оставляем как есть."""
    if not d:
        return ""
    m = re.match(r"(\d{4}-\d{2})", d)
    return m.group(1) if m else d


def _fmt_year(d: str) -> str:
    m = re.match(r"(\d{4})", d or "")
    return m.group(1) if m else d


# ══════════════════════════════════════════════════════════════════════════════
# ЯЗЫК
# ══════════════════════════════════════════════════════════════════════════════

def terms_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("terms_btn_accept", lang), callback_data="terms:accept")
    b.adjust(1)
    return b.as_markup()


def terms_required_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("terms_required_btn", lang), callback_data="terms:show")
    b.adjust(1)
    return b.as_markup()


def lang_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🇬🇧 English", callback_data="set_lang:en")
    b.button(text="🇷🇺 Русский", callback_data="set_lang:ru")
    b.button(text="🇩🇪 Deutsch", callback_data="set_lang:de")
    b.adjust(3)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
# SETUP — онбординг-опросник
# ══════════════════════════════════════════════════════════════════════════════

def setup_gender_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_gender_female", lang), callback_data="setup_gender:female")
    b.button(text=t("btn_gender_male",   lang), callback_data="setup_gender:male")
    b.button(text=t("btn_gender_any",    lang), callback_data="setup_gender:any")
    b.adjust(3)
    return b.as_markup()


def setup_country_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_country_target", lang), callback_data="setup_country:target")
    b.button(text=t("btn_country_all",    lang), callback_data="setup_country:all")
    b.adjust(1)
    return b.as_markup()


def setup_min_photos_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_no_limit", lang), callback_data="setup_min_photos:0")
    b.button(text="1",                     callback_data="setup_min_photos:1")
    b.button(text="3",                     callback_data="setup_min_photos:3")
    b.button(text="5",                     callback_data="setup_min_photos:5")
    b.adjust(2, 2)
    return b.as_markup()


def setup_max_followers_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for val, label in [(1_000, "1К"), (5_000, "5К"), (10_000, "10К"),
                       (50_000, "50К"), (100_000, "100К")]:
        b.button(text=label, callback_data=f"setup_max_followers:{val}")
    b.adjust(3, 2)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS — изменение фильтра после настройки
# ══════════════════════════════════════════════════════════════════════════════

def settings_text(cfg: dict, lang: str = "en", credits: int | None = None) -> str:
    g      = t(f"gender_{cfg.get('gender', 'any')}",   lang)
    c      = t(f"country_{cfg.get('country', 'all')}",  lang)
    mp     = cfg.get("min_photos", 1)
    mf     = cfg.get("max_followers", 10_000)
    mp_str = t("unlimited", lang) if mp == 0 else str(mp)
    lines  = [
        t("settings_header", lang),
        f"{t('settings_gender', lang)}  —  <b>{g}</b>",
        f"{t('settings_country', lang)}  —  <b>{c}</b>",
        f"{t('settings_minphotos', lang)}  —  <b>{mp_str}</b>",
        f"{t('settings_maxfollowers', lang)}  —  <b>{_fmt(mf)}</b>",
    ]
    if credits is not None:
        lines.append(f"{t('settings_credits', lang)}  —  <b>{credits}</b>")
    return "\n".join(lines)


def settings_main_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_edit_gender",       lang), callback_data="settings:gender")
    b.button(text=t("btn_edit_country",      lang), callback_data="settings:country")
    b.button(text=t("btn_edit_minphotos",    lang), callback_data="settings:min_photos")
    b.button(text=t("btn_edit_maxfollowers", lang), callback_data="settings:max_followers")
    b.button(text=t("btn_delete_profile",    lang), callback_data="settings:delete")
    b.adjust(2, 2, 1)
    return b.as_markup()


def delete_profile_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("delete_btn_yes", lang), callback_data="delete_profile:confirm")
    b.button(text=t("delete_btn_no",  lang), callback_data="settings:back")
    b.adjust(1)
    return b.as_markup()


def settings_option_kb(param: str, lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if param == "gender":
        b.button(text=t("btn_gender_female", lang), callback_data="set_gender:female")
        b.button(text=t("btn_gender_male",   lang), callback_data="set_gender:male")
        b.button(text=t("btn_gender_any",    lang), callback_data="set_gender:any")
        b.adjust(3)
    elif param == "country":
        b.button(text=t("btn_country_target", lang), callback_data="set_country:target")
        b.button(text=t("btn_country_all",    lang), callback_data="set_country:all")
        b.adjust(1)
    elif param == "min_photos":
        b.button(text=t("btn_no_limit", lang), callback_data="set_min_photos:0")
        b.button(text="1",                     callback_data="set_min_photos:1")
        b.button(text="3",                     callback_data="set_min_photos:3")
        b.button(text="5",                     callback_data="set_min_photos:5")
        b.adjust(2, 2)
    elif param == "max_followers":
        for val, label in [(1_000, "1К"), (5_000, "5К"), (10_000, "10К"),
                           (50_000, "50К"), (100_000, "100К")]:
            b.button(text=label, callback_data=f"set_max_followers:{val}")
        b.adjust(3, 2)
    b.row(width=1)
    b.button(text=t("btn_back", lang), callback_data="settings:back")
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
# SCAN — выбор режима и лимита
# ══════════════════════════════════════════════════════════════════════════════

def mode_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_mode_following", lang), callback_data="mode:following")
    b.button(text=t("btn_mode_followers", lang), callback_data="mode:followers")
    b.button(text=t("btn_mode_suggested", lang), callback_data="mode:suggested")
    b.button(text=t("btn_mode_deep",      lang), callback_data="mode:deep")
    b.adjust(1)
    return b.as_markup()


def deep_count_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="25", callback_data="deep_count:25")
    b.button(text="50", callback_data="deep_count:50")
    b.adjust(2)
    return b.as_markup()


def deep_confirm_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("deep_yes", lang), callback_data="deep_confirm:yes")
    b.button(text=t("deep_no",  lang), callback_data="deep_confirm:no")
    b.adjust(2)
    return b.as_markup()


def deep_stop_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("deep_stop_btn", lang), callback_data="deep_stop:ask")
    b.adjust(1)
    return b.as_markup()


def deep_stop_confirm_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("deep_stop_yes", lang), callback_data="deep_stop:confirm")
    b.button(text=t("deep_stop_no",  lang), callback_data="deep_stop:cancel")
    b.adjust(2)
    return b.as_markup()


def limit_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in (25, 50, 100):
        b.button(text=str(n), callback_data=f"limit:{n}")
    b.adjust(3)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА ПРОФИЛЯ
# ══════════════════════════════════════════════════════════════════════════════

def card_kb(idx: int, username: str, lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_like",         lang), callback_data=f"like:{idx}")
    b.button(text=t("btn_skip",         lang), callback_data=f"skip:{idx}")
    b.button(text=t("btn_profile_link", lang), url=f"https://www.instagram.com/{username}/")
    b.adjust(2, 1)
    return b.as_markup()


def liked_card_kb(username: str, lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_liked_saved",  lang), callback_data="noop")
    b.button(text=t("btn_profile_link", lang), url=f"https://www.instagram.com/{username}/")
    b.adjust(2)
    return b.as_markup()


def card_caption(u: dict, lang: str = "en") -> str:
    username  = u.get("username", "")
    full_name = u.get("full_name", "")
    analysis  = u.get("analysis") or {}
    skip      = u.get("skip_reason")
    lines: list[str] = []

    name_line = f'<b><a href="https://www.instagram.com/{_html.escape(username)}/">@{_html.escape(username)}</a></b>'
    if full_name:
        name_line += f"  ·  {_html.escape(full_name)}"
    lines.append(name_line)

    meta = []
    country   = u.get("country") or ""
    city      = u.get("city_name") or ""
    loc = ",  ".join(filter(None, [country, city]))
    if loc:
        meta.append(_html.escape(loc))
    elif not country and not skip:
        meta.append(t("card_country_unknown", lang))
    if u.get("account_date"):
        meta.append(f"{t('card_since', lang)} {_fmt_date(u['account_date'])}")
    if meta:
        lines.append("  ·  ".join(meta))

    bio = (u.get("biography") or "").strip()
    if bio:
        bio_safe = _html.escape(bio[:220]) + ("…" if len(bio) > 220 else "")
        lines.append("")
        lines.append(f"<i>{bio_safe}</i>")

    stats = []
    fc = u.get("follower_count")
    mc = u.get("media_count")
    if fc is not None: stats.append(f"{_fmt(fc)} {t('card_followers', lang)}")
    if mc is not None: stats.append(f"{mc} {t('card_posts', lang)}")
    if u.get("has_highlights"): stats.append(t("card_highlights", lang))
    if stats:
        lines.append("")
        lines.append("  ·  ".join(stats))

    if analysis and not skip:
        traits = []
        if analysis.get("age_apr"):     traits.append(f"{analysis['age_apr']} {t('card_age_unit', lang)}")
        if analysis.get("hair_color"):  traits.append(_html.escape(str(analysis["hair_color"])))
        if analysis.get("eyes_color"):  traits.append(_html.escape(str(analysis["eyes_color"])))
        if analysis.get("figure_type"): traits.append(_html.escape(str(analysis["figure_type"])))
        langs = analysis.get("account_languages") or []
        if langs: traits.append(" / ".join(_html.escape(l.upper()) for l in langs[:3]))
        if traits:
            lines.append("")
            lines.append("  ·  ".join(traits))

        interests = analysis.get("possible_interest") or []
        if interests:
            lines.append(", ".join(_html.escape(str(i)) for i in interests[:5]))

    if skip:
        labels = {
            "private":   "🔒 " + t("privacy_private",  lang).lstrip("🔒 "),
            "verified":  "✅ Verified",
            "followers": "📈 " + t("fs_followers", lang).strip(" ·{n}0123456789:").strip(),
        }
        lines.append("")
        lines.append(labels.get(skip, f"⚠️ {skip}"))

    return "\n".join(lines)[:1020]


# ══════════════════════════════════════════════════════════════════════════════
# СПИСОК ЛАЙКОВ
# ══════════════════════════════════════════════════════════════════════════════

def liked_page_kb(page: int, total: int, edit_mode: bool = False, lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # пагинация
    nav = []
    if page > 0:
        s, e = (page - 1) * PAGE_SIZE + 1, page * PAGE_SIZE
        b.button(text=f"◀  {s}–{e}", callback_data=f"liked_page:{page}:{page-1}:{int(edit_mode)}")
        nav.append(1)
    if (page + 1) * PAGE_SIZE < total:
        s, e = (page + 1) * PAGE_SIZE + 1, min((page + 2) * PAGE_SIZE, total)
        b.button(text=f"{s}–{e}  ▶", callback_data=f"liked_page:{page}:{page+1}:{int(edit_mode)}")
        nav.append(1)
    if nav:
        b.adjust(*nav)
    # управление
    b.row()
    if edit_mode:
        b.button(text=t("btn_liked_clear_all", lang), callback_data="liked_clear:ask")
        b.button(text=t("btn_liked_done",      lang), callback_data="liked_page:0:0:0")
        b.adjust(1, 2)
    else:
        b.button(text=t("btn_liked_edit", lang), callback_data="liked_page:0:0:1")
        b.adjust(1)
    return b.as_markup()


def liked_page(
    liked: list[dict],
    page: int,
    lang: str = "en",
    edit_mode: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    total       = len(liked)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start       = page * PAGE_SIZE
    end         = min(start + PAGE_SIZE, total)
    chunk       = liked[start:end]
    sep         = t("liked_sep", lang) * 32

    lines = [t("liked_header", lang, total=total), f"<code>{sep}</code>"]

    for i, u in enumerate(chunk, start=start + 1):
        un = u.get("username", "")
        fn = u.get("full_name", "")
        real_idx = start + (i - start - 1)  # глобальный индекс в списке

        row = f"<b>{i}.</b>  <b><a href=\"https://www.instagram.com/{un}/\">@{un}</a></b>"
        if fn: row += f"  —  {fn}"
        lines.append(row)

        meta = []
        loc = ",  ".join(filter(None, [u.get("country"), u.get("city_name")]))
        if loc: meta.append(loc)
        if u.get("account_date"): meta.append(_fmt_year(u["account_date"]))
        fc = u.get("follower_count")
        if fc is not None: meta.append(f"{_fmt(fc)} {t('card_followers', lang)}")
        if meta: lines.append("    " + "  ·  ".join(meta))

        if i < end: lines.append("")

    lines.append(f"<code>{sep}</code>")
    lines.append(t("liked_page_info", lang,
                   page=page + 1, pages=total_pages,
                   **{"from": start + 1}, to=end, total=total))

    b = InlineKeyboardBuilder()

    # кнопки удаления в режиме редактирования
    if edit_mode:
        for i, u in enumerate(chunk):
            real_idx = start + i
            un = u.get("username", "")
            b.button(
                text=f"🗑  @{un}",
                callback_data=f"liked_del:{real_idx}:{page}",
            )
        b.adjust(1)

    # пагинация + управление
    nav_kb = liked_page_kb(page, total, edit_mode, lang)
    # объединяем кнопки удаления + навигацию
    for row in nav_kb.inline_keyboard:
        b.row(*[btn for btn in row])

    return "\n".join(lines), b.as_markup()


def liked_clear_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_liked_clear_yes", lang), callback_data="liked_clear:confirm")
    b.button(text=t("btn_back",            lang), callback_data="liked_page:0:0:0")
    b.adjust(1)
    return b.as_markup()


def end_summary(total: int, scan_likes: int, lang: str = "en") -> str:
    if scan_likes:
        return t("end_summary", lang, total=total, likes=scan_likes)
    return t("end_summary_zero", lang, total=total)


def random_profile_kb(lang: str = "en") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_scan_this",     lang), callback_data="random:approve")
    b.button(text=t("btn_another_random", lang), callback_data="random:skip")
    b.adjust(1)
    return b.as_markup()


def end_kb(has_more: bool, lang: str = "en", has_liked: bool = False) -> InlineKeyboardMarkup | None:
    b = InlineKeyboardBuilder()
    if has_more:
        b.button(text=t("btn_continue", lang), callback_data="more_profiles")
        b.adjust(1)
        return b.as_markup()
    # конец списка — предлагаем новый скан и случайный лайк
    b.button(text=t("btn_new_scan", lang), callback_data="action:new_scan")
    if has_liked:
        b.button(text=t("btn_random_liked", lang), callback_data="action:random_liked")
    b.adjust(1)
    return b.as_markup()
