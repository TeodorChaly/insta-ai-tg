"""
Все запросы к HikerAPI: resolve профиля, получение списков, elite-сканирование.
"""

import re
import asyncio
import httpx
from typing import Callable, Awaitable

import config
from logger import hiker_log


# ── Хелперы ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"x-access-key": config.HIKER_API_TOKEN}


async def _get(http: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    r = await http.get(url, headers=_headers(), **kw)
    hiker_log.hit(url, r.status_code)
    return r


def extract_username(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    m = re.search(r"instagram\.com/([^/?#@\s]+)", raw)
    return m.group(1) if m else raw.lstrip("@")


async def download_bytes(http: httpx.AsyncClient, url: str) -> bytes | None:
    if not url:
        return None
    for attempt in range(3):
        try:
            r = await http.get(url, follow_redirects=True, timeout=12)
            if r.status_code == 200:
                return r.content
            # CDN вернул ошибку — возможно ссылка протухла, нет смысла ретраить
            if r.status_code in (403, 404, 410):
                return None
        except Exception:
            pass
        if attempt < 2:
            await asyncio.sleep(1)
    return None


# ── Resolve профиля ───────────────────────────────────────────────────────────

async def resolve_user(username: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20) as http:
        r = await _get(http, f"{config.BASE}/user/by/username",
                       params={"username": username})
    if r.status_code != 200:
        return None
    body = r.json()
    if isinstance(body, dict) and "detail" in body:
        return None
    u = body.get("user") or body.get("response") or body
    return {
        "user_id":    str(u.get("pk") or u.get("id") or ""),
        "username":   u.get("username", username),
        "full_name":  u.get("full_name", ""),
        "is_private": u.get("is_private", False),
        "followers":  u.get("follower_count"),
        "following":  u.get("following_count"),
        "pic_url":    u.get("profile_pic_url_hd") or u.get("profile_pic_url") or "",
    }


# ── Медиа-хелперы ─────────────────────────────────────────────────────────────

def _media_items(body) -> list[dict]:
    if isinstance(body, list):
        return body
    if "items" in body:
        return body["items"] or []
    inner = body.get("response") or body
    if isinstance(inner, list):
        return inner
    if isinstance(inner, dict):
        if "items" in inner:
            return inner["items"] or []
        try:
            edges = (inner.get("data", {}).get("user", {})
                     .get("edge_owner_to_timeline_media", {}).get("edges", []))
            if edges:
                return [e.get("node", e) for e in edges]
        except Exception:
            pass
        raw = inner.get("edges") or []
        return [e.get("node", e) if isinstance(e, dict) else e for e in raw]
    return []


def _best_photo_url(item: dict) -> str:
    src = (item.get("carousel_media") or [item])[0]
    candidates = (src.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        for c in candidates:
            if c.get("width", 9999) <= 1080:
                return c.get("url", "")
        return candidates[0].get("url", "")
    return (item.get("display_url") or item.get("thumbnail_url")
            or item.get("thumbnail_src") or "")


# ── Получение базового списка ─────────────────────────────────────────────────

async def fetch_base_list(
    user_id: str,
    mode: str,
    limit: int,
    page_id: str = "",
) -> tuple[list[dict], str]:
    users_raw: list[dict] = []
    next_pid = ""

    async with httpx.AsyncClient(timeout=30) as http:
        if mode == "suggested":
            r = await _get(http, f"{config.BASE}/user/suggested/profiles",
                           params={"user_id": user_id, "expand_suggestion": "false"})
            if r.status_code != 200:
                raise RuntimeError(f"HikerAPI {r.status_code}: {r.text[:120]}")
            body  = r.json()
            inner = body if isinstance(body, list) else (body.get("response") or body)
            raw   = inner if isinstance(inner, list) else \
                    (inner.get("suggested_users") or inner.get("users") or [])
            for item in raw:
                users_raw.append((item.get("user") or item) if isinstance(item, dict) else item)
            users_raw = users_raw[:limit]
        else:
            next_pid = page_id
            while len(users_raw) < limit:
                params: dict = {"user_id": user_id}
                if next_pid:
                    params["page_id"] = next_pid
                r = await _get(http, f"{config.BASE}/user/{mode}", params=params)
                if r.status_code != 200:
                    raise RuntimeError(f"HikerAPI {r.status_code}: {r.text[:120]}")
                body = r.json()
                if "detail" in body:
                    raise RuntimeError(body["detail"])
                page_users = body.get("response", {}).get("users", [])
                users_raw.extend(page_users)
                next_pid = body.get("next_page_id") or ""
                if not next_pid or not page_users:
                    break
            users_raw = users_raw[:limit]

    return users_raw, next_pid


# ── Elite-сканирование одного профиля ────────────────────────────────────────

async def fetch_elite_profile(
    http: httpx.AsyncClient,
    uid: str,
    base_info: dict,
    api_sem: asyncio.Semaphore,
    gpt_sem: asyncio.Semaphore,
    analyze_fn: Callable[..., Awaitable[dict]],
    skip_followers: int,
    country_filter: str = "all",
    target_country: str = "",
) -> dict:
    username = base_info.get("username", "")

    # ── ранний пропуск ────────────────────────────────────────────────────────
    if base_info.get("is_verified") or base_info.get("is_private"):
        reason = "verified" if base_info.get("is_verified") else "private"
        return _skipped(uid, base_info, reason)

    # ── шаг 1: полный профиль ─────────────────────────────────────────────────
    profile: dict = {}
    async with api_sem:
        r = await _get(http, f"{config.HIKER_BASE}/v2/user/by/id", params={"id": uid})
    if r.status_code == 200:
        b = r.json()
        profile = b.get("user") or b.get("response") or b or {}

    # ── шаг 2: проверка порога подписчиков ───────────────────────────────────
    fc = profile.get("follower_count")
    if skip_followers > 0 and fc is not None and fc > skip_followers:
        return _skipped(uid, base_info, "followers", profile=profile)

    # ── шаг 3: about ─────────────────────────────────────────────────────────
    about: dict = {}
    async with api_sem:
        r = await _get(http, f"{config.HIKER_BASE}/v1/user/about", params={"id": uid})
    if r.status_code == 200:
        a = r.json()
        about = a if isinstance(a, dict) else {}

    # ── шаг 3.5: ранний пропуск по стране (до скачивания фото и GPT) ─────────
    if country_filter == "target" and target_country:
        u_country = (about.get("country") or "").strip().lower()
        if u_country and u_country != target_country.strip().lower():
            return _skipped(uid, base_info, "country", profile=profile)

    # ── шаг 4: фото постов (до 3 шт., итого с аватаром = 4) ──────────────────
    photo_urls: list[str] = []
    if (profile.get("media_count") or 1) > 0:
        async with api_sem:
            r = await _get(http, f"{config.HIKER_BASE}/gql/user/medias",
                           params={"user_id": uid, "flat": "true"})
        if r.status_code == 200:
            mb = r.json()
            if not (isinstance(mb, dict) and "detail" in mb):
                for item in _media_items(mb)[:9]:   # до 9 постов + 1 аватар = 10 фото
                    url = _best_photo_url(item)
                    if url:
                        photo_urls.append(url)

    # ── шаг 5: url аватара ────────────────────────────────────────────────────
    hd_info = profile.get("hd_profile_pic_url_info") or {}
    pic_url = (hd_info.get("url") if isinstance(hd_info, dict) else "") or \
              profile.get("profile_pic_url_hd") or profile.get("profile_pic_url") or \
              base_info.get("profile_pic_url_hd") or base_info.get("profile_pic_url") or ""

    # ── шаг 6: параллельная загрузка изображений ─────────────────────────────
    downloads = await asyncio.gather(
        download_bytes(http, pic_url),
        *[download_bytes(http, u) for u in photo_urls],
    )
    pic_bytes   = downloads[0]
    photo_bytes = [b for b in downloads[1:] if b]

    # ── шаг 7: vision-анализ ──────────────────────────────────────────────────
    bio       = profile.get("biography") or ""
    full_name = profile.get("full_name") or base_info.get("full_name", "")
    analysis  = await analyze_fn(pic_bytes, photo_bytes, bio, full_name, username, gpt_sem)

    return {
        "id":       uid,
        "username": username,
        "full_name": full_name,
        "biography": bio,
        "city_name": profile.get("city_name") or "",
        "country":   about.get("country") or "",
        "account_date": about.get("date") or "",
        "former_usernames": str(about.get("former_usernames") or "0"),
        "is_private":  bool(profile.get("is_private",  base_info.get("is_private",  False))),
        "is_verified": bool(profile.get("is_verified", base_info.get("is_verified", False))),
        "is_business": bool(profile.get("is_business", False)),
        "follower_count":   profile.get("follower_count"),
        "following_count":  profile.get("following_count"),
        "media_count":      profile.get("media_count"),
        "has_highlights":   bool(profile.get("has_highlight_reels")),
        "skip_reason": None,
        "analysis":    analysis,
        "_pic":        pic_bytes,
        "_photos":     photo_bytes,
    }


def _skipped(uid: str, base_info: dict, reason: str, profile: dict | None = None) -> dict:
    p = profile or {}
    hd_info = p.get("hd_profile_pic_url_info") or {}
    return {
        "id":       uid,
        "username": base_info.get("username", ""),
        "full_name": p.get("full_name") or base_info.get("full_name", ""),
        "biography": p.get("biography", ""),
        "city_name": p.get("city_name", ""),
        "country": "", "account_date": "", "former_usernames": "0",
        "is_private":  bool(p.get("is_private",  base_info.get("is_private",  False))),
        "is_verified": bool(p.get("is_verified", base_info.get("is_verified", False))),
        "is_business": bool(p.get("is_business", False)),
        "follower_count":   p.get("follower_count"),
        "following_count":  p.get("following_count"),
        "media_count":      p.get("media_count"),
        "has_highlights":   bool(p.get("has_highlight_reels")),
        "skip_reason": reason,
        "analysis": {},
        "_pic":     None,
        "_photos":  [],
    }


# ── Страна целевого пользователя ─────────────────────────────────────────────

async def fetch_user_country(user_id: str) -> str:
    """Возвращает страну пользователя из /v1/user/about или '' если недоступно."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await _get(http, f"{config.HIKER_BASE}/v1/user/about", params={"id": user_id})
    if r.status_code == 200:
        a = r.json()
        if isinstance(a, dict):
            return (a.get("country") or "").strip()
    return ""


# ── Полный elite-скан ─────────────────────────────────────────────────────────

async def elite_scan(
    user_id: str,
    mode: str,
    limit: int,
    skip_followers: int,
    analyze_fn: Callable[..., Awaitable[dict]],
    page_id: str = "",
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    country_filter: str = "all",
    target_country: str = "",
) -> tuple[list[dict], str]:
    users_raw, next_pid = await fetch_base_list(user_id, mode, limit, page_id)

    api_sem = asyncio.Semaphore(config.API_SEM)
    gpt_sem = asyncio.Semaphore(config.GPT_SEM)
    done    = [0]
    total   = len(users_raw)

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as http:
        async def _scan_one(u: dict) -> dict:
            uid    = str(u.get("pk") or u.get("id") or "")
            result = await fetch_elite_profile(
                http, uid, u, api_sem, gpt_sem, analyze_fn, skip_followers,
                country_filter=country_filter,
                target_country=target_country,
            )
            done[0] += 1
            if on_progress:
                await on_progress(done[0], total)
            return result

        results = await asyncio.gather(*[_scan_one(u) for u in users_raw])

    return list(results), next_pid
