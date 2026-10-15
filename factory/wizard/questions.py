"""The question catalogue: what gets asked, in what order, and what counts as an answer.

The CLI owns the questions so that a weak agent cannot invent one, reorder them or skip
the connect step. Two flows:

  setup  seven steps, asked once per machine (PLAN §9.3)
  make   four steps, asked every time something gets made (PLAN §9.4)

A step can hold more than one question — "brand name, one line, tone" is one step and
three prompts — because a beginner answers one thing at a time but should still see
"3/7" rather than "6/13".

Answers arrive as the words a person would actually say, so every choice carries an
alias table in both languages. Anything the table cannot resolve is handed to the
classifier, and if there is no key for that, the question is asked again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from ..errors import FactoryError
from ..spec.profile import PLATFORMS

SETUP_STEPS = 7
MAKE_STEPS = 4

CHOICE = "choice"
MULTI = "multi"
TEXT = "text"
CONFIRM = "confirm"
LANG = "lang"
DRAFT = "draft"
WHEN = "when"
SLOTS = "slots"

TEXT_MAX = 400
# Longer than this, a draft answer is treated as the correction itself.
DRAFT_SHORT = 12


@dataclass(frozen=True)
class Question:
    id: str
    step: int
    kind: str
    choices: tuple[str, ...] = ()
    optional: bool = False
    # Connect questions carry the one thing to do and the command that proves it worked.
    target: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def steps(self) -> int:
        return SETUP_STEPS if self.id in SETUP_IDS else MAKE_STEPS


@dataclass(frozen=True)
class Connect:
    target: str
    action: str
    verify: str
    link: str = ""
    keys: tuple[str, ...] = ()


# One action and one verification per thing to connect. `{profile}` is filled in with the
# profile the brand answers created. Keys are pasted through `factory key set`, never
# echoed, never put in argv.
CONNECTS: dict[str, Connect] = {
    "anthropic": Connect(
        "anthropic", "factory key set --profile {profile} --name ANTHROPIC_API_KEY",
        "factory key list --profile {profile}",
        "https://console.anthropic.com/settings/keys", ("ANTHROPIC_API_KEY",)),
    "higgsfield": Connect(
        "higgsfield", "higgsfield auth login", "factory doctor",
        "https://higgsfield.ai"),
    "elevenlabs": Connect(
        "elevenlabs", "factory key set --profile {profile} --name ELEVENLABS_API_KEY",
        "factory key list --profile {profile}",
        "https://elevenlabs.io/app/settings/api-keys", ("ELEVENLABS_API_KEY",)),
    "threads": Connect(
        "threads", "factory auth threads --profile {profile}",
        "factory key list --profile {profile}",
        "https://developers.facebook.com/apps",
        ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")),
    "instagram": Connect(
        "instagram", "factory auth instagram --profile {profile}",
        "factory key list --profile {profile}",
        "https://developers.facebook.com/apps",
        ("IG_ACCESS_TOKEN", "IG_USER_ID")),
    "tiktok": Connect(
        "tiktok", "higgsfield auth login", "factory doctor",
        "https://higgsfield.ai"),
    "youtube": Connect(
        "youtube", "factory key set --profile {profile} --name YOUTUBE_REFRESH_TOKEN",
        "factory key list --profile {profile}",
        "https://console.cloud.google.com/apis/credentials",
        ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")),
    "naver_blog": Connect(
        "naver_blog", "factory naver login", "factory naver status",
        "https://blog.naver.com"),
}

# Platforms that need a narration voice, so ElevenLabs is only asked about when one of
# them is picked. Anthropic and Higgsfield are asked about always: one writes, the other
# draws, and no platform choice removes either.
VOICE_PLATFORMS: frozenset[str] = frozenset({"tiktok", "youtube"})
ALWAYS_CONNECT: tuple[str, ...] = ("anthropic", "higgsfield")

# Answers as people type them. Matching is exact first, then substring, so "맥북이요" and
# "I'm on a mac" both land on macos.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "os": {"macos": ("macos", "mac", "osx", "apple", "맥", "맥북", "맥미니", "애플"),
           "windows": ("windows", "win", "pc", "윈도", "윈도우", "윈두"),
           "linux": ("linux", "ubuntu", "debian", "리눅스", "우분투")},
    "ui_lang": {"ko": ("ko", "korean", "한국", "한국어", "국어", "우리말"),
                "en": ("en", "english", "영어", "잉글리시")},
    "content_lang": {"ko": ("ko", "korean", "한국", "한국어"),
                     "en": ("en", "english", "영어"),
                     "ja": ("ja", "japanese", "일본", "일본어"),
                     "es": ("es", "spanish", "스페인", "스페인어")},
    "brand_tone": {"warm": ("warm", "kind", "soft", "따뜻", "다정", "친근", "부드"),
                   "dry": ("dry", "flat", "plain", "건조", "담담", "차분", "담백"),
                   "playful": ("playful", "fun", "funny", "장난", "재미", "유머", "가볍")},
    "platforms": {"threads": ("threads", "thread", "쓰레드", "스레드", "트레드"),
                  "instagram": ("instagram", "insta", "ig", "인스타", "인스타그램"),
                  "tiktok": ("tiktok", "tik tok", "틱톡"),
                  "youtube": ("youtube", "yt", "shorts", "유튜브", "쇼츠"),
                  "naver_blog": ("naver_blog", "naver", "blog", "네이버", "블로그")},
    "slots": {"morning": ("morning", "am", "아침", "오전", "출근"),
              "noon": ("noon", "lunch", "midday", "점심", "낮", "정오"),
              "evening": ("evening", "night", "저녁", "밤", "퇴근"),
              "custom": ("custom", "myself", "직접", "내가", "지정")},
    "what": {"text": ("text", "post", "write", "글", "포스트", "게시글", "캡션", "블로그"),
             "image": ("image", "photo", "picture", "이미지", "사진", "그림", "카드"),
             "video": ("video", "reel", "short", "영상", "비디오", "릴스", "쇼츠"),
             "talk": ("talk", "explain", "dunno", "말로", "설명", "모르", "몰라", "아무")},
    "confirm": {"yes": ("yes", "y", "yep", "yeah", "ok", "okay", "sure", "done",
                        "did it", "finished", "all set", "네", "예", "응",
                        "ㅇㅋ", "ㅇ", "그래", "했어", "했어요", "완료", "좋아"),
                "no": ("no", "n", "nope", "later", "skip", "not yet", "아니", "아뇨",
                       "나중", "안했", "못했", "패스", "건너")},
    "draft": {"ok": ("ok", "okay", "good", "as is", "ship it", "이대로", "ㅇㅋ", "좋아",
                     "맘에", "그대로"),
              "again": ("again", "retry", "redo", "another", "다시", "새로", "재생성")},
    "when": {"now": ("now", "asap", "지금", "바로", "당장"),
             "next": ("next", "next slot", "다음", "다음 슬롯", "다음번", "이따")},
}

# Local wall-clock hour for each named slot answer. Converted to UTC before it touches a
# profile, because slots are UTC everywhere else in this project.
SLOT_HOURS: dict[str, tuple[int, int]] = {"morning": (8, 0), "noon": (12, 30),
                                          "evening": (20, 0)}

SETUP_IDS: frozenset[str] = frozenset({
    "os", "ui_lang", "content_lang", "brand_name", "brand_intro", "brand_tone",
    "platforms", "slots", "slots_custom", "first_post",
    *(f"connect.{target}" for target in CONNECTS)})

SLUG = re.compile(r"[^A-Za-z0-9_-]+")
WORDLIKE = re.compile(r"\w", re.UNICODE)
# A brand name in Hangul, Kana or Cyrillic leaves nothing for a directory name, and
# "그 이름은 못 씁니다" to a Korean user on question four is where onboarding ends. The
# display name is kept as typed; only the directory falls back to this.
FALLBACK_PROFILE = "brand"
LANG_TAG = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
LOCAL_TIME = re.compile(r"^(\d{1,2})\s*(?::|시|h)?\s*(\d{2})?\s*분?$")
LOCAL_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})$")
UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def slug(text: str) -> str:
    """ASCII part of a brand name, usable as a directory name. May be empty."""
    folded = SLUG.sub("-", str(text or "").strip().lower()).strip("-")
    return folded[:40]


def profile_slug(text: str, taken: Sequence[str] = ()) -> str:
    """The profile directory for this brand name, never empty, never a collision."""
    base = slug(text) or FALLBACK_PROFILE
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base[:36]}-{suffix}"
        if candidate not in taken:
            return candidate
    raise FactoryError("PROFILE_EXISTS", base[:32])


# "아직 안했어요" contains 했어, which is a yes alias, so a plain substring scan reads a
# refusal as consent. Negation is checked before anything else on confirm questions: a
# false yes on a connect step means the wizard moves on from a platform that is not
# connected, and the failure surfaces much later as a token error.
NEGATION = re.compile(r"안\s*(했|됐|돼|함|해|깔|넣)|못\s*했|아직|아니|아뇨|나중|패스|건너|"
                      r"\bno\b|\bnot\b|n't|never|later|skip")


def substring_aliases(words: Sequence[str]) -> list[str]:
    """Aliases long enough to be matched inside a sentence.

    One ASCII character is too little ("n" would hit "and"), but one Hangul syllable is
    a whole word: "글이요" is an answer to "글 / 이미지 / 영상", and dropping 글 for being
    one character was why that answer came back as "not one of the choices".
    """
    return [word for word in words if len(word) >= 2 or not word.isascii()]


def resolve_confirm(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if NEGATION.search(text):
        return "no"
    return resolve("confirm", text)


def resolve(group: str, raw: str) -> str:
    """Map a typed answer onto a choice value, or "" when nothing matches."""
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    table = ALIASES.get(group, {})
    for value, words in table.items():
        if text == value or text in words:
            return value
    for value, words in table.items():
        if any(word in text for word in substring_aliases(words)):
            return value
    return ""


def resolve_many(group: str, raw: str) -> list[str]:
    """Multi select: every choice mentioned anywhere in the answer, in catalogue order."""
    text = str(raw or "").strip().lower()
    if not text:
        return []
    table = ALIASES.get(group, {})
    found: list[str] = []
    for piece in re.split(r"[,/、\s]+", text):
        value = resolve(group, piece)
        if value and value not in found:
            found.append(value)
    if not found:
        for value, words in table.items():
            if any(word in text for word in substring_aliases(words)):
                found.append(value)
    order = list(table)
    return sorted(found, key=order.index)


def local_offset(now: datetime | None = None) -> timedelta:
    """This machine's UTC offset. The slot answer is a wall clock time, not UTC."""
    moment = now or datetime.now()
    return moment.astimezone().utcoffset() or timedelta(0)


