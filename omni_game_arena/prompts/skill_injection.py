"""Utilities for adding prompt-skill text to VLM agents."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return _repo_root() / path


def load_prompt_skills(paths: list[str] | tuple[str, ...] | None) -> str:
    """Load prompt-skill markdown files and join them into one lesson block."""
    if not paths:
        return ""

    chunks: list[str] = []
    for raw in paths:
        path = _resolve_path(str(raw))
        if not path.exists():
            raise FileNotFoundError(f"Prompt skill file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)
