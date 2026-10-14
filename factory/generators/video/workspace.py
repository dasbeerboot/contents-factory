"""Where one video's intermediate files live, and what each stage left behind.

A reel is built one stage per command, so the stages cannot pass artifacts in
memory. They pass them through this directory, and `stages.json` records what
exists so a later stage can refuse to start on a missing input instead of
producing a silently empty render.

`stages.json` is written only from here, by the `factory gen video` command
(README: state under the runtime home is CLI-owned).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...errors import FactoryError

STAGES: tuple[str, ...] = ("script", "still", "clip", "tts", "captions",
                           "assemble")
RECORD_FILENAME = "stages.json"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def for_brief(cls, home: Path, brief_id: str) -> "Workspace":
        return cls(root=Path(home) / "outputs" / brief_id)

    def directory(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # Canonical artifact locations. Stages agree on these names and nothing else.
    def still_prompt(self) -> Path:
        return self.root / "prompt_still.txt"

    def clip_prompt(self) -> Path:
        return self.root / "prompt_clip.txt"

    def script_json(self) -> Path:
        return self.root / "script.json"

    def still_image(self) -> Path:
        return self.root / "still" / "still.png"

    def clip_dir(self) -> Path:
        return self.root / "clip"

    def clip_video(self) -> Path:
        return self.clip_dir() / "clip.mp4"

    def tts_dir(self) -> Path:
        return self.root / "tts"

    def narration(self) -> Path:
        return self.tts_dir() / "narration.wav"

    def cards_json(self) -> Path:
        return self.root / "captions" / "cards.json"

    def caption_frames(self) -> Path:
        return self.root / "captions" / "frames"

    def final(self) -> Path:
        return self.root / "final.mp4"

    def record_path(self) -> Path:
        return self.root / RECORD_FILENAME

    def record(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.record_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def note(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one stage result. Last write for a stage wins."""
        if stage not in STAGES:
            raise FactoryError("STAGE_UNKNOWN", stage[:32])
        data = self.record()
        data[stage] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       **payload}
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.record_path().with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                        sort_keys=True), encoding="utf-8")
        temporary.replace(self.record_path())
        return data[stage]

    def require(self, path: Path, *, stage: str) -> Path:
        if not Path(path).is_file():
            raise FactoryError("STAGE_INPUT_MISSING", f"{stage}:{Path(path).name}"[:60])
        return Path(path)


def resolve_stages(selected: str) -> list[str]:
    """`all` means every stage in order; anything else is one named stage."""
    if selected in ("all", "", None):
        return list(STAGES)
    if selected not in STAGES:
        raise FactoryError("STAGE_UNKNOWN", str(selected)[:32])
    return [selected]
