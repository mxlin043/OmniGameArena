"""I/O utilities for IDC runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..logging_utils import timestamp_slug


def resolve_idc_run_dir(
    *,
    output_root: str,
    game_name: str,
    model: str,
    run_dir: str = "",
) -> Path:
    if run_dir:
        return Path(run_dir)
    safe_model = model.replace("/", "_")
    return Path(output_root) / game_name / safe_model / timestamp_slug()


def atomic_write_json(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, path)


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text or "")
    os.replace(tmp, path)


def load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def read_text_if_exists(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def relpath(path: str | Path, start: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(start).resolve()))
    except ValueError:
        return str(path)
