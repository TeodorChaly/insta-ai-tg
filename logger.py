"""
Логирование запросов к HikerAPI и OpenAI:
 · цветной вывод в терминал (каждый запрос)
 · logs/users.log  — итог каждого скана (JSON Lines, без накоплений)
 · logs/totals.log — накопленные итоги по каждому юзеру, перезаписывается после каждого скана
                     переживает перезапуск бота (читается при старте)
"""

import json
import time
import datetime
import threading
import contextvars
from collections import defaultdict
from pathlib import Path

# ── Директория логов ──────────────────────────────────────────────────────────

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

_USERS_LOG  = LOGS_DIR / "users.log"
_TOTALS_LOG = LOGS_DIR / "totals.log"
_file_lock  = threading.Lock()

# Контекст текущего скана (устанавливается в handlers.py перед elite_scan)
scan_context: contextvars.ContextVar[dict] = contextvars.ContextVar("scan_ctx", default={})


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── HikerAPI логгер ───────────────────────────────────────────────────────────

class HikerLogger:
    def __init__(self):
        self.total          = 0
        self.by_endpoint:   dict[str, int] = defaultdict(int)
        self._session_start = time.time()

    def _short(self, url: str) -> str:
        for base in ("https://api.hikerapi.com/v2", "https://api.hikerapi.com"):
            if url.startswith(base):
                url = url[len(base):]
                break
        return url.split("?")[0]

    def hit(self, url: str, status: int) -> None:
        self.total += 1
        ep  = self._short(url)
        self.by_endpoint[ep] += 1
        n   = self.by_endpoint[ep]
        bar = "█" * min(n, 25)
        ok  = "32" if status == 200 else "31"
        print(
            f"\033[36m[Hiker]\033[0m  "
            f"#{self.total:>4}  \033[{ok}m{status}\033[0m  "
            f"{ep:<42}  hits={n:>3}  {bar}",
            flush=True,
        )

    def summary(self) -> None:
        elapsed = time.time() - self._session_start
        print("\n\033[33m── HikerAPI summary ──────────────────────────────\033[0m")
        for ep, cnt in sorted(self.by_endpoint.items(), key=lambda x: -x[1]):
            print(f"  {cnt:>4}x  {ep}")
        print(f"  \033[1mИтого: {self.total} запросов за {elapsed:.1f}s\033[0m")
        print("\033[33m──────────────────────────────────────────────────\033[0m\n")


# ── OpenAI логгер ─────────────────────────────────────────────────────────────

class OpenAILogger:
    def __init__(self):
        self.total          = 0
        self._session_start = time.time()

    def log(self, model: str, prompt_tok: int, compl_tok: int, elapsed: float) -> None:
        self.total += 1
        total_tok = prompt_tok + compl_tok
        print(
            f"\033[35m[OpenAI]\033[0m "
            f"#{self.total:>4}  \033[36m{model:<18}\033[0m  "
            f"prompt={prompt_tok:>5}  compl={compl_tok:>4}  "
            f"total=\033[1m{total_tok:>5}\033[0m tok  "
            f"{elapsed:.2f}s",
            flush=True,
        )


# ── Суммарный лог по юзерам ───────────────────────────────────────────────────

class UserSummaryLogger:
    """
    users.log  — одна запись на скан (что было в этом скане)
    totals.log — один JSON-объект, перезаписывается после каждого скана:
                 накопленные итоги по каждому tg_user за всё время
    """

    def __init__(self):
        # tg_user_id (str) → {"hiker": N, "openai": N, "scans": N}
        self._totals: dict[str, dict[str, int]] = {}
        self._load_totals()

    def _load_totals(self) -> None:
        if _TOTALS_LOG.exists():
            try:
                data = json.loads(_TOTALS_LOG.read_text(encoding="utf-8"))
                self._totals = data.get("users", {})
            except Exception:
                pass

    def _save_totals(self) -> None:
        data = {
            "updated": _now(),
            "users": self._totals,
        }
        with _file_lock:
            _TOTALS_LOG.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def log_scan(
        self,
        tg_user:  int,
        target:   str,
        hiker:    int,
        openai:   int,
        profiles: int,
    ) -> None:
        key = str(tg_user)
        t   = self._totals.setdefault(key, {"hiker": 0, "openai": 0, "scans": 0})
        t["hiker"]  += hiker
        t["openai"] += openai
        t["scans"]  += 1

        # users.log — запись этого скана
        money = round((hiker + openai) * 0.001, 4)
        scan_entry = {
            "ts":            _now(),
            "tg_user":       tg_user,
            "target":        target,
            "scan_hiker":    hiker,
            "scan_openai":   openai,
            "scan_profiles": profiles,
            "money_spent":   money,
        }
        with _file_lock:
            with open(_USERS_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(scan_entry, ensure_ascii=False) + "\n")

        # totals.log — перезаписываем с актуальными накоплениями
        self._save_totals()

        # Терминал
        print(
            f"\033[33m[Users]\033[0m  tg={tg_user}  target=@{target}  "
            f"скан: hiker={hiker}  openai={openai}  профилей={profiles}  💰${money}  |  "
            f"итого: hiker={t['hiker']}  openai={t['openai']}  сканов={t['scans']}",
            flush=True,
        )


# ── глобальные синглтоны ──────────────────────────────────────────────────────
hiker_log   = HikerLogger()
openai_log  = OpenAILogger()
summary_log = UserSummaryLogger()
