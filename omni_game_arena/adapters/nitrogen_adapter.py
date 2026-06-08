"""Action adapter for NitroGen gamepad output.

NitroGen returns joystick axes + button states. This adapter sends axis
commands for sticks and diffs button state for press/release events.
"""

import logging

from .base import BaseActionAdapter

logger = logging.getLogger(__name__)

# NitroGen button name -> UE5 FKey name
_BUTTON_MAP = {
    "SOUTH": "Gamepad_FaceButton_Bottom",       # A
    "EAST": "Gamepad_FaceButton_Right",          # B
    "WEST": "Gamepad_FaceButton_Left",           # X
    "NORTH": "Gamepad_FaceButton_Top",           # Y
    "LEFT_SHOULDER": "Gamepad_LeftShoulder",     # LB
    "RIGHT_SHOULDER": "Gamepad_RightShoulder",   # RB
    "LEFT_TRIGGER": "Gamepad_LeftTriggerAxis",   # LT
    "RIGHT_TRIGGER": "Gamepad_RightTriggerAxis", # RT
    "LEFT_THUMB": "Gamepad_LeftThumbstick",      # L3
    "RIGHT_THUMB": "Gamepad_RightThumbstick",    # R3
    "DPAD_UP": "Gamepad_DPad_Up",
    "DPAD_DOWN": "Gamepad_DPad_Down",
    "DPAD_LEFT": "Gamepad_DPad_Left",
    "DPAD_RIGHT": "Gamepad_DPad_Right",
    "START": "Gamepad_Special_Right",
    "BACK": "Gamepad_Special_Left",
    "GUIDE": "Gamepad_Special_Left",
}


class NitroGenAdapter(BaseActionAdapter):
    """Translates NitroGen gamepad actions into UE5 commands.

    Sends axis values for joysticks, diffs button state for press/release.
    """

    def __init__(
        self,
        stick_scale: float = 1.0,
        invert_y: bool = True,
        axis_deadzone: float = 0.0,
    ):
        self.stick_scale = stick_scale
        self.invert_y = invert_y
        self.axis_deadzone = axis_deadzone
        self._prev_buttons: set[str] = set()

    @property
    def action_schema(self) -> dict:
        return {"type": "nitrogen_gamepad"}

    def execute(self, client, action: dict) -> None:
        """Translate NitroGen action into UE5 axis/key events.

        Expected action format:
            {
                "j_left": [x, y],      # [-1, 1]
                "j_right": [x, y],     # [-1, 1]
                "buttons": {"SOUTH": 1, "EAST": 0, ...},
            }
        """
        # --- Joysticks ---
        jl = action.get("j_left", [0, 0])
        jr = action.get("j_right", [0, 0])
        client.send_axis("Gamepad_LeftX", self._stick_axis(jl[0]))
        client.send_axis("Gamepad_LeftY", self._stick_axis(jl[1], y_axis=True))
        client.send_axis("Gamepad_RightX", self._stick_axis(jr[0]))
        client.send_axis("Gamepad_RightY", self._stick_axis(jr[1], y_axis=True))

        # --- Analog triggers ---
        buttons = action.get("buttons", {})
        client.send_axis("Gamepad_LeftTriggerAxis", float(buttons.get("LEFT_TRIGGER", 0.0)))
        client.send_axis("Gamepad_RightTriggerAxis", float(buttons.get("RIGHT_TRIGGER", 0.0)))

        # --- Buttons (state-based diff) ---
        curr_pressed = {
            name for name, val in buttons.items()
            if val and name not in {"LEFT_TRIGGER", "RIGHT_TRIGGER"}
        }

        # Release buttons no longer held
        for btn in self._prev_buttons - curr_pressed:
            ue_key = _BUTTON_MAP.get(btn)
            if ue_key:
                client.send_key(ue_key, pressed=False)

        # Press newly held buttons
        for btn in curr_pressed - self._prev_buttons:
            ue_key = _BUTTON_MAP.get(btn)
            if ue_key:
                client.send_key(ue_key, pressed=True)
            else:
                logger.debug("Unmapped NitroGen button: %s", btn)

        self._prev_buttons = curr_pressed

    def _stick_axis(self, value, y_axis: bool = False) -> float:
        """Map NitroGen joystick value to UE5 axis value."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        if abs(v) < self.axis_deadzone:
            v = 0.0
        v *= self.stick_scale
        if y_axis and self.invert_y:
            v = -v
        return max(-1.0, min(1.0, v))

    def release_all(self, client) -> None:
        """Release all held buttons and zero joysticks."""
        for btn in self._prev_buttons:
            ue_key = _BUTTON_MAP.get(btn)
            if ue_key:
                client.send_key(ue_key, pressed=False)
        self._prev_buttons.clear()
        client.send_axis("Gamepad_LeftX", 0.0)
        client.send_axis("Gamepad_LeftY", 0.0)
        client.send_axis("Gamepad_RightX", 0.0)
        client.send_axis("Gamepad_RightY", 0.0)
        client.send_axis("Gamepad_LeftTriggerAxis", 0.0)
        client.send_axis("Gamepad_RightTriggerAxis", 0.0)
