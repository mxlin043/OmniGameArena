"""IDC reflection lints - pre- and post-submission checks.

Pre-reflection
--------------
Signals computed from this round's traces + curve_so_far + best_so_far
that the reflector must be aware of *before* writing a memo. They are
staged into the round directory as ``lints.md`` (and ``regression_guard.md``
for the MAJOR_REGRESSION case), so the reflector picks them up via the
same ``read_text`` it already uses.

Post-submission
---------------
Hard rules checked on the reflector's submitted memo + notebook. The
reflector's ``submit_skill`` call is intercepted by the harness; if any
rule fails, the harness sends back a ``tool_result`` with the rejection
reason and the reflector must call ``submit_skill`` again. Bounded
``max_post_submit_retries`` to keep latency finite.

Design philosophy
-----------------
The previous run (20260523_183435) showed that the reflector's *reasoning*
is high quality but its *output* drifts. It identifies prescriptive
collapse and writes "I should revert to best_skill" in its analysis,
then submits a 10-bullet memo that keeps map-specific content. Prose
rules in the system prompt don't bite. Runner-side enforcement does.
"""

from __future__ import annotations

import re
from typing import Any


# -- Banned phrase patterns ---------------------------------------------
# Matched case-insensitively against memo + notebook text. These are
# patterns that strongly indicate map / level memorization, calibrated
# from real failures in the 20260523_183435 run. Add to this list when
# new violation patterns surface in production runs.
BANNED_PHRASES: list[tuple[str, str]] = [
    (r"\bhazard[-\s]?(marked|block|markings?)\b", "hazard-marked/block/markings"),
    (r"\byellow[-\s]?(black|striped|markings?)\b", "yellow-black/striped"),
    (r"\bsplit[-\s]?platforms?\b", "split-platform"),
    (
        r"\b(first|second|third|fourth|fifth|next|previous|last)\s+"
        r"(platform|block|obstacle|gap|jump|ledge)\b",
        "ordinal platform/block/obstacle/gap reference",
    ),
    (r"\bledge on the\s+(left|right|side)\b", "ledge on the (left|right|side)"),
    (
        r"\b(elevated|tall|short|small|narrow|wide)\s+"
        r"(block|platform|obstacle)\b",
        "size-adjective block/platform",
    ),
    (r"\bspawn\s+(point|position|area)\b", "spawn point/position"),
    (r"\b(starting|initial)\s+(platform|position|area|block)\b", "starting platform"),
    (
        r"\bfor\s+the\s+\w+(?:[- ]\w+)*\s+(specifically|in particular)\b",
        "for the X specifically",
    ),
    (
        r"\b(step|sub[- ]?step)\s+\d+\b",
        "explicit step number reference",
    ),
    (
        r"\bscore\s+(of\s+)?0?\.\d+\b",
        "specific score threshold (use 'low/mid/high score' instead)",
    ),
]


