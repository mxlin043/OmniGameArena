"""Unified VLM agent.

One class drives every general VLM (Claude, Gemini, GPT, Qwen-VL, ...).
The three orthogonal axes that used to be tangled together:

  1. **Backend**       - how the HTTP request is made (commercial gateway
     vs self-host OpenAI-compatible). Selected by ``omni_game_arena.models.backends.pick_backend``.
  2. **MethodStyle**   - output format the model produces (lumine chunked
     text).
     Selected by ``omni_game_arena.prompts.methods.get_method``.
  3. **Adapter**       - how the parsed action is sent to UE5. Lives outside
     the agent (paired 1:1 with a MethodStyle).

History layout
--------------
Two layouts are supported, chosen by ``MethodStyle.history_layout``:

  - ``"turns"``  : each past frame becomes its own user/assistant pair.
  - ``"packed"`` : all past frames + a compact action log live in one user
                   turn. Token-efficient for chunked methods (lumine).
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque

from PIL import Image
from omni_game_arena.clock import require_server_inference_seconds

from omni_game_arena.prompts.composer import compose_vlm_system
from omni_game_arena.prompts.methods import MethodStyle, get_method

from .backends import Backend, pick_backend
from .base import BaseAgent

logger = logging.getLogger(__name__)

_TERMINAL_NOOP_RE = re.compile(
    r"\b("
    r"game\s+over|"
    r"game\s+is\s+over|"
    r"episode\s+(?:has\s+)?ended|"
    r"ended\s+in\s+failure|"
    r"no\s+further\s+(?:movement|action)s?\s+is\s+possible"
    r")\b",
    re.IGNORECASE,
)


class EmptyModelResponseError(RuntimeError):
    """Raised when a VLM returns empty text after repeated sends."""

    def __init__(self, model: str, attempts: int):
        self.model = model
        self.attempts = attempts
        super().__init__(
            f"Model {model!r} returned an empty response after {attempts} attempts"
        )


class ModelRequestError(RuntimeError):
    """A backend exhausted its request retries, rather than returning empty text."""

    def __init__(self, model: str, error: Exception):
        self.model = model
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        self.request_failure = {
            "model": model,
            "error_type": type(error).__name__,
            "http_status": status if isinstance(status, int) and not isinstance(status, bool) else None,
        }
        super().__init__(
            f"Model {model!r} request failed after backend retries: "
            f"{type(error).__name__}: {error}"
        )


class ActionFormatError(RuntimeError):
    """An opted-in strict action response stayed malformed after correction."""


class VLMAgent(BaseAgent):
    """General VLM agent. Combines a Backend + a MethodStyle.

    Args:
        model: Model identifier (e.g. ``"claude-opus-4-6"``, ``"qwen3.5-vl-72b"``).
        method: MethodStyle name or instance (``"lumine"``).
        base_url / api_key: Forwarded to OpenAI-compatible VLM endpoints.
        resize_size: Max image edge in px (longest side; aspect ratio preserved).
            ``<=0`` keeps native resolution.
        temperature: Decoding temperature; ``None`` skips the field.
        history_len: Number of past (image, action_text) pairs to include.
        history_reasoning_len: Number of most-recent packed-history actions
            whose reasoning text should be kept. 0 keeps only compact action
            chunks.
        with_visual_input: Whether screenshots may be sent to the model.
            False removes current, history, and recap image blocks for a
            strict text-only / no-frame ablation.
        game: Game name passed to ``load_game_prompt``. ``None`` = no map prompt.
        include_history_actions: For ``packed`` layout, whether to include the
            action chunks alongside past frames.
    """

    def __init__(
        self,
        model: str,
        method: str | MethodStyle = "lumine",
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        request_model: str | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        resize_size: int = 512,
        temperature: float | None = 0.3,
        request_timeout: float | None = None,
        max_retries: int | None = None,
        history_len: int = 5,
        history_reasoning_len: int = 0,
        with_visual_input: bool = True,
        game: str | None = None,
        with_game_prompt: bool = True,
        with_controls_prompt: bool = True,
        with_skill_prompt: bool = False,
        with_output_format_prompt: bool = True,
        include_history_actions: bool = True,
        empty_response_max_attempts: int = 5,
        action_format_guard: bool = False,
    ):
        self.model = model
        self.method: MethodStyle = (
            method if isinstance(method, MethodStyle) else get_method(method)
        )
        self.backend: Backend = pick_backend(
            model,
            base_url=base_url,
            api_key=api_key,
            request_model=request_model,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            resize=True,
            resize_size=resize_size,
            temperature=temperature,
            request_timeout=request_timeout,
            max_retries=max_retries,
        )
        self.history_len = history_len
        self.history_reasoning_len = max(0, int(history_reasoning_len or 0))
        self.with_visual_input = bool(with_visual_input)
        self.game = game
        self.with_game_prompt = bool(with_game_prompt)
        self.with_controls_prompt = bool(with_controls_prompt)
        self.with_skill_prompt = bool(with_skill_prompt)
        self.with_output_format_prompt = bool(with_output_format_prompt)
        self.include_history_actions = include_history_actions
        self.empty_response_max_attempts = max(
            1,
            int(empty_response_max_attempts or 1),
        )
        self.action_format_guard = bool(action_format_guard)
        self.last_action_metadata = None
        self._format_guard_instruction = ""

        # Per-episode state.
        # In text-only mode we retain only prior action text, never frame
        # objects. This keeps the internal state aligned with the no-visual
        # request invariant as well as avoiding unnecessary image retention.
        self._history: deque[tuple[Image.Image | None, str]] = deque(
            maxlen=history_len,
        )
        self.last_vlm_response = ""
        self.last_decision_latency_s: float | None = None
        self.last_decision_latency_source: str | None = None
        self.last_latency_details: dict = {}
        self._inference_calls: list[dict] = []
        # Cross-episode evolution scripts inject lessons here; appended to
        # the per-turn user text on every call.
        self.experience: str = ""
        # Prompt skills injected by benchmark configs. These belong in the
        # system prompt so they behave like durable gameplay instructions
        # rather than low-priority per-turn notes.
        self.system_experience: str = ""
        # Prior Trajectory Recall - optional multimodal memory blocks
        # injected ahead of the current frame on every turn. External
        # callers may populate these fields; they are ignored when None or
        # empty. Each block follows the backend's native content schema.
        # MODE: "stuffed" - packed into a single leading position of current
        # user message. Activated when ``recap_blocks`` is non-None.
        self.recap_blocks: list[dict] | None = None
        # MODE: "conversational" - prior episode reconstructed as a list of
        # natural user/assistant turn pairs, inserted right after the system
        # message and before the in-episode history + current turn. Mimics
        # what Claude itself sees when "remembering" across CLI sessions
        # (no [Previous attempt] header, no outcome metadata, no third-person
        # framing). Activated when ``recap_messages`` is non-None.
        self.recap_messages: list[dict] | None = None
        # Neutral transition phrase prepended to the current-turn user text
        # ONLY on the first turn after a conversational recap (i.e. when the
        # in-episode history is empty). Subsequent turns within the same
        # episode rely on the natural visual change between prior trajectory
        # and new frames to establish "the game reset" - the model handles
        # this without further explicit cuing.
        self.recap_transition_text: str = (
            "Let's run another attempt. The game has been reset."
        )

    # -- BaseAgent --------------------------------------------------------
    def act(self, obs: dict, task: str, action_schema: dict) -> dict:
        self.last_action_metadata = None
        self.last_decision_latency_s = None
        self.last_decision_latency_source = None
        self.last_latency_details = {}
        self._inference_calls = []
        if self.action_format_guard:
            if action_schema.get("format") != "lumine_chunked" or action_schema.get("mouse_axes"):
                raise ValueError("action_format_guard currently supports keyboard-only Lumine")
            chunk = action_schema["chunk_steps"]
            self._format_guard_instruction = (
                f"Output exactly {chunk} time slots separated by {chunk - 1} semicolons. "
                "Each key appears at most once in a slot. Holding a key across slots is valid. "
                "Close the single action block with <|action_end|> and stop."
            )
            # Preserve every observation, but do not teach the model to copy
            # an invalid response from a resumed or externally supplied history.
            self._history = deque(
                ((frame, text if not self._action_format_issues(text, action_schema)
                  else "[Previous action text omitted because its format was invalid.]")
                 for frame, text in self._history), maxlen=self.history_len,
            )
        if self.with_visual_input:
            image: Image.Image | None = obs.get("image")
            if image is None:
                raise ValueError(
                    "VLMAgent requires obs['image'] when with_visual_input=true"
                )
        else:
            # Do not even read the environment frame in the text-only path.
            image = None
        system_prompt = compose_vlm_system(
            self.method,
            action_schema,
            self.game,
            prompt_skill=self.system_experience,
            with_game_prompt=self.with_game_prompt,
            with_controls_prompt=self.with_controls_prompt,
            with_skill_prompt=self.with_skill_prompt,
            with_output_format_prompt=self.with_output_format_prompt,
        )
        if not self.with_visual_input:
            system_prompt += (
                "\n\nText-only ablation: No screenshots or other visual "
                "observations are provided. Choose actions using only these "
                "textual game instructions and prior textual action history."
            )

        layout = getattr(self.method, "history_layout", "turns")
        # Conversational Replay forces turn layout regardless of method's
        # default (which is typically "packed" for Lumine). Mixing
        # turn-based prior recap with packed in-episode history would
        # confuse the model about what's prior vs current.
        if self.recap_messages:
            layout = "turns"
        if layout == "packed":
            messages = self._build_packed_messages(image, system_prompt)
        else:
            messages = self._build_turn_messages(image, system_prompt)
        if not self.with_visual_input:
            # Defense in depth: builders avoid ordinary game frames below,
            # while this scrubber also catches optional PTR/recap images.
            messages = self._without_visual_blocks(messages)

        self._log_messages(messages, layout)

        response = ""
        for attempt in range(1, self.empty_response_max_attempts + 1):
            raw_response = self.backend.chat(messages) or ""
            response = raw_response.strip()
            request_error = getattr(self.backend, "last_request_error", None)
            if not response and request_error is not None:
                self.last_vlm_response = f"(request failed: {type(request_error).__name__})"
                raise ModelRequestError(self.model, request_error) from request_error
            self._capture_response_latency()
            logger.debug(
                "Raw VLM response (attempt %d/%d): %s",
                attempt,
                self.empty_response_max_attempts,
                response[:500] if response else "(empty)",
            )
            if response:
                if attempt > 1:
                    logger.info(
                        "Recovered from empty VLM response after %d attempts",
                        attempt,
                    )
                break
            self.last_vlm_response = "(empty response)"
            if attempt < self.empty_response_max_attempts:
                logger.warning(
                    "Empty response from model (attempt %d/%d); retrying",
                    attempt,
                    self.empty_response_max_attempts,
                )
            else:
                logger.warning(
                    "Empty response from model after %d attempts; skipping episode",
                    self.empty_response_max_attempts,
                )
                raise EmptyModelResponseError(
                    self.model,
                    self.empty_response_max_attempts,
                )

        if self.action_format_guard:
            response = self._ensure_action_format(response, messages, action_schema)

        # Always expose the raw response (even on failure) so the runner
        # records it in summary.json - otherwise debugging "all zero
        # score" runs blind because the field stays at the previous
        # turn's value (or the empty initial value).
        self.last_vlm_response = response

        action = self.method.parse(response, action_schema=action_schema)

        # Treat semantically-empty parsed actions the same as a parse
        # failure: surface the raw response in the log and emit a real
        # no-op rather than silently letting the agent walk in place.
        from omni_game_arena.prompts.methods.lumine import is_empty_action

        if not action or is_empty_action(action):
            reason = "empty parsed action" if action else "failed to parse"
            logger.warning(
                "%s; raw response (first 500 chars): %s",
                reason,
                response[:500].replace("\n", " \\n "),
            )
            noop = self.method.noop_action(action_schema)
            if _looks_like_terminal_noop(response):
                logger.info("Terminal no-op detected from VLM response")
                noop["done"] = True
            return noop

        logger.info("Action: %s", _short_action_log(action))

        self._history.append((
            image if self.with_visual_input else None,
            self.last_vlm_response,
        ))
        return action

    def reset_history(self) -> None:
        self._history.clear()
        self.last_action_metadata = None

    def _requires_inference_timing(self) -> bool:
        return bool(getattr(self.backend, "lcm_timing_enabled", False))

    def _capture_response_latency(self) -> None:
        value = getattr(self.backend, "last_decision_latency_s", None)
        source = getattr(self.backend, "last_decision_latency_source", None)
        details = getattr(self.backend, "last_latency_details", None) or {}
        if self._requires_inference_timing():
            # Empty-response and format-correction calls also consume inference.
            # Missing timing on any one of them invalidates the entire decision.
            seconds = require_server_inference_seconds(value, source)
            self._inference_calls.append({"seconds": seconds, "source": source, "details": details})
            self.last_decision_latency_s = sum(call["seconds"] for call in self._inference_calls)
            self.last_decision_latency_source = "sum.server_inference"
            self.last_latency_details = {"inference_calls": list(self._inference_calls)}
        else:
            self.last_decision_latency_s = value
            self.last_decision_latency_source = source
            self.last_latency_details = details

    @staticmethod
    def _action_format_issues(response: str, schema: dict) -> list[str]:
        matches = re.findall(r"<\|action_start\|>(.*?)<\|action_end\|>", response or "", re.S)
        if len(matches) != 1 or not (response or "").rstrip().endswith("<|action_end|>"):
            return ["missing or multiple action blocks"]
        groups = [group.split() for group in matches[0].split(";")]
        issues = []
        if len(groups) != schema["chunk_steps"]:
            issues.append("wrong number of slots")
        if any(len(group) != len(set(group)) for group in groups):
            issues.append("repeated key within a slot")
        valid = set(schema.get("valid_keys") or ())
        if any(len(group) > 4 or (valid and any(key not in valid for key in group)) for group in groups):
            issues.append("invalid keys")
        return issues

    def _ensure_action_format(self, response: str, messages: list[dict], schema: dict) -> str:
        rejected = []
        total_latency = self.last_decision_latency_s or 0.0
        for attempt in range(3):
            issues = self._action_format_issues(response, schema)
            if not issues:
                self.last_action_metadata = {"action_format_guard": {"attempts": attempt + 1, "rejected": rejected}}
                if not self._requires_inference_timing():
                    self.last_decision_latency_s = total_latency or self.last_decision_latency_s
                return response
            rejected.append({"response": response, "issues": issues})
            logger.warning("Action format rejected (attempt %d/3): %s", attempt + 1, ", ".join(issues))
            if attempt == 2 or not self.with_output_format_prompt:
                self.last_vlm_response = response
                self.last_action_metadata = {"action_format_guard": {"attempts": attempt + 1, "rejected": rejected}}
                raise ActionFormatError(f"Model {self.model!r} produced invalid actions after {attempt + 1} attempts")
            # Resend the same observations with a format correction. The bad
            # response itself is never added as an assistant demonstration.
            retry_messages = [dict(message) for message in messages]
            content = retry_messages[-1]["content"]
            correction = "Previous output rejected: " + ", ".join(issues) + ". " + self._format_guard_instruction
            retry_messages[-1]["content"] = (
                [*content, {"type": "text", "text": correction}]
                if isinstance(content, list) else str(content) + "\n" + correction
            )
            response = (self.backend.chat(retry_messages) or "").strip()
            error = getattr(self.backend, "last_request_error", None)
            if not response and error is not None:
                raise ModelRequestError(self.model, error) from error
            self._capture_response_latency()
            if not self._requires_inference_timing():
                total_latency += self.last_decision_latency_s or 0.0
        raise AssertionError("unreachable")

    # -- Layout: alternating user/assistant pairs -------------------------
    def _build_turn_messages(
        self,
        image: Image.Image | None,
        system: str,
    ) -> list[dict]:
        per_turn = self._per_turn_user_text()
        messages: list[dict] = [{"role": "system", "content": system}] if system else []

        # Conversational Replay - splice prior episode's user/assistant
        # pairs immediately after the system prompt. The model treats them
        # as its own past chat history.
        if self.recap_messages:
            messages.extend(self.recap_messages)

        for past_image, past_action in self._history:
            past_content: list = []
            if self.with_visual_input:
                assert past_image is not None
                past_content.append(self.backend.make_image_content(past_image))
            past_content.append({"type": "text", "text": per_turn})
            messages.append({
                "role": "user",
                "content": past_content,
            })
            messages.append({"role": "assistant", "content": past_action})
        # Current turn - append experience tail / transition prefix if any.
        current_text = per_turn
        # Neutral transition only on the FIRST turn after a conversational
        # recap (when the in-episode history is still empty). Subsequent
        # turns within this episode no longer need it - the visual reset is
        # already encoded in the in-episode history pair the agent built up.
        if self.recap_messages and len(self._history) == 0:
            current_text = self.recap_transition_text + "\n\n" + current_text
        if self.experience:
            current_text += f"\n\n## Lessons from Previous Attempts\n{self.experience}"
        # Stuffed PTR (legacy mode) - recap blocks prepended to current user
        # message content. Mutually exclusive with conversational replay.
        current_content: list = []
        if self.recap_blocks:
            current_content.extend(self.recap_blocks)
        if self.with_visual_input:
            assert image is not None
            current_content.append(self.backend.make_image_content(image))
        current_content.append({"type": "text", "text": current_text})
        messages.append({"role": "user", "content": current_content})
        return messages

    # -- Layout: all frames + action log in one user turn -----------------
    def _build_packed_messages(
        self,
        image: Image.Image | None,
        system: str,
    ) -> list[dict]:
        past = list(self._history)
        n_past = len(past)

        content: list = []
        # PTR recap goes first so the model contextualises history + current
        # against the prior episode it carries forward.
        if self.recap_blocks:
            content.extend(self.recap_blocks)
        if self.with_visual_input:
            for frame, _ in past:
                assert frame is not None
                content.append(self.backend.make_image_content(frame))
            assert image is not None
            content.append(self.backend.make_image_content(image))

        if n_past > 0 and self.include_history_actions:
            lines = []
            for offset, (_, action_text) in enumerate(past, start=1):
                idx = n_past - offset + 1  # oldest=N, newest=1
                compact = self._format_history_action(
                    action_text,
                    include_reasoning=idx <= self.history_reasoning_len,
                )
                lines.append(f"[action t-{idx}]: {compact}")
            content.append({
                "type": "text",
                "text": "**History actions**\n" + "\n".join(lines),
            })

        if not self.with_visual_input:
            if n_past > 0 and self.include_history_actions:
                turn_text = (
                    "No game frame is provided in this text-only ablation. "
                    "Use the textual game instructions and history actions "
                    "above to choose the next actions."
                )
            else:
                turn_text = self._per_turn_user_text()
        elif n_past == 0:
            turn_text = self.method.per_turn_user_text()
        elif self.include_history_actions:
            turn_text = (
                f"The {n_past + 1} images above are consecutive game frames "
                f"in chronological order; the last one is the current frame. "
                f"The history actions listed below the frames are the action "
                f"chunks you emitted at each past frame. Plan your next actions."
            )
        else:
            turn_text = (
                f"The {n_past + 1} images above are consecutive game frames "
                "in chronological order (the last one is the current frame). "
                "Plan your next actions."
            )

        if self.experience:
            turn_text += f"\n\n## Lessons from Previous Attempts\n{self.experience}"
        content.append({"type": "text", "text": turn_text})

        messages = [{"role": "system", "content": system}] if system else []
        return messages + [{"role": "user", "content": content}]

    def _per_turn_user_text(self) -> str:
        if self.with_visual_input:
            text = self.method.per_turn_user_text()
            if self.action_format_guard and self.with_output_format_prompt:
                text += "\n" + self._format_guard_instruction
            return text
        return (
            "No game frame is provided in this text-only ablation. "
            "Choose the next actions using only the textual game instructions "
            "and prior textual action history."
        )

    @classmethod
    def _without_visual_blocks(cls, messages: list[dict]) -> list[dict]:
        """Return message copies with every supported image block removed.

        Normal game frames are skipped before encoding. This final pass keeps
        ``with_visual_input=False`` strict even when callers inject multimodal
        PTR recap content using Anthropic, OpenAI, or Responses-style blocks.
        """
        cleaned: list[dict] = []
        for message in messages:
            copy = dict(message)
            content = cls._without_visual_content(message.get("content"))
            if content == []:
                # Anthropic rejects empty message content. Preserve turn
                # structure without leaking anything about the omitted image.
                content = [{
                    "type": "text",
                    "text": "[Visual content omitted for text-only ablation.]",
                }]
            copy["content"] = content
            cleaned.append(copy)
        return cleaned

    @classmethod
    def _without_visual_content(cls, content):
        if not isinstance(content, list):
            return content

        cleaned = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in {
                "image",
                "image_url",
                "input_image",
            }:
                continue
            if isinstance(block, dict) and isinstance(block.get("content"), list):
                block = dict(block)
                nested = cls._without_visual_content(block["content"])
                if nested == []:
                    nested = [{
                        "type": "text",
                        "text": "[Visual content omitted for text-only ablation.]",
                    }]
                block["content"] = nested
            cleaned.append(block)
        return cleaned

    # -- Debug logging ----------------------------------------------------
    def _format_history_action(
        self,
        raw_response: str,
        *,
        include_reasoning: bool,
    ) -> str:
        if include_reasoning:
            return (raw_response or "").strip()
        return self.method.compact_history_action(raw_response)

    def _log_messages(self, messages: list[dict], layout: str) -> None:
        if not logger.isEnabledFor(logging.INFO):
            return
        logger.info("=" * 60)
        logger.info(
            "[VLM] model=%s method=%s layout=%s history=%d",
            self.model, self.method.name, layout, len(self._history),
        )
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                logger.info("[%s] %s", role, content[:500])
                continue
            n_images = sum(
                1 for c in content
                if isinstance(c, dict) and c.get("type") in ("image_url", "image")
            )
            text_parts = [
                c["text"] for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            logger.info(
                "[%s] %s%s",
                role,
                " ".join(text_parts)[:500],
                f" [+{n_images} images]" if n_images else "",
            )
        logger.info("=" * 60)


def _short_action_log(action: dict) -> str:
    """Compact one-line representation of an action dict for logging."""
    if "steps" in action:
        return f"mouse={action.get('mouse')} steps={action['steps']}"
    return json.dumps(action, ensure_ascii=False)[:200]


def _looks_like_terminal_noop(response: str) -> bool:
    text = (response or "").lower()
    if "not game over" in text or "avoid game over" in text:
        return False
    return bool(_TERMINAL_NOOP_RE.search(response or ""))
