"""Display-only context for a resumed variant evaluation (never sent to agents)."""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path


CONTEXT_ENV = "OMNI_ARENA_VARIANT_PROGRESS"
logger = logging.getLogger(__name__)


class VariantProgress:
    def __init__(self, context: dict):
        self.context = context
        self.cell_dir = Path(context["cell_dir"])
        self.target = int(context["target_episodes"])

    @classmethod
    def from_environment(cls, game: str) -> VariantProgress | None:
        raw = os.environ.get(CONTEXT_ENV)
        if not raw:
            return None
        try:
            context = json.loads(raw)
            if context["game"] != game:
                return None
            if context["result_file"] not in ("result.json", "match_result.json"):
                return None
            if not isinstance(context["model"], str) or int(context["target_episodes"]) < 1:
                return None
            return cls(context)
        except (ValueError, TypeError, KeyError):
            logger.warning("Ignoring invalid variant viewer context")
            return None

    def viewer_options(self) -> dict:
        return {
            "show_progress_panel": True,
            "progress_panel_width": 320,
            "progress_title": "Variant Test",
            "progress_initial": "Loading test history...",
        }

    def history(self) -> list[tuple[Path, dict]]:
        rows = []
        for path in sorted(self.cell_dir.rglob(self.context["result_file"])):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(result, dict) and result.get("status"):
                    rows.append((path, result))
            except (OSError, ValueError):
                # A result may still be being written. It is not a saved test yet.
                continue
        return rows

    def score(self, result: dict) -> float | None:
        if self.context.get("mode") == "pvp":
            value = (result.get("player_results", {}).get("player_1") or {}).get("score")
        elif self.context.get("mode") == "coop":
            value = result.get("coop_total_score")
        else:
            metrics = result.get("metrics") or {}
            value = (metrics.get("game") or {}).get("score", metrics.get("score"))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        return None

    def format(self, *, finished: bool = False) -> str:
        rows = self.history()
        completed = [result for _, result in rows if result["status"] == "ok"]
        scores = [score for result in completed if (score := self.score(result)) is not None]
        context = self.context
        skill = "ON (best skill)" if context.get("with_skill") else "OFF (no skill)"
        lines = [
            f"Game: {context['game']}",
            f"Variant: {context.get('variant', '')}",
            "",
            f"Model: {context['model']}",
            f"Skill: {skill}",
        ]
        if context.get("opponent_model"):
            lines.extend([f"P2: {context['opponent_model']}", "P2 skill: OFF", "Score: P1"])
        elif context.get("mode") == "coop":
            lines.extend(["Same model / skill for both players", "Score: team total"])
        lines.extend(["", f"Completed: {len(completed)} / {self.target}"])
        if finished:
            state = "complete" if len(completed) >= self.target else "episode ended"
            lines.append(f"> {state}")
        else:
            lines.append(f"> Test {len(completed) + 1} / {self.target} - running")
            lines.append(f"Attempt: {len(rows) + 1}")
        if scores:
            lines.append(f"mean={sum(scores) / len(scores):.4g}  best={max(scores):.4g}")
        lines.extend(["", "History (same model / variant / skill)"])
        if not rows:
            lines.append("No saved tests yet.")
        successful = 0
        history_lines = []
        for attempt, (path, result) in enumerate(rows, start=1):
            if result["status"] == "ok":
                successful += 1
                score = self.score(result)
                score_text = "n/a" if score is None else f"{score:.4g}"
                history_lines.append(f"#{successful}  score={score_text}  completed")
            else:
                history_lines.append(f"Attempt {attempt}: {result['status']} (not counted)")
            history_lines.append(f"  {path.parent.name}")
        lines.extend(history_lines)
        lines.extend(["", "Completed = saved run, not a game win."])
        return "\n".join(lines)

    def update(self, viewer, *, finished: bool = False) -> None:
        if viewer is not None:
            try:
                viewer.set_progress(self.format(finished=finished))
            except Exception:
                logger.warning("Could not refresh variant viewer history", exc_info=True)
