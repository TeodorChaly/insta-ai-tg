"""
GPT-4.1-mini vision-анализ Instagram-профиля.
"""

import asyncio
import base64
import json
import time

from openai import AsyncOpenAI

import config
from logger import openai_log

_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

_PROMPT = """Analyze this Instagram profile and return JSON with exactly these fields:

- is_personal_account: boolean — MOST IMPORTANT FIELD.
  true ONLY if this is a real person sharing their personal life (selfies, daily life, travel, friends, family).
  false if ANY of these apply:
  • art / illustration / photography / design portfolio (even if named "personal portfolio")
  • meme, humor, quotes, or fan page
  • hobby showcase (crafts, drawing, music covers, etc.) without personal life content
  • business, brand, shop, or service account
  • bot, spam, or fake-looking account (0 posts + random followers, wall-of-text bio)
  • news, entertainment, or educational content page
  When in doubt — return false.

- is_account_commercial: boolean
  true if the account sells something or has clear monetization intent (shop, prices, "DM for price", business email, brand collab focus).
  false otherwise.

- gender: "female"|"male"|"unknown"
  Check ALL signals: (1) username — extract first name if present (e.g. "nastya_fit"→female, "alex_93"→male); (2) full name; (3) bio pronouns/self-description; (4) visible face/body in photos. Return "unknown" ONLY if every signal is absent or contradictory.

- age_apr: integer — REQUIRED, never null. Estimate age using ALL available signals:
  • Face in photos → estimate by skin, wrinkles, facial structure
  • Body/posture → helps narrow range
  • Bio mentions (e.g. "class of 2015", "born 2000", age emojis)
  • Content style (school/uni posts → 16-22, family/kids → 25-40, career → 25-45)
  • If no photos at all → estimate from username/name style, default to 25
  Always return a single integer, never null.

- hair_color: string or null
- eyes_color: string or null
- figure_type: "slim"|"athletic"|"curvy"|"average" or null — null only if no person visible at all
- possible_interest: array of strings (max 5)
- account_languages: array of language codes (e.g. ["ru","en"])

First image = profile picture. Remaining = feed posts.
Return ONLY valid JSON, no markdown, no explanation."""


def _to_data_url(b: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(b).decode()}"


async def analyze_profile(
    pic: bytes | None,
    photos: list[bytes],
    bio: str,
    name: str,
    username: str,
    sem: asyncio.Semaphore,
) -> dict:
    """Анализирует профиль через GPT-4.1-mini vision. Возвращает dict или {}."""
    content: list[dict] = []

    if pic:
        content.append({
            "type": "image_url",
            "image_url": {"url": _to_data_url(pic), "detail": "low"},
        })
    for pb in photos[:3]:
        if pb:
            content.append({
                "type": "image_url",
                "image_url": {"url": _to_data_url(pb), "detail": "low"},
            })

    if not content:
        return {}

    text_parts = []
    if username: text_parts.append(f"Username: @{username}")
    if name:     text_parts.append(f"Name: {name}")
    if bio:      text_parts.append(f"Bio: {bio}")
    text_parts.append("\n" + _PROMPT)
    content.append({"type": "text", "text": "\n".join(text_parts)})

    t0 = time.time()
    try:
        async with sem:
            resp = await _client.chat.completions.create(
                model="gpt-5.4-nano",
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                max_completion_tokens=400,
            )
        elapsed = time.time() - t0
        openai_log.log(
            "gpt-5.4-nano",
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            elapsed,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        elapsed = time.time() - t0
        print(f"\033[35m[OpenAI]\033[0m  \033[31mERROR\033[0m  {e!s:.80}  {elapsed:.2f}s",
              flush=True)
        return {}