# -- Pre-reflection signal computation ----------------------------------
def compute_pre_signals(
    *,
    round_result: dict[str, Any],
    curve_so_far: list[dict[str, Any]] | None,
    best_so_far: dict[str, Any] | None,
    previous_round: dict[str, Any] | None,
    previous_skill: str | None = None,
    regression_threshold: float = 0.7,
    stagnant_relative_spread: float = 0.10,
    bimodal_min_gap: float = 0.15,
    min_baseline_bullets: int = 2,
) -> dict[str, dict[str, Any]]:
    """Compute objective signals for this round.

    Returns dict ``{signal_id: {severity, message, ...detail}}``. Only
    signals that triggered are returned.
    """
    signals: dict[str, dict[str, Any]] = {}

    cur_mean = _as_float(round_result.get("mean_score"))
    cur_scores = [
        _as_float(s) for s in (round_result.get("scores") or [])
    ]
    cur_scores = [s for s in cur_scores if s is not None]
    round_idx = round_result.get("round_idx", 0)

    # -- EMPTY_SKILL_DOWNSTREAM ------------------------------------------
    # Calibration: the gemini-3.1-pro-preview run 20260523_232112 collapsed
    # to a 30-byte header-only skill from round 3 onwards. Each subsequent
    # round inherited an empty skill_in, fired MAJOR_REGRESSION, the
    # validator dutifully enforced "delete only" -> empty memo again ->
    # stuck. This lint breaks the loop by telling the reflector to
    # rebuild a minimal baseline whenever skill_in is empty post-round-0.
    if (
        previous_skill is not None
        and isinstance(round_idx, int)
        and round_idx >= 1
    ):
        n_in_bullets = count_bullets(previous_skill)
        if n_in_bullets < min_baseline_bullets:
            signals["EMPTY_SKILL_DOWNSTREAM"] = {
                "severity": "high",
                "skill_in_bullets": n_in_bullets,
                "min_baseline_bullets": min_baseline_bullets,
                "message": (
                    f"skill_in.md has only {n_in_bullets} bullet(s) - "
                    f"the previous round's reflection collapsed to an "
                    "empty / near-empty skill (probably from over-"
                    "aggressive deletion under MAJOR_REGRESSION). The "
                    "player has effectively been running NO SKILL this "
                    "round; further deletion would do nothing. Your "
                    "task this reflection: REBUILD a minimal 2-5 bullet "
                    "baseline of basic game mechanics (forward "
                    "movement, jump timing, platform alignment, edge "
                    "awareness, etc.) grounded in what you observe in "
                    "the episode traces. Do NOT submit another empty / "
                    "title-only memo - that would extend the zombie "
                    "state. submit_diagnosis must list these new "
                    "bullets as additions (with referenced_signals "
                    "including EMPTY_SKILL_DOWNSTREAM)."
                ),
            }

    # NOTE: IDENTICAL_STEP_0 used to be detected here. Removed because
    # identical step-0 actions across K episodes are the EXPECTED outcome
    # of deterministic resets + temperature=0 (same input -> same output)
    # - not an agent bug. The signal fired every round and consumed
    # reflector attention without representing a real problem to act on.

    # -- MAJOR_REGRESSION ------------------------------------------------
    best_mean = _as_float((best_so_far or {}).get("mean_score"))
    if cur_mean is not None and best_mean is not None and best_mean > 0:
        if cur_mean < best_mean * regression_threshold:
            signals["MAJOR_REGRESSION"] = {
                "severity": "high",
                "current_mean_score": cur_mean,
                "best_mean_score": best_mean,
                "best_round": (best_so_far or {}).get("round_idx"),
                "threshold": regression_threshold,
                "message": (
                    f"Mean score regressed from {best_mean:.4f} (round "
                    f"{(best_so_far or {}).get('round_idx')}) to "
                    f"{cur_mean:.4f} - below {int(regression_threshold*100)}% "
                    "of the historical best. The runner has REPLACED your "
                    "skill_in.md with best_skill.md content. You may ONLY "
                    "DELETE bullets, not add prescriptive rules. K=K samples "
                    "from one regressed round are below the noise floor and "
                    "cannot justify a new heuristic."
                ),
            }

    # -- STAGNANT --------------------------------------------------------
    if curve_so_far and len(curve_so_far) >= 3:
        recent = [
            _as_float(p.get("mean_score")) for p in curve_so_far[-3:]
        ]
        recent = [r for r in recent if r is not None]
        if len(recent) == 3:
            avg = sum(recent) / 3
            spread = max(recent) - min(recent)
            if avg > 0 and spread / avg < stagnant_relative_spread:
                signals["STAGNANT"] = {
                    "severity": "medium",
                    "recent_means": recent,
                    "spread": spread,
                    "relative_spread": spread / avg,
                    "message": (
                        f"Last 3 rounds have mean_score in "
                        f"[{min(recent):.4f}, {max(recent):.4f}] (relative "
                        f"spread {spread/avg:.1%}). Skill is not adapting. "
                        "Consider whether (a) the remaining bullets are "
                        "actually being followed by the player, (b) the "
                        "bottleneck is now an obstacle the player keeps "
                        "missing rather than one the skill addresses, or "
                        "(c) further skill changes can't move K=K noise."
                    ),
                }

    # -- BIMODAL ---------------------------------------------------------
    if len(cur_scores) >= 3:
        sorted_s = sorted(cur_scores)
        gaps = [
            sorted_s[i + 1] - sorted_s[i] for i in range(len(sorted_s) - 1)
        ]
        max_gap = max(gaps) if gaps else 0
        if max_gap >= bimodal_min_gap:
            signals["BIMODAL"] = {
                "severity": "info",
                "scores_sorted": sorted_s,
                "max_gap": max_gap,
                "message": (
                    f"Scores cluster into two groups separated by a "
                    f"{max_gap:.3f} gap: {sorted_s}. The mean is hiding a "
                    "binary outcome (e.g. some episodes pass a gate, others "
                    "don't). Pay attention to *what makes the gate pass* "
                    "rather than tuning the mean."
                ),
            }

    return signals


