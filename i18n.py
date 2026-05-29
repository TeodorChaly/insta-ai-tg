"""
Интернационализация. Загружает JSON-локали из папки locales/.
"""

import json
from pathlib import Path

_DIR = Path(__file__).parent / "locales"
_cache: dict[str, dict] = {}

SUPPORTED = {"en", "ru", "de"}
DEFAULT   = "en"


def _load(lang: str) -> dict:
    if lang not in _cache:
        path = _DIR / f"{lang}.json"
        _cache[lang] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _cache[lang]


def t(key: str, lang: str = DEFAULT, **kwargs) -> str:
    """Возвращает строку по ключу для указанного языка (fallback → EN)."""
    text = _load(lang).get(key) or _load(DEFAULT).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
