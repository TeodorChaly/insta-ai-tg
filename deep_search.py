"""
Deep Search — AI-driven continuous following scan.
Keeps scanning following lists until target_count filtered profiles found.
1 credit per API page (≤25 profiles). Max 20 credits per session.
"""

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

import config
import storage
from instagram import fetch_elite_profile, _get, fetch_user_country
from vision import analyze_profile
from filters import apply as filter_apply, DEFAULT_FILTER
from logger import hiker_log, openai_log, summary_log

MAX_CREDITS = 20
LOG_DIR = "logs"


@dataclass
class DeepSession:
    tg_user: int
    target_uid: str
    target_username: str
    target_count: int
    user_filter: dict
    lang: str
    target_country: str = ""

    queue: list[dict] = field(default_factory=list)
    queue_uids: set[str] = field(default_factory=set)
    cursors: dict[str, str] = field(default_factory=dict)
    scanned_uids: set[str] = field(default_factory=set)

    bucket_match: list[dict] = field(default_factory=list)
    bucket_no_country: list[dict] = field(default_factory=list)

    credits_used: int = 0
    profiles_scanned: int = 0

    # snapshots taken at run() start to compute per-session API usage
    hiker_calls_start: int = 0
    openai_calls_start: int = 0

    stop_flag: bool = False
    stop_reason: str = ""

    log_lines: list[str] = field(default_factory=list)


_sessions: dict[int, DeepSession] = {}


def get_session(tg_user: int) -> Optional[DeepSession]:
    return _sessions.get(tg_user)


def is_running(tg_user: int) -> bool:
    return tg_user in _sessions


def stop_session(tg_user: int) -> None:
    s = _sessions.get(tg_user)
    if s:
        s.stop_flag = True
        s.stop_reason = "user_stopped"


def _save_log(sess: DeepSession) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"deep_{sess.target_username}_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(sess.log_lines))


async def run(
    sess: DeepSession,
    bot: Bot,
    chat_id: int,
    progress_msg_id: int,
    state: FSMContext,
) -> None:
    """Background task. Runs deep search, then triggers card swiping."""
    from i18n import t
    import keyboards as kb
    import logging
    _log = logging.getLogger("deep_search")

    _sessions[sess.tg_user] = sess
    sess.hiker_calls_start  = hiker_log.total
    sess.openai_calls_start = openai_log.total

    try:
        await _run_inner(sess, bot, chat_id, progress_msg_id, state, t, kb)
    except Exception as exc:
        _log.error(f"[deep_search] user={sess.tg_user} crashed: {exc}", exc_info=True)
        try:
            await bot.send_message(chat_id, f"❌ Search stopped due to an error.")
        except Exception:
            pass
    finally:
        _sessions.pop(sess.tg_user, None)


