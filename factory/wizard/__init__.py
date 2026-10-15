"""Guided flows: the CLI asks, the agent reads it out, the user answers.

PLAN §9. `questions.py` is the catalogue, `engine.py` the state machine, `apply.py` the
side effects (ordinary factory commands), `state.py` the one state file, `cli.py` the
command surface.
"""
from __future__ import annotations

from . import apply, classify, engine, questions, state, templates

__all__ = ["apply", "classify", "engine", "questions", "state", "templates"]
