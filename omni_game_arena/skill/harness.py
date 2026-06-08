"""Analyzer agent loop.

Drives a multi-turn ``chat_with_tools`` conversation against one
round's output directory. The agent decides - through ``list_dir`` /
``read_text`` / ``read_image`` / ``grep`` / ``get_previous_skill`` -
what to look at, then calls ``submit_skill`` with the memo.

Trace audit
-----------
Every tool call (name, input args, brief result summary) is appended
to ``self.trace``. The orchestrator writes this out as
``analysis_trace.jsonl`` so we can see *what the analyzer actually
looked at* without re-reading the full transcript.

Safety valves
-------------
- ``max_iterations``: hard cap on tool-use rounds. On exhaustion the
  harness sends one final user turn forbidding further tools and asks
  for a memo. If that also fails we fall back to the previous skill.
- Image tool results are wrapped via the backend's
  ``make_image_content`` so the analyzer sees them in the correct
  multimodal shape.
- Bad input (missing required arg / unknown tool / sandbox refusal)
  becomes a tool_result with ``is_error: true``; the agent gets to
  recover instead of crashing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .parser import extract_skill
from .prompts import ANALYZER_SYSTEM_PROMPT, TOOL_SPECS, build_seed_message
from .sandbox import RoundReadOnlyFS

log = logging.getLogger(__name__)


class AnalyzerHarness:
    """Run one analyzer pass over one round's output directory."""

    def __init__(
        self,
        *,
        backend,
        round_dir: str | Path,
        game: str,
        round_idx: int,
        n_episodes: int,
        previous_skill: str | None = None,
        max_iterations: int = 100,
        max_text_chars_per_result: int = 80_000,
        system_prompt: str | None = None,
        seed_message: str | None = None,
        tool_specs: list[dict] | None = None,
        validate_submission=None,
        max_post_submit_retries: int = 2,
        require_diagnosis_before_skill: bool = False,
        soft_tools: tuple[str, ...] | None = None,
        tool_handlers: dict | None = None,
    ):
        """Run one analyzer pass.

        ``system_prompt`` / ``seed_message`` / ``tool_specs`` allow callers
        (e.g. IDC's agentic reflector) to swap in a domain-specific prompt
        and seed turn while keeping the same multi-turn tool-use loop.
        Defaults fall back to the base analyzer prompt set.

        ``validate_submission`` is an optional callable
        ``(submission_input: dict) -> str | None`` called when the agent
        invokes ``submit_skill``. Returning ``None`` accepts the
        submission and ends the loop; returning a string sends that
        message back as a ``tool_result`` (``is_error: true``) so the
        agent can correct and resubmit. Capped by
        ``max_post_submit_retries`` to keep latency finite.

        ``require_diagnosis_before_skill``: when ``True``, the harness
        rejects ``submit_skill`` calls made before any ``submit_diagnosis``
        call has been recorded this run. Forces explicit diagnosis-first
        workflow.

        ``soft_tools``: tool names that should be dispatched by the
        harness directly (not via the sandbox), captured into
        ``self.last_soft_calls[name]``, and ack'd. Currently used for
        ``submit_diagnosis``. They do NOT terminate the loop.

        ``tool_handlers``: optional ``{name: callable(input_dict,
        harness) -> str}`` mapping. Each handler is invoked when the
        agent calls that tool; the returned string becomes the
        ``tool_result`` content. Unlike soft_tools (which just ack and
        record), tool_handlers can do real work - e.g. call another LLM
        for the IDC validator. The harness still records each call in
        ``self.last_soft_calls[name]`` so callers can enforce a budget
        from inside the handler.
        """
        self.backend = backend
        self.round_dir = Path(round_dir)
        self.fs = RoundReadOnlyFS(round_dir, previous_skill=previous_skill)
        self.game = game
        self.round_idx = round_idx
        self.n_episodes = n_episodes
        self.previous_skill = previous_skill
        self.max_iterations = max_iterations
        self.max_text_chars_per_result = max_text_chars_per_result
        self.system_prompt = system_prompt
        self.seed_message = seed_message
        self.tool_specs = tool_specs

        self.validate_submission = validate_submission
        self.max_post_submit_retries = max(0, int(max_post_submit_retries))
        self.require_diagnosis_before_skill = bool(require_diagnosis_before_skill)
        self.soft_tools = tuple(soft_tools or ())
        self.tool_handlers = dict(tool_handlers or {})

        self.trace: list[dict] = []
        # ``messages`` exposes the full transcript for audit / debugging.
        # Cleared and rebuilt on each call to ``run()``.
        self.messages: list[dict] = []
        # ``last_submission`` holds the FULL input dict of the submit_skill
        # tool call that ended the loop. ``run()`` returns the memo string
        # for simple callers, while IDC reads extra submit_skill fields
        # (e.g. ``notebook``) from this dict after run().
        self.last_submission: dict | None = None
        # ``last_soft_calls`` accumulates {tool_name: list[input_dict]} for
        # non-terminating tools like submit_diagnosis. Caller reads these
        # after run() returns.
        self.last_soft_calls: dict[str, list[dict]] = {}
        # Internal counters.
        self._submit_retries_left = self.max_post_submit_retries

    # -- public entry point ---------------------------------------------
    def run(self) -> tuple[str | None, list[dict], list[dict]]:
        """Drive the agent loop. Returns ``(memo, trace, messages)``.

        ``memo`` is ``None`` only if the analyzer fails to produce any
        usable text after all retries; orchestrator should fall back to
        the previous round's skill in that case.
        """
        self.trace = []
        seed = self.seed_message if self.seed_message is not None else build_seed_message(
            game=self.game,
            round_idx=self.round_idx,
            n_episodes=self.n_episodes,
            has_previous_skill=self.previous_skill is not None,
        )
        self.messages = [{"role": "user", "content": seed}]
        system_text = self.system_prompt if self.system_prompt is not None else ANALYZER_SYSTEM_PROMPT
        tools = self.tool_specs if self.tool_specs is not None else TOOL_SPECS

        for iteration in range(self.max_iterations):
            body = self.backend.chat_with_tools(
                messages=self.messages,
                tools=tools,
                system=system_text,
            )
            if not body:
                log.error(
                    "Analyzer chat_with_tools returned empty (iter=%d)", iteration
                )
                self.trace.append({
                    "iteration": iteration, "event": "empty_response"
                })
                return None, self.trace, self.messages

            content = body.get("content") or []
            stop_reason = body.get("stop_reason")

            # Record the assistant turn verbatim into messages so the
            # next turn's API call carries the full tool_use / tool_result
            # chain.
            self.messages.append({"role": "assistant", "content": content})

            # Inspect blocks for submit_skill / other tool calls / final text.
            tool_uses = [b for b in content if isinstance(b, dict)
                         and b.get("type") == "tool_use"]
            text_blocks = [b for b in content if isinstance(b, dict)
                           and b.get("type") == "text"]

            # Look for submit_skill first - if present, accept the memo
            # immediately and don't bother dispatching other tools in
            # the same turn.
            submit_tu = next(
                (tu for tu in tool_uses if tu.get("name") == "submit_skill"),
                None,
            )
            if submit_tu is not None:
                tu = submit_tu
                submission = dict(tu.get("input") or {})
                memo = (submission.get("memo") or "").strip()

                # Check 1: diagnosis-first rule.
                diagnosis_blocker: str | None = None
                if (
                    self.require_diagnosis_before_skill
                    and not self.last_soft_calls.get("submit_diagnosis")
                ):
                    diagnosis_blocker = (
                        "submit_skill REJECTED: you must call "
                        "submit_diagnosis FIRST in this run to enumerate "
                        "what you intend to delete and add. Make that "
                        "call, then call submit_skill again."
                    )

                # Check 2: empty memo.
                if memo and diagnosis_blocker is None:
                    # Check 3: external post-submit validator (banned
                    # phrases / bullet budget / unchanged-skill).
                    rejection: str | None = None
                    if self.validate_submission is not None:
                        try:
                            rejection = self.validate_submission(submission)
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "validate_submission raised %s; accepting "
                                "submission to avoid stalling.", exc,
                            )
                            rejection = None
                    if rejection is None:
                        # Accept and end loop.
                        self.trace.append({
                            "iteration": iteration,
                            "event": "submit_skill",
                            "memo_chars": len(memo),
                            "extra_keys": sorted(
                                k for k in submission.keys() if k != "memo"
                            ),
                        })
                        self.last_submission = submission
                        return memo, self.trace, self.messages
                    # Rejected by validator.
                    if self._submit_retries_left <= 0:
                        log.warning(
                            "submit_skill validator rejected and retries "
                            "exhausted; accepting submission with warning."
                        )
                        self.trace.append({
                            "iteration": iteration,
                            "event": "submit_skill",
                            "memo_chars": len(memo),
                            "extra_keys": sorted(
                                k for k in submission.keys() if k != "memo"
                            ),
                            "validator_warning": "accepted after retries exhausted",
                            "last_rejection": rejection[:500],
                        })
                        self.last_submission = submission
                        return memo, self.trace, self.messages
                    self._submit_retries_left -= 1
                    reject_msg = rejection
                    self.trace.append({
                        "iteration": iteration,
                        "event": "submit_skill_rejected",
                        "retries_left": self._submit_retries_left,
                        "rejection_summary": rejection.splitlines()[0]
                            if rejection else "",
                    })
                elif diagnosis_blocker is not None:
                    reject_msg = diagnosis_blocker
                    self.trace.append({
                        "iteration": iteration,
                        "event": "submit_skill_blocked_no_diagnosis",
                    })
                else:
                    # Empty memo.
                    log.warning(
                        "submit_skill called with empty memo; "
                        "asking for retry"
                    )
                    reject_msg = (
                        "Empty memo. Please call submit_skill again with "
                        "a real tactical memo."
                    )
                    self.trace.append({
                        "iteration": iteration,
                        "event": "submit_skill_empty",
                    })

                # Build tool_result for the rejected submit_skill, plus
                # ack-style results for any other tool_uses in the same
                # turn (API requires every tool_use have a matching result).
                rejection_blocks: list[dict] = [{
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": reject_msg,
                    "is_error": True,
                }]
                for other in tool_uses:
                    if other is tu:
                        continue
                    rejection_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": other["id"],
                        "content": "(skipped - submit_skill was attempted)",
                    })
                self.messages.append({
                    "role": "user", "content": rejection_blocks,
                })
                # next iteration
            else:
                # No submit_skill in this turn. Two possibilities:
                # (a) the agent called other tools - dispatch them.
                # (b) the agent only wrote text - try to fish a
                #     <|skill_start|> block, else nudge it.
                if tool_uses:
                    tool_result_blocks: list[dict] = []
                    for tu in tool_uses:
                        result_blocks = self._dispatch(tu)
                        tool_result_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": result_blocks,
                        })
                    self.messages.append({
                        "role": "user", "content": tool_result_blocks,
                    })
                else:
                    # Pure-text turn. Check for a skill block fallback.
                    text = "\n\n".join(b.get("text", "") for b in text_blocks)
                    fished = extract_skill(text)
                    if fished:
                        self.trace.append({
                            "iteration": iteration,
                            "event": "skill_extracted_from_text",
                            "memo_chars": len(fished),
                        })
                        return fished, self.trace, self.messages
                    # Else: nudge it. Up to a couple of nudges before we
                    # bail out via the iteration cap.
                    self.trace.append({
                        "iteration": iteration,
                        "event": "no_tools_no_skill_nudging",
                        "stop_reason": stop_reason,
                    })
                    self.messages.append({
                        "role": "user",
                        "content": (
                            "You haven't submitted a memo yet. Call "
                            "submit_skill now with your final memo, or "
                            "run more tools first if you need more "
                            "information."
                        ),
                    })

        # -- max iterations exhausted ------------------------------------
        log.warning(
            "Analyzer hit max_iterations=%d without submit_skill; "
            "forcing finalization",
            self.max_iterations,
        )
        self.trace.append({"event": "max_iterations_exhausted"})
        # One last call WITHOUT tools - tell it to write the memo plainly.
        # ``chat()`` is enough; we don't need a tool spec.
        self.messages.append({
            "role": "user",
            "content": (
                "You've used the maximum number of exploratory tool calls. "
                "Stop exploring. Reply with ONLY the tactical memo text "
                "(no markdown headers, no tool calls). Keep it under 200 words."
            ),
        })
        final_text = self.backend.chat(self.messages)
        if final_text:
            memo = extract_skill(final_text) or final_text.strip()
            if memo:
                self.trace.append({
                    "event": "forced_finalization",
                    "memo_chars": len(memo),
                })
                return memo, self.trace, self.messages
        log.error(
            "Forced finalization also failed; analyzer produced no memo."
        )
        return None, self.trace, self.messages

    # -- tool dispatch --------------------------------------------------
    def _dispatch(self, tool_use: dict) -> list[dict] | str:
        """Run one tool and return the result formatted as Anthropic
        tool_result content (string for text-only, list of blocks for
        multimodal).
        """
        name = tool_use.get("name")
        args = tool_use.get("input") or {}
        trace_entry: dict = {
            "iteration": len(self.trace),
            "event": "tool_call",
            "name": name,
            "input": args,
        }

        try:
            if name == "list_dir":
                result = self.fs.list_dir(args.get("subpath", ""))
                payload = _format_text_payload(result)
            elif name == "read_text":
                if "path" not in args:
                    payload = _error_payload("Missing required arg 'path'.")
                else:
                    result = self.fs.read_text(
                        args["path"],
                        max_chars=args.get("max_chars"),
                    )
                    payload = _format_text_payload(
                        result, max_chars=self.max_text_chars_per_result
                    )
            elif name == "read_image":
                if "path" not in args:
                    payload = _error_payload("Missing required arg 'path'.")
                else:
                    result = self.fs.read_image(args["path"])
                    if "error" in result:
                        payload = _error_payload(result["error"])
                    else:
                        img = result["image"]
                        image_block = self.backend.make_image_content(img)
                        size = result.get("size")
                        text_blk = {
                            "type": "text",
                            "text": (
                                f"Loaded {args['path']} ({size[0]}x{size[1]} "
                                f"after re-encode)."
                            ) if size else f"Loaded {args['path']}.",
                        }
                        payload = [image_block, text_blk]
                        trace_entry["result_summary"] = (
                            f"image {size}" if size else "image"
                        )
            elif name == "grep":
                if "pattern" not in args:
                    payload = _error_payload("Missing required arg 'pattern'.")
                else:
                    result = self.fs.grep(
                        args["pattern"],
                        glob_pattern=args.get("glob_pattern", "**/*.json"),
                        ignore_case=bool(args.get("ignore_case", False)),
                    )
                    payload = _format_text_payload(result)
            elif name == "get_previous_skill":
                result = self.fs.get_previous_skill()
                payload = _format_text_payload(result)
            elif name == "submit_skill":
                # submit_skill is handled in the main loop above, never here.
                payload = _error_payload(
                    "submit_skill handled by harness; this branch is unreachable."
                )
            elif name in self.soft_tools:
                # Soft tool: capture the input, return an ack message.
                # Does NOT terminate the loop. Used for submit_diagnosis.
                self.last_soft_calls.setdefault(name, []).append(dict(args))
                payload = json.dumps(
                    {
                        "ok": True,
                        "tool": name,
                        "n_calls_so_far": len(self.last_soft_calls[name]),
                        "note": (
                            f"{name} accepted. Continue investigating, "
                            "then call submit_skill when ready."
                        ),
                    },
                    ensure_ascii=False,
                )
                trace_entry["result_summary"] = f"{name} accepted"
            elif name in self.tool_handlers:
                # Callable tool handler: invokes external logic (e.g. an
                # LLM judge for validate_skill). Handler returns the
                # tool_result content as a string. Each call is recorded
                # in last_soft_calls so the handler can enforce a per-run
                # budget.
                self.last_soft_calls.setdefault(name, []).append(dict(args))
                handler = self.tool_handlers[name]
                try:
                    payload = handler(dict(args), self)
                except Exception as exc:  # noqa: BLE001
                    payload = _error_payload(
                        f"{name} handler raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                if not isinstance(payload, str):
                    # Handlers return strings (Anthropic tool_result
                    # content is a string in this code path). If a
                    # handler returns something else, JSON-encode it.
                    payload = json.dumps(payload, ensure_ascii=False, default=str)
                trace_entry["result_summary"] = (
                    f"{name} call #{len(self.last_soft_calls[name])}"
                )
            else:
                payload = _error_payload(f"Unknown tool: {name!r}")
        except Exception as e:  # noqa: BLE001
            payload = _error_payload(f"{type(e).__name__}: {e}")
            trace_entry["error"] = str(e)

        if "result_summary" not in trace_entry:
            trace_entry["result_summary"] = _summarize_for_trace(payload)
        self.trace.append(trace_entry)
        return payload


# -- helpers -----------------------------------------------------------
def _format_text_payload(result: dict, *, max_chars: int | None = None) -> str:
    """Serialize a sandbox result dict as compact JSON for tool_result.

    For ``read_text`` the inner ``content`` field can be huge; we
    truncate if it exceeds ``max_chars`` and add a note. (The sandbox
    already has a 1 MB hard cap, but we want a softer cap for the
    analyzer's working budget.)
    """
    if max_chars is not None and isinstance(result.get("content"), str):
        body = result["content"]
        if len(body) > max_chars:
            result = dict(result)
            result["content"] = body[:max_chars]
            result["truncated"] = True
            result["original_chars"] = len(body)
    try:
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as e:
        return json.dumps(
            {"error": f"failed to serialize result: {e}"},
            ensure_ascii=False,
        )


def _error_payload(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _summarize_for_trace(payload) -> str:
    if isinstance(payload, list):
        return f"<{len(payload)} blocks>"
    s = str(payload)
    return s[:200] + ("..." if len(s) > 200 else "")
