"""Chunked keyboard + mouse action adapter (Lumine-style).

Each VLM call produces a chunk of 8 steps (120ms each = 960ms total).
Format: <|action_start|>X Y Z ; k1 k2 ; k3 ; ; k4 ; ; k5 k6 ; ; k7<|action_end|>
"""

import time
import logging

from .base import BaseActionAdapter

logger = logging.getLogger(__name__)

# -- Key name mapping: VLM-friendly name -> UE5 FKey name ------------------
VLM_TO_UE5_KEY = {
    # Letters (VLM uses same name)
    "W": "W", "A": "A", "S": "S", "D": "D",
    "E": "E", "F": "F", "Q": "Q", "R": "R",
    "G": "G", "H": "H", "V": "V", "B": "B",
    "C": "C", "X": "X", "Z": "Z",
    # Special keys
    "Space": "SpaceBar",
    "Shift": "LeftShift",
    "Ctrl": "LeftControl",
    "Alt": "LeftAlt",
    "Tab": "Tab",
    "Esc": "Escape",
    # Number keys (Lumine convention: lowercase English words)
    "one": "One", "two": "Two", "three": "Three",
    "four": "Four", "five": "Five", "six": "Six",
    "seven": "Seven", "eight": "Eight", "nine": "Nine",
    "zero": "Zero",
}

# -- Mouse button mappings (treated as special "keys" in action chunks) --
# VLM-facing names are fixed three-letter acronyms - do not use aliases
# like Fire / LeftClick / Shoot; every FPS game shares this vocabulary.
MOUSE_BUTTONS = {
    "LMB": "left",
    "RMB": "right",
    "MMB": "middle",
}

# -- Default key bindings shown in system prompt --------------------------
DEFAULT_KEY_BINDINGS = {
    "W": "Move forward",
    "A": "Move left",
    "S": "Move backward",
    "D": "Move right",
    "Space": "Jump",
}

CHUNK_STEPS = 8
STEP_DURATION = 0.120  # 120ms per step - 8 steps = 960ms per chunk
TAP_DURATION = 0.030


_MOUSE_AXIS_DESCRIPTIONS = {
    "X": (
        f"'Mouse X': desired horizontal rotation in degrees "
        f"(X>0 = turn right)"
    ),
    "Y": (
        f"'Mouse Y': desired vertical rotation in degrees "
        f"(Y>0 = look up)"
    ),
    "Z": "'Scroll Z': mouse wheel (Z>0 = scroll up)",
}


class TimedChunkedAction:
    """Incremental runner for one chunked action.

    The normal execute() path blocks until the whole chunk finishes. LCRT
    drives this object phase by phase so another player can start midway.
    """

    def __init__(self, adapter, client, action: dict):
        self.adapter = adapter
        self.client = client
        self.action = action
        self.steps = list(action.get("steps", []))
        self.step_index = 0
        self.prev_keys: set[str] = set()
        self.active_keys: set[str] = set()
        self.phase = "init"
        self.phase_delay_s = 0.0
        self.hold_keys: set[str] = set()
        self.tap_keys: set[str] = set()
        self.done = False

    def start(self) -> None:
        mouse = self.action.get("mouse", (0, 0, 0))
        dx, dy, _scroll = mouse
        if abs(dx) >= 0.5 or abs(dy) >= 0.5:
            self.client.send_mouse_move(dx, dy)
        self._start_next_step()
        self._drain_zero_delay_events()

    def next_delay_s(self) -> float:
        if self.done:
            return 0.0
        return max(0.0, float(self.phase_delay_s))

    def advance_event(self) -> None:
        if self.done:
            return
        if self.phase == "step_end":
            self.prev_keys = set(self.hold_keys)
            self.step_index += 1
            self._start_next_step()
        elif self.phase == "tap_start":
            for key in self.hold_keys:
                self._press(key, False)
            for key in self.tap_keys:
                self._press(key, True)
            self.phase = "tap_end"
            self.phase_delay_s = min(
                self.adapter.tap_duration,
                self.adapter.step_duration,
            )
        elif self.phase == "tap_end":
            for key in self.tap_keys:
                self._press(key, False)
            self.prev_keys = set()
            self.step_index += 1
            self._start_next_step()
        else:
            self._start_next_step()
        self._drain_zero_delay_events()

    def cancel(self) -> None:
        self._release_all_active()
        self.done = True

    def _start_next_step(self) -> None:
        if self.step_index >= len(self.steps):
            self._release_all_active()
            self.done = True
            self.phase = "done"
            self.phase_delay_s = 0.0
            return

        curr_keys = set(self.steps[self.step_index])
        self.tap_keys = curr_keys & self.adapter._tap_key_set
        self.hold_keys = curr_keys - self.adapter._tap_key_set

        for key in self.prev_keys - self.hold_keys:
            self._press(key, False)
        for key in self.hold_keys - self.prev_keys:
            self._press(key, True)

        if self.tap_keys:
            tap_sleep = min(self.adapter.tap_duration, self.adapter.step_duration)
            self.phase = "tap_start"
            self.phase_delay_s = self.adapter.step_duration - tap_sleep
        else:
            self.phase = "step_end"
            self.phase_delay_s = self.adapter.step_duration

    def _drain_zero_delay_events(self) -> None:
        guard = 0
        while not self.done and self.phase_delay_s <= 0 and guard < 1024:
            guard += 1
            self.advance_event()

    def _press(self, key: str, pressed: bool) -> None:
        self.adapter._press(self.client, key, pressed)
        if pressed:
            self.active_keys.add(key)
        else:
            self.active_keys.discard(key)

    def _release_all_active(self) -> None:
        for key in list(self.active_keys):
            self.adapter._press(self.client, key, False)
        self.active_keys.clear()
        self.prev_keys.clear()


