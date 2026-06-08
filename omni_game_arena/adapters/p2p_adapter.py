"""Action adapter for P2P policy model output.

The P2P model returns *state-based* actions: which keys and mouse buttons
are currently pressed, plus mouse movement delta. This adapter diffs against
the previous state to emit the correct press/release events to UE5.
"""

import logging

from .base import BaseActionAdapter

logger = logging.getLogger(__name__)

# P2P mouse button ID -> UE5 button name
_MOUSE_BUTTON_MAP = {
    "0": "left",
    "1": "right",
    "2": "middle",
}

# P2P lowercase key -> UE5 FKey name
_KEY_MAP = {
    "w": "W",
    "a": "A",
    "s": "S",
    "d": "D",
    "space": "SpaceBar",
    " ": "SpaceBar",
    "shift": "LeftShift",
    "ctrl": "LeftControl",
    "e": "E",
    "f": "F",
    "r": "R",
    "q": "Q",
    "c": "C",
    "1": "One",
    "2": "Two",
    "3": "Three",
    "4": "Four",
    "5": "Five",
}


class P2PAdapter(BaseActionAdapter):
    """Translates P2P policy model actions into UE5 commands.

    The P2P model outputs which keys/buttons are currently held down.
    This adapter tracks previous state and only sends press/release deltas.
    """

    def __init__(self):
        self._prev_keys: set[str] = set()
        self._prev_mouse_buttons: set[str] = set()

    @property
    def action_schema(self) -> dict:
        # Not injected into any prompt - the P2P model has its own action space.
        return {"type": "p2p_policy_model"}

    def execute(self, client, action: dict) -> None:
        """Translate P2P state-based action into UE5 press/release events.

        Expected action format:
            {
                "keys": ["w", "d"],
                "mouse_buttons": ["0"],
                "mouse_delta_x": 10,
                "mouse_delta_y": -5,
            }
        """
        # --- Keyboard ---
        raw_keys = action.get("keys", [])
        curr_keys = set()
        for k in raw_keys:
            mapped = _KEY_MAP.get(k.lower(), k.upper())
            curr_keys.add(mapped)

        # Release keys no longer held
        for key in self._prev_keys - curr_keys:
            client.send_key(key, pressed=False)
        # Press newly held keys
        for key in curr_keys - self._prev_keys:
            client.send_key(key, pressed=True)
        self._prev_keys = curr_keys

        # --- Mouse buttons ---
        raw_mb = action.get("mouse_buttons", [])
        curr_mb = set(str(b) for b in raw_mb)

        for mb_id in self._prev_mouse_buttons - curr_mb:
            btn = _MOUSE_BUTTON_MAP.get(mb_id)
            if btn:
                client.send_mouse_button(btn, pressed=False)
        for mb_id in curr_mb - self._prev_mouse_buttons:
            btn = _MOUSE_BUTTON_MAP.get(mb_id)
            if btn:
                client.send_mouse_button(btn, pressed=True)
        self._prev_mouse_buttons = curr_mb

        # --- Mouse movement ---
        dx = action.get("mouse_delta_x", 0)
        dy = action.get("mouse_delta_y", 0)
        if dx or dy:
            client.send_mouse_move(float(dx), float(dy))

    def release_all(self, client) -> None:
        """Release all currently held keys and mouse buttons."""
        for key in self._prev_keys:
            client.send_key(key, pressed=False)
        for mb_id in self._prev_mouse_buttons:
            btn = _MOUSE_BUTTON_MAP.get(mb_id)
            if btn:
                client.send_mouse_button(btn, pressed=False)
        self._prev_keys.clear()
        self._prev_mouse_buttons.clear()
