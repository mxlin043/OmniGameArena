"""Agentic IDC reflector.

Wraps the project's ``AnalyzerHarness`` (multi-turn tool-use loop) with an
IDC-specific system prompt + seed message + round-directory staging. The
reflector itself decides which episode artifacts to read; the runner does
not pre-package evidence into the prompt.

Implementation notes
--------------------
- The harness expects the backend to expose ``chat_with_tools``.
  Anthropic and OpenAI-compatible backends implement that method; other
  backends fail fast during reflector construction.
- Before invoking the harness, two helper files are staged inside the
  round directory so the agent can fetch IDC-specific context purely via
  ``read_text``:
      idc_context.json   curve_so_far / best_round / previous_round / delta
      best_skill.md      historical best-measured skill (may equal skill_in)
  These additions are idempotent (atomic_write) and survive resume.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from omni_game_arena.models.backends import pick_backend
from omni_game_arena.skill import AnalyzerHarness
from omni_game_arena.skill.prompts import TOOL_SPECS as _BASE_TOOL_SPECS

from ..io import atomic_write_json, atomic_write_text, read_text_if_exists
from ..lints import (
    compute_pre_signals,
    render_lints_markdown,
)
from .base import IDCReflectionInput, IDCReflectionResult


REPO_ROOT = Path(__file__).resolve().parents[4]
PROMPT_ROOT = REPO_ROOT / "omni_game_arena" / "prompts" / "idc"


_VALIDATE_SKILL_SPEC: dict = {
    "name": "validate_skill",
    "description": (
        "Ask a separate LLM judge to review a DRAFT memo (and optional "
        "notebook) before you commit to it via submit_skill. The judge "
        "checks semantic rules that simple text matching cannot — map "
        "memorization paraphrased, hidden prescription without numbers, "
        "diagnosis-memo misalignment, and whether lint signals are "
        "addressed. The judge returns a markdown report with 'Issues "
        "found' and 'Verdict' (ok | needs_revision). You decide what to "
        "do with the feedback. This tool is OPTIONAL — call it when you "
        "want a second pair of eyes on your draft. Hard cap: 5 calls "
        "per round; the 6th and beyond return a budget-exhausted "
        "message instructing you to submit now."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "memo": {
                "type": "string",
                "description": (
                    "The DRAFT skill memo you intend to submit. Must be "
                    "non-empty."
                ),
            },
            "notebook": {
                "type": "string",
                "description": (
                    "Optional DRAFT notebook update you also intend to "
                    "submit. Omit if you're not changing the notebook."
                ),
            },
        },
        "required": ["memo"],
    },
}


_SUBMIT_DIAGNOSIS_SPEC: dict = {
    "name": "submit_diagnosis",
    "description": (
        "REQUIRED before submit_skill. Submit a structured diagnosis of "
        "what happened this round and what you intend to change. This is "
        "a non-terminating tool — calling it does not end the loop; you "
        "still need to call submit_skill afterwards. Use it to force "
        "yourself to ENUMERATE the changes you're making, not just have "
        "general intentions. Concrete fields:\n"
        "- diagnosis: 1-3 sentences describing what dominated this round "
        "(success cause, failure cause, regression vs best, prescriptive "
        "collapse signal, etc.).\n"
        "- deletions: list of EXACT bullet snippets (or 'bullet N: <first "
        "few words>...') from skill_in.md you intend to DELETE in the "
        "next memo. May be empty only if no bullets failed this round.\n"
        "- additions: list of new bullet topics (NOT full text) you "
        "intend to add. Should usually be empty or much smaller than "
        "deletions. K=K samples from one round cannot justify new "
        "prescriptive rules.\n"
        "- referenced_signals: list of lint signal IDs from lints.md "
        "that your plan addresses (e.g. ['MAJOR_REGRESSION'])."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "deletions": {
                "type": "array", "items": {"type": "string"},
            },
            "additions": {
                "type": "array", "items": {"type": "string"},
            },
            "referenced_signals": {
                "type": "array", "items": {"type": "string"},
            },
        },
        "required": ["diagnosis", "deletions"],
    },
}


def _build_idc_tool_specs() -> list[dict]:
    """Clone the base analyzer tool specs and (a) extend ``submit_skill``
    with an optional ``notebook`` field, (b) add a non-terminating
    ``submit_diagnosis`` tool that the reflector MUST call before
    submit_skill.

    The notebook is a persistent observation log carried across IDC
    rounds. It is the reflector's own working memory (facts about what
    happened, not advice), distinct from the bounded skill prompt that
    the player sees. The default AnalyzerHarness still uses the base
    TOOL_SPECS; IDC passes this extended list explicitly.
    """
    specs = copy.deepcopy(_BASE_TOOL_SPECS)
    for spec in specs:
        if spec.get("name") != "submit_skill":
            continue
        props = spec.setdefault("input_schema", {}).setdefault("properties", {})
        props["notebook"] = {
            "type": "string",
            "description": (
                "Optional persistent observation log to carry into the next "
                "IDC round. If provided, this REPLACES the previous notebook "
                "(not appended). Write FACTS ONLY: 'round X ep Y step Z had "
                "W' lines describing what was observed (score deltas, visual "
                "anomalies, terminal frames, agent reasoning patterns). "
                "NEVER write operating advice or heuristics here — those go "
                "in `memo` (the player-facing skill). Bounded ~2000 tokens; "
                "edit and compress, do not append indefinitely. Omit this "
                "field if there is nothing new worth recording this round."
            ),
        }
        # Strengthen submit_skill description to mention the notebook +
        # diagnosis-first rule + post-submit lint risk.
        old_desc = spec.get("description") or ""
        if "submit_diagnosis" not in old_desc:
            spec["description"] = (
                old_desc.rstrip()
                + " You may also pass an optional `notebook` field; see "
                "that field's description. submit_diagnosis MUST have "
                "been called at least once before submit_skill, "
                "otherwise this call will be rejected with retry. The "
                "memo is also checked against banned-phrase and "
                "bullet-budget lints; violations bounce back as a "
                "tool_result with retry guidance."
            )
    # Append the new diagnosis + validator tools.
    specs.append(copy.deepcopy(_SUBMIT_DIAGNOSIS_SPEC))
    specs.append(copy.deepcopy(_VALIDATE_SKILL_SPEC))
    return specs


IDC_TOOL_SPECS: list[dict] = _build_idc_tool_specs()


class AgenticIDCReflector:
    """Multi-turn tool-use IDC reflector."""

    def __init__(
        self,
        *,
        game_name: str,
        model: str,
        temperature: float | None = 0.0,
        resize_size: int = 512,
        max_iterations: int = 100,
        max_text_chars_per_result: int = 80_000,
        require_diagnosis_before_skill: bool = True,
        regression_threshold: float = 0.7,
        validator_model: str | None = None,
        validator_temperature: float | None = 0.0,
        max_validate_skill_calls: int = 5,
    ):
        self.game_name = game_name
        self.model = model
        self.max_iterations = max(1, int(max_iterations))
        self.max_text_chars_per_result = max(1024, int(max_text_chars_per_result))
        self.require_diagnosis_before_skill = bool(require_diagnosis_before_skill)
        self.regression_threshold = float(regression_threshold)
        self.max_validate_skill_calls = max(0, int(max_validate_skill_calls))

        self.backend = pick_backend(
            model,
            temperature=temperature,
            resize=True,
            resize_size=resize_size,
        )
        if not hasattr(self.backend, "chat_with_tools"):
            raise ValueError(
                f"Agentic IDC reflector requires a backend with "
                f"chat_with_tools(); got {type(self.backend).__name__} "
                f"for model {model!r}. Use a backend that supports tool "
                f"calls, such as Anthropic Claude or OpenAI GPT."
            )

        self.system_prompt = self._load_prompt("agentic_base.md")
        self.validator_system_prompt = self._load_prompt("validator_base.md")

        # Validator backend: defaults to the reflector's own backend
        # (same model) when validator_model is None / empty. Validators
        # only need .chat(); no tool use, no multimodal.
        validator_model_resolved = (
            validator_model.strip() if validator_model else ""
        ) or model
        if validator_model_resolved == model:
            self.validator_backend = self.backend
        else:
            self.validator_backend = pick_backend(
                validator_model_resolved,
                temperature=validator_temperature,
                resize=False,
            )
        self.validator_model_name = validator_model_resolved

    # ── public API ─────────────────────────────────────────────────────
    def reflect(self, inp: IDCReflectionInput) -> IDCReflectionResult:
        round_dir = self._require_round_dir(inp)

        # Stage IDC-specific context as readable files inside round_dir.
        # The agent fetches them via read_text — no tool extension needed.
        self._stage_context_files(round_dir, inp)

        # Pre-reflection lints: objective signals computed from this
        # round's traces. Stage them as lints.md for the reflector to
        # read. If MAJOR_REGRESSION fires, also REPLACE skill_in.md in
        # the round dir with best_skill content + stage a regression
        # guard file (so the reflector sees best_skill as its baseline
        # without us mutating the input prev_skill string).
        signals = compute_pre_signals(
            round_result=self._extract_round_result_dict(inp),
            curve_so_far=(inp.aggregate or {}).get("curve_so_far") or [],
            best_so_far=(inp.aggregate or {}).get("best_so_far"),
            previous_round=(inp.aggregate or {}).get("previous_round"),
            previous_skill=inp.previous_skill or "",
            regression_threshold=self.regression_threshold,
        )
        atomic_write_text(
            round_dir / "lints.md",
            render_lints_markdown(signals, round_idx=inp.round_idx),
        )

        effective_previous_skill = inp.previous_skill or ""
        if "MAJOR_REGRESSION" in signals and inp.best_skill:
            # Replace skill_in.md with best_skill content so the
            # reflector's read_text("skill_in.md") returns the
            # historical best, not the failed skill. Save the original
            # alongside for audit.
            atomic_write_text(
                round_dir / "skill_in_pre_guard.md",
                inp.previous_skill or "",
            )
            atomic_write_text(round_dir / "skill_in.md", inp.best_skill)
            atomic_write_text(
                round_dir / "regression_guard.md",
                self._render_regression_guard(
                    signals["MAJOR_REGRESSION"], inp.round_idx
                ),
            )
            effective_previous_skill = inp.best_skill

        system_text = self.system_prompt
        seed = self._build_seed(inp, signals=signals)

        # Capture context needed by the validate_skill handler. We bind
        # these into a closure so the handler can re-read them on each
        # validate_skill call.
        validate_ctx = {
            "round_dir": round_dir,
            "skill_in_text": effective_previous_skill,
            "lints_md": (round_dir / "lints.md").read_text(encoding="utf-8")
                if (round_dir / "lints.md").exists() else "",
        }

        def _validate_skill_handler(args: dict, harness_self) -> str:
            return self._validate_skill(
                args=args,
                harness=harness_self,
                context=validate_ctx,
            )

        harness = AnalyzerHarness(
            backend=self.backend,
            round_dir=round_dir,
            game=inp.game_name,
            round_idx=inp.round_idx,
            n_episodes=int((inp.aggregate or {}).get("n") or 0),
            previous_skill=effective_previous_skill or None,
            max_iterations=self.max_iterations,
            max_text_chars_per_result=self.max_text_chars_per_result,
            system_prompt=system_text,
            seed_message=seed,
            tool_specs=IDC_TOOL_SPECS,
            require_diagnosis_before_skill=self.require_diagnosis_before_skill,
            soft_tools=("submit_diagnosis",),
            tool_handlers={"validate_skill": _validate_skill_handler},
        )
        memo, trace, messages = harness.run()

        # Persist soft-tool calls for audit (diagnoses + validations).
        diagnoses = harness.last_soft_calls.get("submit_diagnosis") or []
        if diagnoses:
            atomic_write_json(
                round_dir / "diagnosis_log.json",
                {"count": len(diagnoses), "calls": diagnoses},
            )
        validations = harness.last_soft_calls.get("validate_skill") or []
        if validations:
            atomic_write_json(
                round_dir / "validate_skill_log.json",
                {
                    "count": len(validations),
                    "cap": self.max_validate_skill_calls,
                    "calls": validations,
                },
            )
        skill = (memo or "").strip()
        response_text = skill or self._last_text_turn(messages)

        # Pull the optional notebook update out of the submission payload.
        # Treat empty / whitespace-only / unchanged-from-input as "no
        # update this round" so the runner keeps the previous notebook.
        notebook_out: str | None = None
        submission = getattr(harness, "last_submission", None)
        if isinstance(submission, dict):
            raw = submission.get("notebook")
            if isinstance(raw, str):
                candidate = raw.strip()
                if candidate and candidate != (inp.notebook_so_far or "").strip():
                    notebook_out = candidate
                    atomic_write_text(round_dir / "notebook_out.md", candidate)

        return IDCReflectionResult(
            prompt_text=seed,
            response_text=response_text,
            skill_text=skill,
            api_response=getattr(self.backend, "last_response_json", None),
            messages=messages,
            trace=trace,
            notebook_out=notebook_out,
        )

    # ── seed message ───────────────────────────────────────────────────
    def _build_seed(
        self,
        inp: IDCReflectionInput,
        *,
        signals: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        agg = inp.aggregate or {}
        n = agg.get("n")
        mean_score = agg.get("mean_score")
        delta = agg.get("delta_vs_previous_round")
        best = agg.get("best_so_far") or {}
        prev = agg.get("previous_round") or {}

        lines = [
            f"IDC round {inp.round_idx} just finished for game "
            f"{inp.game_name!r}.",
            "",
            f"This round ran {n if n is not None else 'K'} episodes under the "
            f"skill prompt in skill_in.md. Aggregate:",
            f"  mean_score = {_fmt(mean_score)}",
        ]
        if prev:
            lines.append(
                f"  previous_round (round {prev.get('round_idx')}): "
                f"mean_score={_fmt(prev.get('mean_score'))}"
            )
        if delta is not None:
            lines.append(f"  delta_vs_previous_round = {_fmt(delta)}")
        if best:
            lines.append(
                f"  best_so_far (round {best.get('round_idx')}): "
                f"mean_score={_fmt(best.get('mean_score'))}"
            )

        if signals:
            hi = [k for k, v in signals.items() if v.get("severity") == "high"]
            if hi:
                lines.append("")
                lines.append(
                    f"⚠️  Lint signals fired: {', '.join(hi)}. Read lints.md "
                    f"NOW (before doing any other investigation). The signals "
                    f"are objective and must be addressed in your "
                    f"submit_diagnosis call."
                )
            if "MAJOR_REGRESSION" in signals:
                lines.append(
                    "    Because MAJOR_REGRESSION fired, your skill_in.md "
                    "has been REPLACED with best_skill.md content (the "
                    "original is preserved at skill_in_pre_guard.md). "
                    "See regression_guard.md for the rules of this round."
                )

        lines.extend([
            "",
            "Per-episode score breakdown, full trajectories, and frames are "
            "under episodes/. The full curve so far + best/previous round "
            "info is in idc_context.json. The historical best skill is in "
            "best_skill.md (empty when no measured skill has beaten the "
            "baseline yet). Pre-reflection lints are in lints.md.",
            "",
            "A persistent observation log from earlier rounds is in "
            "notebook_so_far.md. Read it FIRST — it focuses what you need "
            "to look at this round and lets you build on facts already "
            "established, rather than re-discovering them. If you observe "
            "new things this round, return an updated notebook via "
            "submit_skill's `notebook` arg; otherwise omit that arg.",
            "",
            "Workflow (HARD requirement):",
            "1. Investigate the round (lints.md → context → notebook → "
            "skills → episodes → frames).",
            "2. Call submit_diagnosis with explicit deletions/additions "
            "lists. This is REQUIRED before submit_skill.",
            "3. Call submit_skill with the rewritten memo (and optional "
            "notebook). The memo is checked against banned-phrase and "
            "bullet-budget lints; violations bounce back with retry "
            "guidance — fix and call again.",
        ])
        if inp.round_idx == 0:
            lines.append(
                "\nNote: this is the round_00 reflection — episodes here "
                "are the official no-skill PDQ baseline carried over from "
                "runs/pdq, so they ran without any skill "
                "prompt. Your memo will become skill_1, which will be "
                "frozen for the round_01 measurement."
            )
        if getattr(inp, "force_submit", False):
            lines.extend([
                "",
                "⚠️ RETRY — your PREVIOUS attempt this round ended WITHOUT "
                "calling submit_skill, so it produced NO skill and was "
                "wasted. This time you MUST finish the loop: keep "
                "investigation brief, then call submit_diagnosis, then call "
                "submit_skill with a complete memo. Do NOT end your turn "
                "until submit_skill has been called — a plain text reply "
                "with no submit_skill tool call is a failure.",
            ])
        return "\n".join(lines)

    # ── validate_skill handler (LLM judge) ─────────────────────────────
    def _validate_skill(
        self,
        *,
        args: dict,
        harness,
        context: dict,
    ) -> str:
        """Handler for the validate_skill tool. Builds a judge prompt
        from the draft memo + this round's context and calls
        self.validator_backend.chat() once. Returns the judge's markdown
        report (or a budget-exhausted notice if the cap is reached).

        The handler is invoked AFTER the harness has already recorded
        this call in self.last_soft_calls["validate_skill"], so the
        count includes the current call.
        """
        n_so_far = len(harness.last_soft_calls.get("validate_skill") or [])
        if n_so_far > self.max_validate_skill_calls:
            return (
                f"validate_skill budget EXHAUSTED "
                f"({n_so_far}/{self.max_validate_skill_calls}). "
                "Stop validating and call submit_skill with whatever you "
                "have. If you still see issues, fix them yourself before "
                "submitting — the validator will not respond again this "
                "round."
            )

        memo = str(args.get("memo") or "").strip()
        if not memo:
            return (
                "validate_skill REJECTED: `memo` is empty. Provide your "
                "draft memo text."
            )
        notebook = str(args.get("notebook") or "").strip()

        # Latest diagnosis from this run (if any).
        diagnoses = harness.last_soft_calls.get("submit_diagnosis") or []
        latest_diagnosis = diagnoses[-1] if diagnoses else None

        prompt_parts = [
            f"# Validation request (call {n_so_far} of "
            f"{self.max_validate_skill_calls})",
            "",
            "## DRAFT MEMO",
            "```markdown",
            memo,
            "```",
            "",
        ]
        if notebook:
            prompt_parts.extend([
                "## DRAFT NOTEBOOK",
                "```markdown",
                notebook,
                "```",
                "",
            ])
        prompt_parts.extend([
            "## SKILL_IN (baseline for this round)",
            "```markdown",
            context.get("skill_in_text") or "(empty)",
            "```",
            "",
        ])
        if latest_diagnosis:
            prompt_parts.extend([
                "## LATEST DIAGNOSIS (from reflector's submit_diagnosis)",
                "```json",
                json.dumps(latest_diagnosis, ensure_ascii=False, indent=2),
                "```",
                "",
            ])
        if context.get("lints_md"):
            prompt_parts.extend([
                "## PRE-REFLECTION LINTS",
                context["lints_md"],
                "",
            ])
        prompt_parts.extend([
            "Apply the rules from your system prompt and return your "
            "markdown report (## Issues found / ## Verdict). Be strict "
            "but constructive.",
        ])
        user_message = "\n".join(prompt_parts)

        messages = [
            {"role": "system", "content": self.validator_system_prompt},
            {"role": "user", "content": user_message},
        ]
        try:
            response_text = self.validator_backend.chat(messages) or ""
        except Exception as exc:  # noqa: BLE001
            return (
                f"validate_skill internal error: {type(exc).__name__}: "
                f"{exc}. Proceed without this validation — judgement is "
                "advisory, not required."
            )

        if not response_text.strip():
            return (
                "validate_skill returned an empty response. Treat as "
                "advisory failure: proceed at your discretion."
            )
        # Tag the call number in the response so the reflector can read
        # it back from its own message history when iterating.
        return (
            f"--- validator response (call "
            f"{n_so_far}/{self.max_validate_skill_calls}) ---\n"
            f"{response_text.strip()}\n"
        )

    @staticmethod
    def _extract_round_result_dict(inp: IDCReflectionInput) -> dict[str, Any]:
        """Reconstruct a round_result-like dict for the lint module.

        The lint module wants per-episode run_dir pointers (to read step 0
        actions) plus the round-level aggregate. We synthesize this from
        the IDCReflectionInput fields the runner already populates.
        """
        agg = inp.aggregate or {}
        episodes = []
        for ep in inp.episodes or []:
            # IDCEpisodeTrace.trace_dir is round_dir/episodes/<ep>/reflection_trace
            # The lint module wants the episode dir (parent of trace_dir).
            ep_dir = Path(ep.trace_dir).parent if ep.trace_dir else None
            episodes.append({
                "episode_id": ep.episode_id,
                "run_dir": str(ep_dir) if ep_dir else None,
                "score": ep.score,
            })
        return {
            "round_idx": inp.round_idx,
            "mean_score": agg.get("mean_score"),
            "scores": agg.get("scores"),
            "n": agg.get("n"),
            "episodes": episodes,
        }

    @staticmethod
    def _render_regression_guard(
        signal: dict[str, Any], round_idx: int
    ) -> str:
        cur = signal.get("current_mean_score")
        best = signal.get("best_mean_score")
        best_round = signal.get("best_round")
        thr = signal.get("threshold")
        return (
            f"# Regression Guard — Round {round_idx}\n\n"
            f"Mean score regressed from {best:.4f} (round {best_round}) to "
            f"{cur:.4f}, which is below {int((thr or 0.7)*100)}% of the "
            f"historical best.\n\n"
            "Runner action taken:\n"
            "- `skill_in.md` has been REPLACED with the content of "
            "`best_skill.md` (the original is preserved at "
            "`skill_in_pre_guard.md` for audit only — DO NOT base your "
            "new memo on it).\n\n"
            "Rules for this round's reflection:\n"
            "1. Your starting point is best_skill, not the failed skill.\n"
            "2. You may ONLY DELETE bullets, not add prescriptive ones. "
            "K=K samples from one regressed round are below the noise "
            "floor and cannot justify new heuristics.\n"
            "3. If you cannot point to direct evidence in THIS round's "
            "traces that a specific best_skill bullet caused harm, do "
            "NOT delete it.\n"
            "4. submit_diagnosis must list ALL bullets you delete and "
            "tie each to specific episode evidence (ep_id + step + "
            "score_delta).\n"
        )

    # ── helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _require_round_dir(inp: IDCReflectionInput) -> Path:
        if inp.round_dir is None:
            raise ValueError(
                "AgenticIDCReflector.reflect: IDCReflectionInput.round_dir "
                "is required. The runner must pass the per-round directory "
                "(e.g. <run_dir>/round_03)."
            )
        round_dir = Path(inp.round_dir)
        if not round_dir.exists() or not round_dir.is_dir():
            raise ValueError(
                f"AgenticIDCReflector.reflect: round_dir does not exist "
                f"or is not a directory: {round_dir}"
            )
        return round_dir

    @staticmethod
    def _stage_context_files(round_dir: Path, inp: IDCReflectionInput) -> None:
        context = {
            "round_idx": inp.round_idx,
            "game": inp.game_name,
            **(inp.aggregate or {}),
        }
        atomic_write_json(round_dir / "idc_context.json", context)
        # best_skill is empty when no earlier measured round exists; still
        # write the file (empty) so list_dir surfaces it consistently.
        atomic_write_text(round_dir / "best_skill.md", inp.best_skill or "")
        # Persistent observation log carried from earlier rounds. Empty
        # on round 0 / when no earlier round wrote observations. Stage it
        # even when empty so list_dir surfaces it and the reflector
        # understands it CAN be updated this round.
        atomic_write_text(
            round_dir / "notebook_so_far.md", inp.notebook_so_far or ""
        )

    @staticmethod
    def _load_prompt(rel_path: str, *, optional: bool = False) -> str:
        path = PROMPT_ROOT / rel_path
        if not path.exists():
            if optional:
                return ""
            raise FileNotFoundError(f"Missing IDC prompt: {path}")
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _last_text_turn(messages: list[dict]) -> str:
        for msg in reversed(messages or []):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                joined = "\n\n".join(t for t in texts if t)
                if joined:
                    return joined
            elif isinstance(content, str) and content.strip():
                return content
        return ""


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