class KeyboardMouseChunkedAdapter(BaseActionAdapter):
    """Lumine-style chunked action adapter.

    Each action chunk = mouse movement + 8 key steps (120ms each = 960ms total).
    Keys are held across consecutive steps without re-pressing.

    ``mouse_axes`` selects which mouse axes the game uses. For example,
    ObstacleRun3D only rotates the camera horizontally, so it passes
    ``mouse_axes=("X",)`` - the prompt drops Y/Z from the controls list
    and the output format becomes ``<|action_start|>X ; k1 ; ...<|action_end|>``.
    """

    def __init__(
        self,
        key_bindings: dict | None = None,
        mouse_axes: tuple[str, ...] = ("X", "Y", "Z"),
        chunk_steps: int = CHUNK_STEPS,
        tap_keys: tuple[str, ...] = (),
        tap_duration: float = TAP_DURATION,
        step_duration: float = STEP_DURATION,
    ):
        self.key_bindings = key_bindings or DEFAULT_KEY_BINDINGS
        # Preserve caller order so the prompt renders axes in the order passed.
        self.mouse_axes = tuple(mouse_axes)
        self.chunk_steps = int(chunk_steps)
        self.tap_keys = tuple(tap_keys)
        self._tap_key_set = set(self.tap_keys)
        self.tap_duration = max(0.0, float(tap_duration))
        self.step_duration = max(0.0, float(step_duration))

    @property
    def action_schema(self) -> dict:
        """Return schema info consumed by the prompt composer."""
        key_list = "\n".join(f"  - '{k}': {v}" for k, v in self.key_bindings.items())
        mouse_lines = [
            f"  - {_MOUSE_AXIS_DESCRIPTIONS[axis]}"
            for axis in self.mouse_axes
            if axis in _MOUSE_AXIS_DESCRIPTIONS
        ]
        mouse_list = "\n".join(mouse_lines)
        return {
            "format": "lumine_chunked",
            "key_bindings": key_list,
            "mouse_controls": mouse_list,
            "mouse_axes": self.mouse_axes,
            "chunk_steps": self.chunk_steps,
            "step_duration_ms": int(self.step_duration * 1000),
            "tap_keys": self.tap_keys,
            "tap_duration_ms": int(self.tap_duration * 1000),
        }

    def _map_key(self, key: str) -> str:
        """Map VLM-friendly key name to UE5 FKey name."""
        return VLM_TO_UE5_KEY.get(key, key)

    def _is_mouse_button(self, key: str) -> bool:
        """Check if key is a mouse button alias."""
        return key in MOUSE_BUTTONS

    def _press(self, client, key: str, pressed: bool) -> None:
        """Press/release a key or mouse button."""
        if key in MOUSE_BUTTONS:
            client.send_mouse_button(MOUSE_BUTTONS[key], pressed)
        else:
            client.send_key(self._map_key(key), pressed=pressed)

    def execute(self, client, action: dict) -> None:
        """Execute chunked action: mouse move + 8 key steps x 120ms.

        action: {"mouse": (dx, dy, scroll), "steps": [["W","A"], ["W"], [], ...]}

        Keys that persist across consecutive steps are held without
        releasing, to avoid input stuttering.
        Supports mouse button names LMB / RMB / MMB.
        """
        t0 = time.perf_counter()
        timed = self.start_timed_action(client, action)
        while not timed.done:
            delay = timed.next_delay_s()
            if delay > 0:
                time.sleep(delay)
            timed.advance_event()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        steps = action.get("steps", [])
        target_ms = len(steps) * int(self.step_duration * 1000)
        logger.info("Chunk executed: %d steps in %.1fms (target %dms)",
                     len(steps), elapsed_ms, target_ms)

    def start_timed_action(self, client, action: dict) -> TimedChunkedAction:
        timed = TimedChunkedAction(self, client, action)
        timed.start()
        return timed

    def release_all(self, client) -> None:
        """Release all known keys and mouse buttons (called on env close)."""
        for vlm_key in self.key_bindings:
            self._press(client, vlm_key, pressed=False)
