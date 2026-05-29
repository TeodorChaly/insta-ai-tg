"""
Фильтрация результатов скана по пользовательским настройкам.
"""

from i18n import t

DEFAULT_FILTER: dict = {
    "gender":        "any",     # "female" | "male" | "any"
    "country":       "target",  # "target" (страна скан-цели) | "all"
    "min_photos":    1,         # минимум загруженных фото
    "max_followers": 2_000,     # максимум подписчиков
}


def apply(
    profiles: list[dict],
    cfg: dict,
    target_country: str = "",
) -> tuple[list[dict], dict[str, int]]:
    gender        = cfg.get("gender",        DEFAULT_FILTER["gender"])
    country       = cfg.get("country",       DEFAULT_FILTER["country"])
    min_photos    = cfg.get("min_photos",    DEFAULT_FILTER["min_photos"])
    max_followers = cfg.get("max_followers", DEFAULT_FILTER["max_followers"])

    result: list[dict] = []
    stats: dict[str, int] = {
        "passed":      0,
        "skip_reason": 0,   # приватные / верифицированные / популярные
        "country":     0,
        "commercial":  0,
        "not_personal": 0,
        "gender":      0,
        "underage":    0,
        "photos":      0,
        "followers":   0,
    }

    for u in profiles:
        reject = None

        # профили с пометкой skip (приватные / верифицированные / много подписчиков / страна)
        if u.get("skip_reason"):
            sr = u["skip_reason"]
            if sr == "country":
                stats["country"] += 1
                reject = "different country (pre-filter)"
            elif sr == "followers":
                fc = u.get("follower_count")
                stats["followers"] += 1
                reject = f"too many followers ({fc:,})" if fc is not None else "too many followers"
            elif sr == "verified":
                stats["skip_reason"] += 1
                reject = "verified"
            elif sr == "private":
                stats["skip_reason"] += 1
                reject = "private"
            else:
                stats["skip_reason"] += 1
                reject = sr
        else:
            an = u.get("analysis") or {}

            # если vision-анализ не прошёл — пропускаем профиль без блокировки
            if not an:
                pass

            elif an.get("is_account_commercial"):
                stats["commercial"] += 1
                reject = "commercial account"

            elif an.get("is_personal_account") is False:
                stats["not_personal"] += 1
                reject = "not a personal account"

            elif (age := an.get("age_apr")) is not None and age < 18:
                stats["underage"] += 1
                reject = f"underage ({age})"

            else:
                detected_gender = an.get("gender", "unknown")
                if gender != "any" and detected_gender == "unknown":
                    stats["gender"] += 1
                    reject = "gender unknown"
                elif gender != "any" and detected_gender != gender:
                    stats["gender"] += 1
                    reject = f"wrong gender ({detected_gender})"

                if not reject and country == "target" and target_country:
                    u_c = (u.get("country") or "").strip().lower()
                    t_c = target_country.strip().lower()
                    if u_c and u_c != t_c:
                        stats["country"] += 1
                        reject = f"different country ({u.get('country')})"

                if not reject and min_photos > 0:
                    all_p = [p for p in ([u.get("_pic")] + (u.get("_photos") or [])) if p]
                    if len(all_p) < min_photos:
                        stats["photos"] += 1
                        reject = f"not enough photos ({len(all_p)})"

                if not reject:
                    fc = u.get("follower_count")
                    if fc is not None and fc > max_followers:
                        stats["followers"] += 1
                        reject = f"too many followers ({fc:,})"

        u["_reject_reason"] = reject
        if reject is None:
            stats["passed"] += 1
            result.append(u)

    return result, stats


def filter_summary(total: int, stats: dict[str, int], lang: str = "en") -> str:
    lines = [t("fs_passed", lang, n=stats["passed"], total=total)]
    if stats["skip_reason"]:  lines.append(t("fs_private",      lang, n=stats["skip_reason"]))
    if stats["followers"]:    lines.append(t("fs_followers",    lang, n=stats["followers"]))
    if stats["country"]:      lines.append(t("fs_country",      lang, n=stats["country"]))
    if stats["gender"]:       lines.append(t("fs_gender",       lang, n=stats["gender"]))
    if stats["commercial"]:   lines.append(t("fs_commercial",   lang, n=stats["commercial"]))
    if stats["not_personal"]: lines.append(t("fs_not_personal", lang, n=stats["not_personal"]))
    if stats["underage"]:     lines.append(t("fs_underage",     lang, n=stats["underage"]))
    if stats["photos"]:       lines.append(t("fs_photos",       lang, n=stats["photos"]))
    return "\n".join(lines)
