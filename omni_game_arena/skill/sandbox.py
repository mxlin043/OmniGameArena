"""Read-only sandbox for the analyzer harness.

The analyzer agent gets a small set of generic filesystem tools
(list_dir / read_text / read_image / grep), but each one is rooted at
a single round directory. Any attempt to escape (``..`` traversal,
absolute paths pointing elsewhere) is rejected. This lets the analyzer
behave like Claude in a CLI without exposing other runs / project
files.

Tools return plain Python dicts; the harness translates these into
Anthropic ``tool_result`` content blocks (string for text-shaped
results, list of blocks when an image is included).
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image


class RoundReadOnlyFS:
    """Path-sandboxed filesystem for one round's output directory."""

    # Size caps. Large file refusals are explicit so the model can choose
    # a different path rather than silently truncating mid-content.
    MAX_TEXT_BYTES = 1_000_000        # 1 MB
    MAX_IMAGE_BYTES = 5_000_000       # 5 MB
    MAX_GREP_RESULTS = 200            # lines

    def __init__(self, round_dir: str | Path, previous_skill: str | None = None):
        self.root: Path = Path(round_dir).resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"round_dir does not exist or is not a dir: {self.root}")
        self.previous_skill = previous_skill

    # -- path resolution -------------------------------------------------
    def _resolve(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` against root, refusing escapes."""
        rel = (rel_path or "").strip().lstrip("/").lstrip("\\")
        if rel == "":
            return self.root
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(
                f"Path '{rel_path}' escapes the round directory."
            ) from exc
        return candidate

    # -- list_dir --------------------------------------------------------
    def list_dir(self, subpath: str = "") -> dict:
        try:
            target = self._resolve(subpath)
        except PermissionError as e:
            return {"error": str(e)}
        if not target.exists():
            return {"error": f"Path not found: {subpath!r}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {subpath!r}"}

        entries: list[dict] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            entry: dict = {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    entry["size_bytes"] = child.stat().st_size
                except OSError:
                    entry["size_bytes"] = None
            entries.append(entry)
        rel_str = str(target.relative_to(self.root)) or "."
        return {"path": rel_str, "entries": entries}

    # -- read_text -------------------------------------------------------
    def read_text(self, path: str, *, max_chars: int | None = None) -> dict:
        try:
            target = self._resolve(path)
        except PermissionError as e:
            return {"error": str(e)}
        if not target.exists():
            return {"error": f"File not found: {path!r}"}
        if not target.is_file():
            return {"error": f"Not a file: {path!r}"}
        size = target.stat().st_size
        if size > self.MAX_TEXT_BYTES:
            return {
                "error": (
                    f"File too large: {size} bytes (max {self.MAX_TEXT_BYTES}). "
                    f"Use grep to find specific content instead."
                )
            }
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            return {"error": f"Not a UTF-8 text file: {e}"}
        if max_chars is not None and max_chars > 0 and len(content) > max_chars:
            return {
                "path": path,
                "content": content[:max_chars],
                "truncated": True,
                "total_chars": len(content),
            }
        return {"path": path, "content": content, "truncated": False}

    # -- read_image ------------------------------------------------------
    def read_image(self, path: str) -> dict:
        try:
            target = self._resolve(path)
        except PermissionError as e:
            return {"error": str(e)}
        if not target.exists():
            return {"error": f"File not found: {path!r}"}
        if not target.is_file():
            return {"error": f"Not a file: {path!r}"}
        size = target.stat().st_size
        if size > self.MAX_IMAGE_BYTES:
            return {
                "error": (
                    f"Image too large: {size} bytes (max {self.MAX_IMAGE_BYTES})."
                )
            }
        try:
            img = Image.open(target).convert("RGB")
        except Exception as e:  # noqa: BLE001
            return {"error": f"Failed to load image: {e}"}
        return {"path": path, "image": img, "size": img.size}

    # -- grep ------------------------------------------------------------
    def grep(
        self,
        pattern: str,
        glob_pattern: str = "**/*.json",
        *,
        ignore_case: bool = False,
    ) -> dict:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"error": f"Invalid regex {pattern!r}: {e}"}

        matches: list[dict] = []
        truncated = False
        for path in sorted(self.root.glob(glob_pattern)):
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
                if size > self.MAX_TEXT_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(self.root)
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append({
                        "file": str(rel).replace("\\", "/"),
                        "line": i,
                        "content": line[:500],
                    })
                    if len(matches) >= self.MAX_GREP_RESULTS:
                        truncated = True
                        break
            if truncated:
                break
        return {
            "pattern": pattern,
            "glob": glob_pattern,
            "matches": matches,
            "truncated": truncated,
            "count": len(matches),
        }

    # -- get_previous_skill ----------------------------------------------
    def get_previous_skill(self) -> dict:
        if self.previous_skill is None:
            return {"skill": None, "message": "No previous skill (this is round 1)."}
        return {"skill": self.previous_skill}