def to_utc_slot(hour: int, minute: int, *, offset: timedelta | None = None) -> str:
    """`08:00` local -> `23:00Z`, written the way a profile slot has to look."""
    shift = local_offset() if offset is None else offset
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(
        hours=hour, minutes=minute) - shift
    return base.strftime("%H:%MZ")


def parse_local_times(raw: str, *, offset: timedelta | None = None) -> list[str]:
    """"8시, 20:30" -> UTC slot strings. Empty list means the answer was unusable."""
    out: list[str] = []
    for piece in re.split(r"[,/、\s]+", str(raw or "").strip()):
        if not piece:
            continue
        match = LOCAL_TIME.match(piece)
        if not match:
            return []
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return []
        slot = to_utc_slot(hour, minute, offset=offset)
        if slot not in out:
            out.append(slot)
    return out


def parse_when(raw: str, *, now: datetime | None = None,
               offset: timedelta | None = None) -> str:
    """A date answer as a UTC stamp. "" when the answer is not a date."""
    text = str(raw or "").strip()
    if UTC_STAMP.match(text):
        return text
    match = LOCAL_STAMP.match(text)
    if not match:
        return ""
    year, month, day, hour, minute = (int(part) for part in match.groups())
    shift = local_offset(now) if offset is None else offset
    try:
        wall = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return ""
    return (wall - shift).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect_targets(platforms: list[str]) -> list[str]:
    """What step 5 asks about: the two always-needed services, the narration key when a
    video platform is in play, then the chosen platforms in catalogue order."""
    targets = list(ALWAYS_CONNECT)
    if VOICE_PLATFORMS & set(platforms):
        targets.append("elevenlabs")
    targets.extend(name for name in PLATFORMS if name in platforms)
    return targets