def render_lints_markdown(
    signals: dict[str, dict[str, Any]], *, round_idx: int
) -> str:
    """Render pre-reflection signals as a markdown file for the reflector."""
    if not signals:
        return (
            f"# IDC Lints - Round {round_idx}\n\n"
            "No signals triggered this round. Proceed with normal "
            "reflection workflow.\n"
        )
    lines = [f"# IDC Lints - Round {round_idx}", ""]
    # High severity first
    severity_order = {"high": 0, "medium": 1, "info": 2}
    items = sorted(
        signals.items(),
        key=lambda kv: severity_order.get(kv[1].get("severity", "info"), 99),
    )
    for sig_id, body in items:
        sev = body.get("severity", "info").upper()
        lines.append(f"## [{sev}] {sig_id}")
        lines.append("")
        lines.append(body.get("message", "(no message)"))
        lines.append("")
    lines.append(
        "Address every HIGH signal in your reflection - they encode "
        "objective facts that prose-only reasoning has historically failed "
        "to act on. Your `submit_diagnosis` call must explicitly reference "
        "each high-severity signal."
    )
    return "\n".join(lines) + "\n"


# -- Post-submission validators -----------------------------------------
def check_banned_phrases(
    *, memo: str, notebook: str
) -> list[dict[str, Any]]:
    """Return list of hit records. Empty list = no violations."""
    hits: list[dict[str, Any]] = []
    for pattern, label in BANNED_PHRASES:
        rgx = re.compile(pattern, re.IGNORECASE)
        for field_name, text in (("memo", memo or ""), ("notebook", notebook or "")):
            for m in rgx.finditer(text):
                hits.append({
                    "field": field_name,
                    "label": label,
                    "pattern": pattern,
                    "match": m.group(0),
                })
    return hits


def count_bullets(text: str) -> int:
    if not text:
        return 0
    return sum(
        1 for line in text.splitlines()
        if line.lstrip().startswith(("-", "*", "+"))
    )


def check_bullet_budget(
    *, memo: str, skill_in: str, max_growth: int = 0
) -> dict[str, Any]:
    """Returns {ok, n_in, n_out, growth}. ok=False if memo grew too much."""
    n_in = count_bullets(skill_in)
    n_out = count_bullets(memo)
    growth = n_out - n_in
    # When skill_in is empty (round 0 baseline), don't enforce - any
    # initial skill is fine.
    if n_in == 0:
        return {"ok": True, "n_in": n_in, "n_out": n_out, "growth": growth}
    return {
        "ok": growth <= max_growth,
        "n_in": n_in,
        "n_out": n_out,
        "growth": growth,
        "max_growth": max_growth,
    }


def check_skill_unchanged(*, memo: str, skill_in: str) -> bool:
    """True if memo is byte-equal to skill_in after whitespace normalization."""
    return _normalize(memo) == _normalize(skill_in) and bool(_normalize(skill_in))


def build_rejection_message(
    *,
    banned_hits: list[dict[str, Any]],
    budget: dict[str, Any] | None,
    unchanged: bool,
) -> str | None:
    """Compose a single tool_result error message describing all violations.

    Returns ``None`` if there are no violations.
    """
    if not banned_hits and (budget is None or budget.get("ok")) and not unchanged:
        return None

    parts: list[str] = [
        "Your submit_skill was REJECTED by IDC post-submission lints. "
        "Fix the issues below and call submit_skill again.",
        "",
    ]

    if banned_hits:
        parts.append("BANNED PHRASE VIOLATIONS:")
        for hit in banned_hits[:12]:  # cap to avoid huge tool_result
            parts.append(
                f"  - {hit['field']}: matched {hit['label']!r} "
                f"(text: {hit['match']!r})"
            )
        if len(banned_hits) > 12:
            parts.append(f"  ...and {len(banned_hits) - 12} more.")
        parts.append(
            "These phrases name specific level features and will not "
            "generalize. Rewrite the bullets to describe the underlying "
            "phenomenon (camera/perspective/momentum) without naming the "
            "specific obstacle."
        )
        parts.append("")

    if budget is not None and not budget.get("ok"):
        parts.append("BULLET BUDGET VIOLATION:")
        parts.append(
            f"  skill_in has {budget['n_in']} bullets; your memo has "
            f"{budget['n_out']} (growth {budget['growth']:+d}, "
            f"max allowed {budget.get('max_growth', 0):+d})."
        )
        parts.append(
            "Compress before adding. For every new bullet, you must delete "
            "an existing one. Skills grow indefinitely otherwise."
        )
        parts.append("")

    if unchanged:
        parts.append("SKILL UNCHANGED:")
        parts.append(
            "Your memo is byte-identical to skill_in.md. If you intend no "
            "change, that's fine - but then justify in your diagnosis WHY "
            "the previous skill held up, what evidence supports keeping it, "
            "and what you'd change if anything became actionable. Submit "
            "again with the same memo if intentional."
        )
        parts.append("")

    parts.append(
        "Call submit_skill again with corrected memo (and notebook). "
        "You may also fix your diagnosis via submit_diagnosis."
    )
    return "\n".join(parts)


# -- helpers ------------------------------------------------------------
def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    """Whitespace-collapse for byte-equality skill comparison."""
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)
