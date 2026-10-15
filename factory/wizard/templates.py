"""persona.md, writer_rules.md and brand.md bodies for the three tone answers.

The setup wizard asks one tone question and writes three files from it, because asking
a beginner to write a persona document is where onboarding stops. The templates are
English on purpose: they are model-facing prefix text, and the language the posts come
out in is a separate answer (`content_lang`).

Each tone differs in the voice section and the closing habit, not in the structure, so
a user who later edits these files finds the same headings in all three.
"""
from __future__ import annotations

from ..errors import FactoryError

TONES: tuple[str, ...] = ("warm", "dry", "playful")

_VOICE: dict[str, list[str]] = {
    "warm": [
        "- Second person, close but never pushy. You speak to one reader, not an audience.",
        "- Short sentences. Plain words. Name the feeling before the advice.",
        "- You admit what was hard. You never perform suffering.",
    ],
    "dry": [
        "- Flat declaratives. No adjectives doing a verb's job.",
        "- One claim per piece, with the number or the consequence attached.",
        "- You state which way you went, including when it was the wrong way.",
    ],
    "playful": [
        "- Quick rhythm, concrete images, one joke that carries the point.",
        "- The punchline is the argument. If it is only a joke, cut it.",
        "- You tease the situation, never the reader.",
    ],
}
_NEVER: dict[str, list[str]] = {
    "warm": ["- Promise an outcome for the reader. Report your own instead.",
             "- Open with 'Let's talk about' or a rhetorical question.",
             "- Close with the same comforting line twice."],
    "dry": ["- Pile three adjectives where one works.",
            "- Open with a definition or a dictionary quote.",
            "- End on a slogan."],
    "playful": ["- Make a joke the reader is the butt of.",
                "- Open with 'POV:' or a trend format you did not earn.",
                "- Reuse a punchline that already went out."],
}
_CLOSER: dict[str, str] = {
    "warm": "Close on one small thing the reader can do today. Change the wording every time.",
    "dry": "Close on a check the reader can run on their own work. Never the same sentence twice.",
    "playful": "Close on the turn, not on a call to action. Vary it every time.",
}


def _tone(tone: str) -> str:
    if tone not in TONES:
        raise FactoryError("TONE_UNKNOWN", str(tone)[:32])
    return tone


def persona(brand: str, intro: str, tone: str) -> str:
    _tone(tone)
    lines = [f"You are the voice of {brand}.", "",
             intro.strip() or f"{brand} publishes short, specific posts about its own work.",
             "", "## Identity", "",
             f"- {brand} is the name on the account. You write in first person as {brand}.",
             "- Specific over general: one decision, one number, one consequence per piece.",
             "- You write about work that already happened, not about plans.",
             "", "## Voice", ""]
    lines.extend(_VOICE[tone])
    lines.extend(["", "## What you never do", ""])
    lines.extend(_NEVER[tone])
    lines.extend(["", "## Signature move", "",
                  f"- {_CLOSER[tone]}"])
    return "\n".join(lines) + "\n"


def writer_rules(brand: str, intro: str, tone: str) -> str:
    _tone(tone)
    lines = ["# Writer rules", "",
             "- Open on the consequence, not the setup.",
             "- One claim per piece. A second claim becomes the next piece.",
             "- Every number carries a unit and a timeframe.",
             f"- {_CLOSER[tone]}",
             "- Aim for the lower half of the platform limit, then cut the last sentence.",
             f"- Stay inside what {brand} actually does: {intro.strip() or 'see brand.md'}."]
    return "\n".join(lines) + "\n"


def brand(name: str, intro: str, tone: str) -> str:
    _tone(tone)
    lines = ["# Brand", "", f"## {name}", "",
             f"- One line: {intro.strip() or 'fill this in'}",
             f"- Tone answer at setup: {tone}",
             "", "## Reference priority", "",
             "1. An approved asset in `assets/approved/` beats anything generated.",
             "2. A generated variant from a locked prompt in the brief.",
             "3. Fresh generation, only when neither of the above exists.",
             "", "## Colour and type", "",
             "- Ink `#14161A`, paper `#FAFAF7`, one accent of your choosing.",
             "- One accent per image, on a single element, never a background.",
             "- Sentence case everywhere. No all caps.",
             "", "## Imagery", "",
             "- Real surfaces, real light. No glossy 3D renders, no gradient meshes.",
             "- At most one short line of text inside an image, in the caption safe zone.",
             "", f"- End card: {name}"]
    return "\n".join(lines) + "\n"


def files(name: str, intro: str, tone: str) -> dict[str, str]:
    """The three documents the tone answer produces, keyed by filename."""
    return {"persona.md": persona(name, intro, tone),
            "writer_rules.md": writer_rules(name, intro, tone),
            "brand.md": brand(name, intro, tone)}
