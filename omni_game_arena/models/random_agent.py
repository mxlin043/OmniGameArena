"""Reproducible legal-action random baseline.

This agent never constructs a prompt or calls a model backend.  For every
slot in the game's normal action chunk it performs exactly two sampling
stages:

1. sample how many simultaneous keys ``K`` the slot contains;
2. sample one compatible ``K``-key combination from that game's key set.

Opposing movement pairs (W+S and A+D) are excluded.  Games with camera axes
also sample one bounded, discrete mouse rotation per chunk.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Sequence

from omni_game_arena.utils.mouse_calibration import degrees_to_mouse_units

from .base import BaseAgent


_OPPOSING_KEY_PAIRS = (frozenset(("W", "S")), frozenset(("A", "D")))
RANDOM_POLICY_VERSION = 1


class RandomAgent(BaseAgent):
    """Uniform two-stage random policy over compatible chunked actions."""

    def __init__(
        self,
        *,
        model: str = "random-policy",
        valid_keys: Iterable[str],
        mouse_axes: Iterable[str],
        tap_keys: Iterable[str] = (),
        tap_duration: float = 0.0,
        chunk_steps: int,
        seed: int = 0,
        max_actions_per_step: int = 4,
        mouse_x_choices: Sequence[float] = (-45, -20, 0, 20, 45),
        mouse_y_choices: Sequence[float] = (-20, 0, 20),
        mouse_z_choices: Sequence[float] = (-1, 0, 1),
    ):
        self.model = str(model)
        self.valid_keys = tuple(str(key) for key in valid_keys)
        self.mouse_axes = tuple(str(axis) for axis in mouse_axes)
        self.tap_keys = tuple(str(key) for key in tap_keys)
        self.tap_duration = float(tap_duration)
        self.chunk_steps = int(chunk_steps)
        self.seed = int(seed)
        self.max_actions_per_step = int(max_actions_per_step)
        self.mouse_x_choices = self._validated_choices("mouse_x_choices", mouse_x_choices)
        self.mouse_y_choices = self._validated_choices("mouse_y_choices", mouse_y_choices)
        self.mouse_z_choices = self._validated_choices("mouse_z_choices", mouse_z_choices)

        if not self.valid_keys:
            raise ValueError("RandomAgent requires at least one valid key")
        if len(set(self.valid_keys)) != len(self.valid_keys):
            raise ValueError(f"RandomAgent valid_keys contain duplicates: {self.valid_keys}")
        unknown_tap_keys = set(self.tap_keys) - set(self.valid_keys)
        if unknown_tap_keys:
            raise ValueError(
                f"RandomAgent tap_keys are outside valid_keys: {unknown_tap_keys}"
            )
        if self.chunk_steps <= 0:
            raise ValueError("RandomAgent chunk_steps must be positive")
        if self.max_actions_per_step < 0:
            raise ValueError("RandomAgent max_actions_per_step must be >= 0")
        unknown_axes = set(self.mouse_axes) - {"X", "Y", "Z"}
        if unknown_axes:
            raise ValueError(f"RandomAgent received unknown mouse axes: {unknown_axes}")

        self._combination_buckets = self._build_combination_buckets()
        self._available_counts = tuple(sorted(self._combination_buckets))
        self._rng = random.Random(self.seed)
        self.decision_index = 0
        self.last_action_metadata: dict = {}

    @staticmethod
    def _validated_choices(name: str, values: Sequence[float]) -> tuple[float, ...]:
        choices = tuple(float(value) for value in values)
        if not choices:
            raise ValueError(f"RandomAgent {name} must not be empty")
        return choices

    def _build_combination_buckets(self) -> dict[int, tuple[tuple[str, ...], ...]]:
        max_size = min(self.max_actions_per_step, len(self.valid_keys))
        buckets: dict[int, tuple[tuple[str, ...], ...]] = {}
        for size in range(max_size + 1):
            compatible = tuple(
                combo
                for combo in itertools.combinations(self.valid_keys, size)
                if self._is_compatible(combo)
            )
            if compatible:
                buckets[size] = compatible
        return buckets

    @staticmethod
    def _is_compatible(keys: Iterable[str]) -> bool:
        key_set = set(keys)
        return not any(pair <= key_set for pair in _OPPOSING_KEY_PAIRS)

    @property
    def policy_metadata(self) -> dict:
        """Serializable run-level policy definition stored in result.json."""
        return {
            "kind": "random",
            "policy_version": RANDOM_POLICY_VERSION,
            "seed": self.seed,
            "sampling": "two_stage_uniform_compatible_key_combinations",
            "chunk_steps": self.chunk_steps,
            "valid_keys": list(self.valid_keys),
            "tap_keys": list(self.tap_keys),
            "tap_duration": self.tap_duration,
            "possible_action_counts": list(self._available_counts),
            "compatible_combinations_per_count": {
                str(count): len(combinations)
                for count, combinations in self._combination_buckets.items()
            },
            "max_actions_per_step": self.max_actions_per_step,
            "opposing_pairs_excluded": [["W", "S"], ["A", "D"]],
            "mouse_axes": list(self.mouse_axes),
            "mouse_choices": {
                "X_degrees": list(self.mouse_x_choices),
                "Y_degrees": list(self.mouse_y_choices),
                "Z_scroll": list(self.mouse_z_choices),
            },
        }

    def reset(self) -> None:
        """Replay the same deterministic policy trajectory for this seed."""
        self._rng.seed(self.seed)
        self.decision_index = 0
        self.last_action_metadata = {}

    def act(self, obs: dict, task: str, action_schema: dict) -> dict:
        del obs, task  # The random baseline intentionally ignores observations/text.

        schema_keys = tuple(action_schema.get("valid_keys") or ())
        if schema_keys and schema_keys != self.valid_keys:
            raise ValueError(
                "RandomAgent action space drift: "
                f"factory={self.valid_keys}, schema={schema_keys}"
            )
        schema_steps = int(action_schema.get("chunk_steps", self.chunk_steps))
        if schema_steps != self.chunk_steps:
            raise ValueError(
                "RandomAgent chunk size drift: "
                f"factory={self.chunk_steps}, schema={schema_steps}"
            )

        steps: list[list[str]] = []
        sampled_counts: list[int] = []
        for _ in range(self.chunk_steps):
            # Layer 1: choose how many simultaneous outputs this slot has.
            count = self._rng.choice(self._available_counts)
            # Layer 2: choose the concrete compatible action combination.
            combination = self._rng.choice(self._combination_buckets[count])
            sampled_counts.append(count)
            steps.append(list(combination))

        mouse_output = self._sample_mouse_degrees()
        mouse = (
            degrees_to_mouse_units(mouse_output[0]),
            degrees_to_mouse_units(mouse_output[1]),
            mouse_output[2],
        )
        metadata = {
            "seed": self.seed,
            "decision_index": self.decision_index,
            "sampled_action_counts": sampled_counts,
            "mouse_output": {
                "X_degrees": mouse_output[0],
                "Y_degrees": mouse_output[1],
                "Z_scroll": mouse_output[2],
            },
        }
        self.last_action_metadata = metadata
        self.decision_index += 1

        return {
            "mouse": mouse,
            "mouse_output": mouse_output,
            "mouse_axes": self.mouse_axes,
            "steps": steps,
            "random_policy": metadata,
        }

    def _sample_mouse_degrees(self) -> tuple[float, float, float]:
        values = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        choices = {
            "X": self.mouse_x_choices,
            "Y": self.mouse_y_choices,
            "Z": self.mouse_z_choices,
        }
        for axis in self.mouse_axes:
            values[axis] = self._rng.choice(choices[axis])
        return values["X"], values["Y"], values["Z"]
