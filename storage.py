"""
Персистентное хранилище:
 · data/<tg_user_id>.json — фильтр и лайки каждого пользователя
 · data/users.json        — реестр всех пользователей (id, username, дата первого входа)
"""

import asyncio
import json
import datetime
import threading
from pathlib import Path

import filters as flt

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_USERS_FILE = Path(__file__).parent / "users.json"
_lock = threading.Lock()

# per-user asyncio lock — гарантирует атомарность read-modify-write для кредитов
_credit_locks: dict[int, asyncio.Lock] = {}

def _credit_lock(tg_user: int) -> asyncio.Lock:
    if tg_user not in _credit_locks:
        _credit_locks[tg_user] = asyncio.Lock()
    return _credit_locks[tg_user]


def _path(tg_user: int) -> Path:
    return DATA_DIR / f"{tg_user}.json"


def load(tg_user: int) -> dict:
    p = _path(tg_user)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(tg_user: int, data: dict) -> None:
    # фиксированный порядок ключей: lang → filter → credits → liked → остальное
    ordered: dict = {}
    for key in ("terms_accepted", "lang", "filter", "credits", "liked"):
        if key in data:
            ordered[key] = data[key]
    for key, val in data.items():
        if key not in ordered:
            ordered[key] = val
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _path(tg_user).write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _update_users_file(tg_user: int, updates: dict) -> None:
    """Обновляет поля записи пользователя в users.json."""
    if not _USERS_FILE.exists():
        return
    with _lock:
        lines = _USERS_FILE.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("id") == tg_user:
                    entry.update(updates)
                new_lines.append(json.dumps(entry, ensure_ascii=False))
            except Exception:
                new_lines.append(line)
        _USERS_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def save_filter(tg_user: int, cfg: dict) -> None:
    data = load(tg_user)
    data["filter"] = cfg
    _save(tg_user, data)




def save_liked(tg_user: int, liked: list[dict]) -> None:
    # _pic/_photos — байты, в JSON не сериализуются, для /liked не нужны
    clean = [{k: v for k, v in u.items() if k not in ("_pic", "_photos")} for u in liked]
    data = load(tg_user)
    data["liked"] = clean
    _save(tg_user, data)


def get_filter(tg_user: int) -> dict:
    return load(tg_user).get("filter", flt.DEFAULT_FILTER.copy())


def get_liked(tg_user: int) -> list[dict]:
    return load(tg_user).get("liked", [])


# ── Язык ─────────────────────────────────────────────────────────────────────

def get_terms_accepted(tg_user: int) -> bool:
    return bool(load(tg_user).get("terms_accepted", False))


def set_terms_accepted(tg_user: int) -> None:
    data = load(tg_user)
    data["terms_accepted"] = True
    _save(tg_user, data)
    _update_users_file(tg_user, {"terms_accepted": True})


def get_lang(tg_user: int) -> str:
    return load(tg_user).get("lang", "en")


def save_lang(tg_user: int, lang: str) -> None:
    data = load(tg_user)
    data["lang"] = lang
    _save(tg_user, data)
    _update_users_file(tg_user, {"lang": lang})


# ── Кредиты ───────────────────────────────────────────────────────────────────

FREE_CREDITS = 3  # стартовый баланс для новых пользователей


def get_credits(tg_user: int) -> int:
    data = load(tg_user)
    if "credits" not in data:
        # первый запрос — 0 если аккаунт был удалён, иначе стартовый баланс
        data["credits"] = 0 if data.get("was_deleted") else FREE_CREDITS
        _save(tg_user, data)
    return data["credits"]


def delete_user(tg_user: int) -> None:
    """Стирает все данные пользователя, сохраняя баланс кредитов и флаг was_deleted."""
    credits = load(tg_user).get("credits", 0)
    _save(tg_user, {"was_deleted": True, "credits": credits})
    _update_users_file(tg_user, {
        "deleted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "credits": credits,
    })


async def add_credits(tg_user: int, amount: int) -> int:
    async with _credit_lock(tg_user):
        data = load(tg_user)
        data["credits"] = data.get("credits", FREE_CREDITS) + amount
        _save(tg_user, data)
        _update_users_file(tg_user, {"credits": data["credits"]})
        return data["credits"]


async def deduct_credit(tg_user: int, n: int = 1) -> bool:
    """Списывает n кредитов атомарно. Возвращает False если баланс меньше n."""
    async with _credit_lock(tg_user):
        data = load(tg_user)
        current = data.get("credits", FREE_CREDITS)
        if current < n:
            return False
        data["credits"] = current - n
        _save(tg_user, data)
        _update_users_file(tg_user, {"credits": data["credits"]})
        return True


# ── Реестр пользователей ──────────────────────────────────────────────────────

def register_user(tg_user: int, username: str | None, first_name: str | None) -> bool:
    """
    Добавляет пользователя в users.json при первом входе.
    Если пользователь уже был, но удалил профиль — обновляет запись как повторную регистрацию.
    Возвращает True если пользователь новый (или повторная регистрация), False если уже активен.
    """
    with _lock:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if _USERS_FILE.exists():
            lines = _USERS_FILE.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("id") != tg_user:
                    continue
                # нашли — если не удалён, значит активный пользователь
                if "deleted_at" not in entry:
                    return False
                # был удалён — обновляем как повторную регистрацию
                entry.pop("deleted_at", None)
                entry["rejoined_at"] = now
                entry["username"]    = username or entry.get("username", "")
                entry["first_name"]  = first_name or entry.get("first_name", "")
                entry["credits"]     = load(tg_user).get("credits", FREE_CREDITS)
                lines[i] = json.dumps(entry, ensure_ascii=False)
                _USERS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True

        # совсем новый пользователь
        entry = {
            "joined_at":  now,
            "id":         tg_user,
            "username":   username or "",
            "first_name": first_name or "",
            "credits":    FREE_CREDITS,
        }
        with open(_USERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True
