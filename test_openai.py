"""
Тест реальной среды — полный цикл как в боте:
  HikerAPI (resolve + profile + about + photos) → скачать фото → OpenAI vision

Запуск:
  python test_openai.py                  # спросит username
  python test_openai.py nastya_fit       # сразу по нику
"""

import sys
import asyncio
import json
import time
import httpx

from dotenv import load_dotenv
load_dotenv()

import config
from instagram import resolve_user, download_bytes, _media_items, _best_photo_url
from vision import analyze_profile, _PROMPT
from logger import hiker_log, openai_log


def _sep(title: str = "") -> None:
    if title:
        print(f"\n── {title} {'─' * (54 - len(title))}")
    else:
        print("─" * 60)


async def main() -> None:
    # ── username ──────────────────────────────────────────────────
    if len(sys.argv) > 1:
        username = sys.argv[1].lstrip("@")
    else:
        username = input("Instagram username (без @): ").strip().lstrip("@")

    if not username:
        print("❌ username не введён")
        return

    print(f"\n{'=' * 60}")
    print(f"  Real Environment Test")
    print(f"  target: @{username}")
    print(f"{'=' * 60}")

    hiker_start  = hiker_log.total
    openai_start = openai_log.total

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as http:

        # ── шаг 1: resolve ────────────────────────────────────────
        _sep("Step 1: resolve user")
        info = await resolve_user(username)
        if not info or not info.get("user_id"):
            print(f"❌ @{username} не найден")
            return
        uid = info["user_id"]
        print(f"  ✅ found: @{info['username']}  —  {info.get('full_name', '')}")
        print(f"     id={uid}  followers={info.get('followers')}  private={info.get('is_private')}")

        if info.get("is_private"):
            print("  ⚠️  аккаунт приватный — анализ невозможен")
            return

        # ── шаг 2: полный профиль ─────────────────────────────────
        _sep("Step 2: full profile  /v2/user/by/id")
        from instagram import _get, _headers
        r = await _get(http, f"{config.HIKER_BASE}/v2/user/by/id", params={"id": uid})
        profile = {}
        if r.status_code == 200:
            b = r.json()
            profile = b.get("user") or b.get("response") or b or {}
            print(f"  ✅ followers={profile.get('follower_count')}  posts={profile.get('media_count')}")
            print(f"     bio: {(profile.get('biography') or '')[:80]}")
        else:
            print(f"  ❌ status {r.status_code}")

        # ── шаг 3: about ──────────────────────────────────────────
        _sep("Step 3: about  /v1/user/about")
        r = await _get(http, f"{config.HIKER_BASE}/v1/user/about", params={"id": uid})
        about = {}
        if r.status_code == 200:
            about = r.json() if isinstance(r.json(), dict) else {}
            print(f"  ✅ country={about.get('country')}  date={about.get('date')}")
        else:
            print(f"  ❌ status {r.status_code}")

        # ── шаг 4: фото ───────────────────────────────────────────
        _sep("Step 4: media  /gql/user/medias")
        photo_urls: list[str] = []
        if (profile.get("media_count") or 1) > 0:
            r = await _get(http, f"{config.HIKER_BASE}/gql/user/medias",
                           params={"user_id": uid, "flat": "true"})
            if r.status_code == 200:
                mb = r.json()
                if not (isinstance(mb, dict) and "detail" in mb):
                    for item in _media_items(mb)[:3]:
                        url = _best_photo_url(item)
                        if url:
                            photo_urls.append(url)
            print(f"  ✅ found {len(photo_urls)} post photo(s)")
        else:
            print("  ⚠️  0 posts — пропускаем")

        # ── шаг 5: скачивание ─────────────────────────────────────
        _sep("Step 5: download images")
        hd_info = profile.get("hd_profile_pic_url_info") or {}
        pic_url = (hd_info.get("url") if isinstance(hd_info, dict) else "") or \
                  info.get("pic_url", "")

        t0 = time.time()
        downloads = await asyncio.gather(
            download_bytes(http, pic_url),
            *[download_bytes(http, u) for u in photo_urls],
        )
        pic_bytes   = downloads[0]
        photo_bytes = [b for b in downloads[1:] if b]
        print(f"  ✅ avatar: {'%d bytes' % len(pic_bytes) if pic_bytes else 'None'}")
        for i, pb in enumerate(photo_bytes):
            print(f"     photo {i+1}: {len(pb):,} bytes")
        print(f"  downloaded in {time.time()-t0:.2f}s")

    # ── шаг 6: OpenAI vision ──────────────────────────────────────
    _sep("Step 6: OpenAI vision analysis")
    print(f"  sending {1 if pic_bytes else 0} avatar + {len(photo_bytes)} photos")
    print(f"  bio: {(profile.get('biography') or '')[:60]}")

    sem = asyncio.Semaphore(1)
    t0  = time.time()
    result = await analyze_profile(
        pic=pic_bytes,
        photos=photo_bytes,
        bio=profile.get("biography") or "",
        name=profile.get("full_name") or info.get("full_name", ""),
        username=username,
        sem=sem,
    )
    elapsed = time.time() - t0

    # ── итоги ─────────────────────────────────────────────────────
    _sep("Result")
    if not result:
        print("  ❌ пустой ответ (таймаут или ошибка)")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    _sep("Stats")
    hiker_used  = hiker_log.total  - hiker_start
    openai_used = openai_log.total - openai_start
    print(f"  HikerAPI requests: {hiker_used}")
    print(f"  OpenAI requests:   {openai_used}")
    print(f"  OpenAI time:       {elapsed:.2f}s")
    print(f"  Est. cost:         ~${(hiker_used + openai_used) * 0.001:.3f}")
    _sep()


if __name__ == "__main__":
    asyncio.run(main())
