"""Repeat guard, ported from saju-threads scripts/repeat_guard.py.

Three rules, any one of which blocks:
  (a) exact_sentence : a normalized sentence of >= 8 words already went out
  (b) ngram_overlap  : 6-gram overlap against the corpus >= 40 percent
  (c) closer_repeat  : a fixed closer phrase already used >= 3 times in 7 days

Corpus is this factory's own published queue items for the profile, last 30 days. Closer
matching is whitespace bounded, not substring: "option my profile" must not count as
"on my profile" (2026-10-04 postmortem in the source repo).

Guard internals fail open: a guard bug must not stop the pipeline. The verdict logic itself
is deterministic, so passing never gets looser.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

WINDOW_DAYS = int(os.environ.get("FACTORY_REPEAT_WINDOW_DAYS") or 30)
NGRAM_N = int(os.environ.get("FACTORY_REPEAT_NGRAM_N") or 6)
NGRAM_RATIO = float(os.environ.get("FACTORY_REPEAT_NGRAM_RATIO") or 0.40)
EXACT_MIN_WORDS = int(os.environ.get("FACTORY_REPEAT_EXACT_MIN_WORDS") or 8)
CLOSER_DAYS = int(os.environ.get("FACTORY_REPEAT_CLOSER_DAYS") or 7)
CLOSER_MAX = int(os.environ.get("FACTORY_REPEAT_CLOSER_MAX") or 3)
CACHE_TTL = int(os.environ.get("FACTORY_REPEAT_CACHE_TTL") or 600)
CACHE_VERSION = 1

DEFAULT_CLOSERS: tuple[str, ...] = (
    "link in bio",
    "more on my profile",
    "on my profile",
    "check my profile",
    "dm me for more",
    "comment below",
    "let me know in the comments",
    "follow for more",
    "save this for later",
)

ALLOW: tuple[bool, str] = (True, "")

_WS = re.compile(r"\s+")
_KEEP = re.compile(r"[^0-9a-z가-힣ㄱ-ㆎ ]+")
_SENT_SPLIT = re.compile(r"[.!?\n…]+")
_MEMO: dict[str, dict[str, Any]] = {}

RETRY_HINT = ("\n\nYour previous attempt repeated wording that already went out. "
              "Write the next one with a different sentence structure than before. "
              "Different first move, different rhythm, different closing sentence. "
              "Do not reuse any phrase from your recent posts.")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _WS.sub(" ", _KEEP.sub(" ", folded)).strip()


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SENT_SPLIT.split(str(text or "")):
        normalized = normalize(chunk)
        if normalized:
            out.append(normalized)
    return out


def grams(text: str, size: int | None = None) -> set[str]:
    width = size or NGRAM_N
    tokens = normalize(text).split()
    if len(tokens) < width:
        return set()
    return {" ".join(tokens[index:index + width])
            for index in range(len(tokens) - width + 1)}


def closer_patterns(extra: Iterable[str] = ()) -> tuple[str, ...]:
    merged = list(DEFAULT_CLOSERS)
    for phrase in extra:
        normalized = normalize(phrase)
        if normalized and normalized not in merged:
            merged.append(normalized)
    return tuple(merged)


def _compiled(patterns: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((pattern, re.compile(rf"(?<!\S){re.escape(pattern)}(?!\S)"))
                 for pattern in patterns if pattern)


def _queue_dir(home: Path) -> Path:
    return Path(home) / "queue"


def _epoch(stamp: str) -> float | None:
    """ISO 8601 with or without offset to a UTC epoch. None when unparseable."""
    text = str(stamp or "").strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", text)
    if not match:
        day = re.match(r"(\d{4}-\d{2}-\d{2})$", text)
        if not day:
            return None
        try:
            return float(calendar.timegm(time.strptime(day.group(1), "%Y-%m-%d")))
        except ValueError:
            return None
    try:
        base = calendar.timegm(time.strptime(f"{match.group(1)}T{match.group(2)}",
                                             "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    offset = re.search(r"([+-])(\d{2}):?(\d{2})$", text)
    if offset:
        sign = 1 if offset.group(1) == "+" else -1
        base -= sign * (int(offset.group(2)) * 3600 + int(offset.group(3)) * 60)
    return float(base)


def collect_items(home: Path, profile: str, *, now: float | None = None,
                  window_days: int | None = None,
                  published_only: bool = True) -> list[dict[str, Any]]:
    """Queue rows for the profile inside the window. Broken files are skipped.

    `published_only=False` includes items still waiting in the queue, which is what
    duplicate_copy needs: queueing the same copy twice is a duplicate before it ships.
    """
    moment = time.time() if now is None else float(now)
    floor = moment - (window_days or WINDOW_DAYS) * 86400
    out: list[dict[str, Any]] = []
    directory = _queue_dir(home)
    try:
        names = sorted(path for path in directory.glob("*.json"))
    except OSError:
        return out
    for path in names:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("profile") != profile:
                continue
            published = bool(row.get("published"))
            if published_only and not published:
                continue
            if not str(row.get("text") or "").strip():
                continue
            stamp = _epoch(str(row.get("published_at") or row.get("scheduled_at") or ""))
            if stamp is None or stamp < floor:
                continue
            out.append({"id": str(row.get("id") or ""),
                        "ts": stamp,
                        "platform": str(row.get("platform") or "queue"),
                        "published": published,
                        "text": str(row["text"])})
    return out


def collect(home: Path, profile: str, *, now: float | None = None,
            window_days: int | None = None) -> list[tuple[float, str, str]]:
    """Published texts for the profile inside the window."""
    return [(row["ts"], row["platform"], row["text"])
            for row in collect_items(home, profile, now=now, window_days=window_days)]


def _cache_path(home: Path, profile: str) -> Path:
    return Path(home) / "state" / f"repeat_guard_cache.{profile}.json"


def _signature(home: Path) -> str:
    directory = _queue_dir(home)
    try:
        names = sorted(directory.glob("*.json"))
    except OSError:
        return "-"
    newest = 0
    for path in names:
        try:
            newest = max(newest, path.stat().st_mtime_ns)
        except OSError:
            continue
    return f"{len(names)}:{newest}"


def corpus(home: Path, profile: str, *, now: float | None = None,
           closers: Iterable[str] = ()) -> dict[str, Any]:
    moment = time.time() if now is None else float(now)
    signature = _signature(home)
    patterns = closer_patterns(closers)
    key = f"{home}|{profile}|{','.join(patterns)}"
    memo = _MEMO.get(key)
    if memo and memo["sig"] == signature and moment - memo["at"] < CACHE_TTL:
        return memo["idx"]

    index = _read_cache(home, profile, signature, moment)
    if index is None:
        rows = collect(home, profile, now=moment)
        seen: set[str] = set()
        unique: list[tuple[float, str, str]] = []
        for stamp, source, text in rows:
            normalized = normalize(text)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append((stamp, source, text))
        sentence_set: set[str] = set()
        gram_set: set[str] = set()
        closer_counts: dict[str, int] = {}
        sources: dict[str, int] = {}
        closer_floor = moment - CLOSER_DAYS * 86400
        compiled = _compiled(patterns)
        for stamp, source, text in unique:
            sources[source] = sources.get(source, 0) + 1
            for sentence in sentences(text):
                if len(sentence.split()) >= EXACT_MIN_WORDS:
                    sentence_set.add(sentence)
            gram_set |= grams(text)
            if stamp >= closer_floor:
                normalized = normalize(text)
                for pattern, expression in compiled:
                    if expression.search(normalized):
                        closer_counts[pattern] = closer_counts.get(pattern, 0) + 1
        index = {"sentences": sentence_set, "grams": gram_set, "closers": closer_counts,
                 "docs": len(unique), "sources": sources}
        _write_cache(home, profile, signature, index, moment)
    _MEMO[key] = {"sig": signature, "at": moment, "idx": index}
    return index


def _read_cache(home: Path, profile: str, signature: str, now: float) -> dict[str, Any] | None:
    try:
        cached = json.loads(_cache_path(home, profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("sig") != signature:
        return None
    if cached.get("v") != CACHE_VERSION or cached.get("window_days") != WINDOW_DAYS:
        return None
    if now - float(cached.get("at") or 0) >= CACHE_TTL:
        return None
    try:
        return {"sentences": set(cached["sentences"]), "grams": set(cached["grams"]),
                "closers": dict(cached["closers"]), "docs": int(cached["docs"]),
                "sources": dict(cached["sources"])}
    except (KeyError, TypeError, ValueError):
        return None


def _write_cache(home: Path, profile: str, signature: str,
                 index: dict[str, Any], now: float) -> None:
    path = _cache_path(home, profile)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(
            {"sig": signature, "at": now, "v": CACHE_VERSION, "window_days": WINDOW_DAYS,
             "ngram_n": NGRAM_N, "closer_days": CLOSER_DAYS,
             "exact_min_words": EXACT_MIN_WORDS, "docs": index["docs"],
             "sources": index["sources"], "closers": index["closers"],
             "sentences": sorted(index["sentences"]), "grams": sorted(index["grams"])},
            ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def judge(text: str, *, home: Path, profile: str, now: float | None = None,
          closers: Iterable[str] = ()) -> dict[str, Any]:
    index = corpus(home, profile, now=now, closers=closers)
    rules: list[dict[str, Any]] = []
    normalized = normalize(text)

    if normalized:
        for sentence in sentences(text):
            if len(sentence.split()) >= EXACT_MIN_WORDS and sentence in index["sentences"]:
                rules.append({"rule": "exact_sentence", "detail": sentence[:120],
                              "words": len(sentence.split())})
                break

        candidate = grams(text)
        if candidate:
            hits = len(candidate & index["grams"])
            ratio = hits / float(len(candidate))
            if ratio >= NGRAM_RATIO:
                rules.append({"rule": "ngram_overlap",
                              "detail": f"{NGRAM_N}-gram {hits}/{len(candidate)} = "
                                        f"{ratio * 100:.0f}% (limit {NGRAM_RATIO * 100:.0f}%)",
                              "ratio": round(ratio, 3)})

        for pattern, expression in _compiled(closer_patterns(closers)):
            if expression.search(normalized):
                used = int(index["closers"].get(pattern, 0))
                if used >= CLOSER_MAX:
                    rules.append({"rule": "closer_repeat",
                                  "detail": f"'{pattern}' used {used} times in "
                                            f"{CLOSER_DAYS}d (limit {CLOSER_MAX})",
                                  "pattern": pattern, "used": used})
                    break

    return {"verdict": "BLOCK" if rules else "PASS", "profile": profile, "rules": rules,
            "corpus": {"docs": index["docs"], "window_days": WINDOW_DAYS,
                       "sources": index["sources"]}}


def check_detail(text: str, *, home: Path, profile: str, now: float | None = None,
                 closers: Iterable[str] = ()) -> dict[str, Any]:
    try:
        return judge(text, home=home, profile=profile, now=now, closers=closers)
    except Exception as error:  # noqa: BLE001 - a guard bug must not stop publishing
        return {"verdict": "PASS", "profile": profile, "rules": [],
                "corpus": {"docs": 0, "window_days": WINDOW_DAYS, "sources": {}},
                "error": type(error).__name__}


def check(text: str, *, home: Path, profile: str, now: float | None = None,
          closers: Iterable[str] = ()) -> tuple[bool, str]:
    report = check_detail(text, home=home, profile=profile, now=now, closers=closers)
    if report["verdict"] == "PASS":
        return ALLOW
    first = report["rules"][0]
    return False, f"{first['rule']}({first['detail']})"


def check_blocks(blocks: Iterable[tuple[str, str]], *, home: Path, profile: str,
                 now: float | None = None,
                 closers: Iterable[str] = ()) -> tuple[bool, str]:
    for label, text in blocks:
        if not str(text or "").strip():
            continue
        allowed, reason = check(text, home=home, profile=profile, now=now, closers=closers)
        if not allowed:
            return False, f"{label} {reason}"
    return ALLOW


def retry_hint(reason: str = "") -> str:
    if not reason:
        return RETRY_HINT
    return RETRY_HINT + f"\nRejected because: {str(reason)[:160]}"
