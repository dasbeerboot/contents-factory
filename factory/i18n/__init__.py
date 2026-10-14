"""Dot-keyed message catalogue. Missing keys fall back to the key string itself."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOGUE_DIR = Path(__file__).resolve().parent
FALLBACK_LANG = "en"


@lru_cache(maxsize=8)
def catalogue(lang: str) -> dict[str, str]:
    path = CATALOGUE_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: str(value) for key, value in data.items() if isinstance(key, str)}


def get(key: str, lang: str = FALLBACK_LANG, **values: object) -> str:
    text = catalogue(lang).get(key) or catalogue(FALLBACK_LANG).get(key) or key
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def keys(lang: str = FALLBACK_LANG) -> list[str]:
    return sorted(catalogue(lang))


def missing(lang: str) -> list[str]:
    return sorted(set(catalogue(FALLBACK_LANG)) - set(catalogue(lang)))


class Translator:
    def __init__(self, lang: str = FALLBACK_LANG) -> None:
        self.lang = lang

    def __call__(self, key: str, **values: object) -> str:
        return get(key, self.lang, **values)