def _connect_question(target: str) -> Question:
    entry = CONNECTS[target]
    return Question(id=f"connect.{target}", step=5, kind=CONFIRM,
                    choices=("yes", "no"), optional=True, target=target,
                    extras={"action": entry.action, "verify": entry.verify,
                            "link": entry.link, "keys": ",".join(entry.keys)})


def setup_plan(answers: dict[str, str]) -> list[Question]:
    plan = [
        Question("os", 1, CHOICE, ("macos", "windows", "linux")),
        Question("ui_lang", 2, CHOICE, ("ko", "en")),
        Question("content_lang", 2, LANG, ("ko", "en", "ja", "es")),
        Question("brand_name", 3, TEXT),
        Question("brand_intro", 3, TEXT),
        Question("brand_tone", 3, CHOICE, ("warm", "dry", "playful")),
        Question("platforms", 4, MULTI, PLATFORMS),
    ]
    chosen = [name for name in PLATFORMS
              if name in str(answers.get("platforms") or "").split(",")]
    if chosen:
        plan.extend(_connect_question(target) for target in connect_targets(chosen))
    plan.append(Question("slots", 6, SLOTS,
                         ("morning", "noon", "evening", "custom")))
    if answers.get("slots") == "custom":
        plan.append(Question("slots_custom", 6, TEXT))
    plan.append(Question("first_post", 7, CONFIRM, ("yes", "no")))
    return plan


