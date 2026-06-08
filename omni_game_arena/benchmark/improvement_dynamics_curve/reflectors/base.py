"""Base data structures for IDC reflectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IDCEpisodeTrace:
    episode_id: str
    trace_dir: Path
    manifest: dict[str, Any]
    steps: list[dict[str, Any]]
    score: float | None = None


@dataclass
class IDCReflectionInput:
    game_name: str
    round_idx: int
    previous_skill: str
    episodes: list[IDCEpisodeTrace]
    aggregate: dict[str, Any] = field(default_factory=dict)
    best_skill: str = ""
    # round_dir is required by the agentic reflector (which roots its
    # read-only sandbox there) and ignored by the single-shot reflector.
    round_dir: Path | None = None
    # Persistent observation log carried over from earlier rounds.
    # Pure fact log ("round X ep Y step Z had W"), NOT advice - advice
    # belongs in skill_in / best_skill. Empty on round 0 or when no
    # earlier round wrote observations.
    notebook_so_far: str = ""
    # Set True ONLY on a retry after a previous attempt ended without
    # calling submit_skill. Adds an emphatic "you MUST submit" nudge to the
    # seed message. Never set on the first attempt.
    force_submit: bool = False


@dataclass
class IDCReflectionResult:
    prompt_text: str
    response_text: str
    skill_text: str
    api_response: Any = None
    # Agentic reflector fills these with the full multi-turn transcript
    # and per-tool-call trace; single-shot reflector leaves them None.
    messages: list[dict] | None = None
    trace: list[dict] | None = None
    # Replacement notebook produced this round (None = reflector chose
    # not to update; runner keeps the previous notebook in that case).
    notebook_out: str | None = None