async def _run_inner(
    sess: DeepSession,
    bot: Bot,
    chat_id: int,
    progress_msg_id: int,
    state: FSMContext,
    t,
    kb,
) -> None:

    sep = "═" * 72
    sess.log_lines += [
        sep,
        "  Deep Search Log",
        f"  Target:  @{sess.target_username}  (uid={sess.target_uid})",
        f"  Date:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Goal:    {sess.target_count} profiles",
        f"  Filter:  gender={sess.user_filter.get('gender','any')}  "
        f"country={sess.user_filter.get('country','target')}  "
        f"min_photos={sess.user_filter.get('min_photos',0)}  "
        f"max_followers={sess.user_filter.get('max_followers','∞')}",
        sep, "",
    ]

    sess.queue.append({"user_id": sess.target_uid, "username": sess.target_username})
    sess.queue_uids.add(sess.target_uid)

    api_sem = config.API_SEM  # глобальный семафор — общий для всех пользователей
    gpt_sem = config.GPT_SEM

    _last_update = [0.0]

    async def _update_progress():
        import time as _t
        now = _t.monotonic()
        if now - _last_update[0] < 3:
            return
        _last_update[0] = now
        found = len(sess.bucket_match) + len(sess.bucket_no_country)
        try:
            await bot.edit_message_text(
                t("deep_progress", sess.lang,
                  found=found, target=sess.target_count,
                  scanned=sess.profiles_scanned,
                  credits=sess.credits_used),
                chat_id=chat_id,
                message_id=progress_msg_id,
                reply_markup=kb.deep_stop_kb(sess.lang),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as http:
        queue_idx = 0

        while True:
            found = len(sess.bucket_match) + len(sess.bucket_no_country)

            if found >= sess.target_count:
                sess.stop_reason = "target_reached"
                break
            if sess.credits_used >= MAX_CREDITS:
                sess.stop_reason = "no_credits"
                break
            if sess.stop_flag:
                break

            if queue_idx >= len(sess.queue):
                expanded = False
                for p in sess.bucket_match:
                    uid = str(p.get("id") or p.get("user_id") or "")
                    if uid and uid not in sess.queue_uids:
                        sess.queue.append({"user_id": uid, "username": p.get("username", "")})
                        sess.queue_uids.add(uid)
                        expanded = True
                if not expanded:
                    sess.stop_reason = "exhausted"
                    break
                if queue_idx >= len(sess.queue):
                    sess.stop_reason = "exhausted"
                    break

            current = sess.queue[queue_idx]
            cur_uid = str(current["user_id"])
            cur_uname = current.get("username", cur_uid)
            queue_idx += 1

            if not await storage.deduct_credit(sess.tg_user):
                sess.stop_reason = "no_credits"
                break
            sess.credits_used += 1

            page_id = sess.cursors.get(cur_uid, "")
            params: dict = {"user_id": cur_uid}
            if page_id:
                params["page_id"] = page_id

            sess.log_lines.append(
                f"\n── @{cur_uname} following {'[page_id='+page_id+']' if page_id else '[page 1]'} ──"
            )

            async with api_sem:
                r = await _get(http, f"{config.BASE}/user/following", params=params)

            if r.status_code != 200:
                sess.log_lines.append(f"  ❌ HTTP {r.status_code}: {r.text[:80]}")
                continue

            body = r.json()
            if isinstance(body, dict) and "detail" in body:
                sess.log_lines.append(f"  ❌ {body['detail']}")
                continue

            page_users: list[dict] = (body.get("response") or {}).get("users") or []
            next_pid: str = body.get("next_page_id") or ""

            if not page_users:
                sess.log_lines.append("  ⚠️  empty page")
                continue

            if next_pid:
                sess.cursors[cur_uid] = next_pid
                sess.queue.insert(queue_idx, {"user_id": cur_uid, "username": cur_uname})

            sess.profiles_scanned += len(page_users)
            sess.log_lines.append(f"  fetched {len(page_users)} users")

            async def _analyze(base_u: dict) -> Optional[dict]:
                uid = str(base_u.get("pk") or base_u.get("id") or "")
                if not uid or uid in sess.scanned_uids:
                    return None
                sess.scanned_uids.add(uid)
                try:
                    return await fetch_elite_profile(
                        http, uid, base_u, api_sem, gpt_sem, analyze_profile,
                        skip_followers=config.SKIP_FOLLOWERS,
                        country_filter=sess.user_filter.get("country", "all"),
                        target_country=sess.target_country,
                    )
                except Exception as e:
                    sess.log_lines.append(f"  ⚠️  {base_u.get('username','?')}: {e}")
                    return None

            results = await asyncio.gather(*[_analyze(u) for u in page_users])

            for profile in results:
                if profile is None:
                    continue

                uname = profile.get("username", "?")

                if profile.get("skip_reason"):
                    sess.log_lines.append(f"  ❌  @{uname}  [{profile['skip_reason']}]")
                    continue

                passed, _ = filter_apply([profile], sess.user_filter, sess.target_country)
                if passed:
                    u_country = (profile.get("country") or "").strip()
                    if u_country:
                        sess.bucket_match.append(profile)
                        sess.log_lines.append(f"  ✅  @{uname}  [{u_country}]  → match")
                    else:
                        sess.bucket_no_country.append(profile)
                        sess.log_lines.append(f"  📍  @{uname}  [no country]  → no_country")
                else:
                    reason = profile.get("_reject_reason", "filtered")
                    sess.log_lines.append(f"  ❌  @{uname}  [{reason}]")

                if len(sess.bucket_match) + len(sess.bucket_no_country) >= sess.target_count:
                    break

            await _update_progress()

    # ── finalize ──
    found = len(sess.bucket_match) + len(sess.bucket_no_country)
    hiker_used  = hiker_log.total  - sess.hiker_calls_start
    openai_used = openai_log.total - sess.openai_calls_start
    sess.log_lines += [
        "",
        "═" * 72,
        "  RESULT",
        f"  Found:        {found} ({len(sess.bucket_match)} match + {len(sess.bucket_no_country)} no-country)",
        f"  Scanned:      {sess.profiles_scanned}",
        f"  Credits:      {sess.credits_used} used",
        f"  Stop reason:  {sess.stop_reason}",
        "",
        "  API usage this session:",
        f"  HikerAPI:     {hiker_used} requests",
        f"  OpenAI:       {openai_used} requests",
        "═" * 72,
    ]
    _save_log(sess)
    summary_log.log_scan(
        tg_user=sess.tg_user,
        target=sess.target_username,
        hiker=hiker_used,
        openai=openai_used,
        profiles=sess.profiles_scanned,
    )

    try:
        await bot.delete_message(chat_id, progress_msg_id)
    except Exception:
        pass

    all_profiles = sess.bucket_match + sess.bucket_no_country

    if not all_profiles:
        key = "deep_not_found" if sess.stop_reason == "exhausted" else "deep_credits_empty"
        await bot.send_message(
            chat_id, t(key, sess.lang, credits=sess.credits_used),
            parse_mode=ParseMode.HTML,
        )
        return

    done_text = t("deep_done", sess.lang,
                  found=found,
                  match=len(sess.bucket_match),
                  no_country=len(sess.bucket_no_country),
                  credits=sess.credits_used,
                  scanned=sess.profiles_scanned)
    if sess.stop_reason == "no_credits":
        done_text += "\n\n" + t("deep_credits_partial", sess.lang,
                                 found=found, credits=sess.credits_used)

    await bot.send_message(chat_id, done_text, parse_mode=ParseMode.HTML)

    state_data = await state.get_data()
    await state.update_data(
        profiles=all_profiles,
        current_idx=0,
        target={"username": sess.target_username, "user_id": sess.target_uid},
        mode="deep",
        next_page_id="",
        scan_likes=0,
        liked=state_data.get("liked", []),
    )

    from handlers import Scan, _send_card
    await state.set_state(Scan.swiping)
    await _send_card(bot, chat_id, state, 0, sess.tg_user)