def make_plan(answers: dict[str, str]) -> list[Question]:
    plan = [
        Question("what", 1, CHOICE, ("text", "image", "video", "talk")),
        Question("topic", 2, TEXT),
        Question("attach", 2, TEXT, optional=True),
    ]
    kind = answers.get("kind") or answers.get("what") or ""
    if kind in {"image", "video"}:
        plan.append(Question("cost_ok", 3, CONFIRM, ("yes", "no")))
    plan.append(Question("draft", 3, DRAFT, ("ok", "again", "fix")))
    plan.append(Question("when", 4, WHEN, ("now", "next", "date")))
    return plan


def plan_for(flow: str, answers: dict[str, str]) -> list[Question]:
    if flow == "setup":
        return setup_plan(answers)
    if flow == "make":
        return make_plan(answers)
    raise FactoryError("WIZARD_FLOW_UNKNOWN", str(flow)[:32])


def find(flow: str, answers: dict[str, str], question_id: str) -> Question | None:
    for question in plan_for(flow, answers):
        if question.id == question_id:
            return question
    return None


@dataclass(frozen=True)
class Verdict:
    ok: bool
    value: str = ""
    reason: str = ""


def validate(question: Question, raw: str) -> Verdict:
    """Check one answer. `value` is what gets stored; `reason` names the retry wording."""
    text = str(raw or "").strip()
    group = "confirm" if question.kind in {CONFIRM} else question.id
    if question.kind in {CHOICE, SLOTS}:
        value = resolve(group, text)
        return Verdict(True, value) if value else Verdict(False, reason=CHOICE)
    if question.kind == CONFIRM:
        value = resolve_confirm(text)
        return Verdict(True, value) if value else Verdict(False, reason=CONFIRM)
    if question.kind == MULTI:
        values = resolve_many(question.id, text)
        return (Verdict(True, ",".join(values)) if values
                else Verdict(False, reason=MULTI))
    if question.kind == LANG:
        value = resolve(group, text)
        if value:
            return Verdict(True, value)
        if LANG_TAG.match(text):
            return Verdict(True, text)
        return Verdict(False, reason=CHOICE)
    if question.kind == DRAFT:
        # A long answer is the correction, even when it happens to contain "좋아": "좋아요
        # 근데 마지막 줄만 빼줘" is an edit, and reading it as approval ships the draft the
        # user just asked to change. Only a short answer is allowed to mean yes or again.
        value = resolve("draft", text) if len(text) <= DRAFT_SHORT else ""
        if value:
            return Verdict(True, value)
        if len(text) >= 2:
            return Verdict(True, f"fix:{text[:TEXT_MAX]}")
        return Verdict(False, reason=DRAFT)
    if question.kind == WHEN:
        value = resolve("when", text)
        if value:
            return Verdict(True, value)
        stamp = parse_when(text)
        if stamp:
            return Verdict(True, f"date:{stamp}")
        return Verdict(False, reason=WHEN)
    if question.kind == TEXT:
        if not text:
            return Verdict(False, reason=TEXT)
        if question.id == "brand_name" and not WORDLIKE.search(text):
            return Verdict(False, reason="brand_name")
        if question.id == "slots_custom" and not parse_local_times(text):
            return Verdict(False, reason="slots_custom")
        return Verdict(True, text[:TEXT_MAX])
    raise FactoryError("WIZARD_ANSWER_INVALID", question.kind[:32])
