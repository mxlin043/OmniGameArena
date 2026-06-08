"""Manual human teleoperation for the UE5 RemoteInput environment.

Drive any OmniGameArena scene by hand through the SAME TCP channel the agents
use (RemoteInput, default port 12345), so you can test maps, sanity-check
controls, or record a human baseline without the model in the loop.

A live Tk window streams the game frames; the chosen input backend forwards
your physical keyboard/mouse or gamepad to UE5.

Keyboard + mouse are always live (captured from the live view window), and a
gamepad is added automatically when one is connected (read through pygame,
XInput / Xbox layout). They drive UE5 simultaneously.

Usage (PowerShell):
    python scripts/manual_control.py                  # keyboard + mouse (+ gamepad if present)
    python scripts/manual_control.py --map last_stand

The P key is intentionally not forwarded: it opens the in-game Map Select
menu, which RemoteInput cannot click (see below). Switch maps with --map or
the backtick console instead.

Keyboard mode:
    Keep the view window focused. WASD move, Space jump, Shift sprint, etc.
    Click the view to capture the mouse for camera look (cursor hides);
    while captured the mouse buttons send LMB/RMB/MMB. Press Esc to release
    the mouse; Esc only reaches the game when the mouse is not captured.

    The in-game Map Select menu cannot be clicked through RemoteInput (the
    mouse is camera-relative, not an OS cursor). Switch maps with --map, or
    at runtime press the backtick key (`) to open a console box and type
    e.g. "open last_stand" (configs/maps.yaml keys are resolved for you).

The gamepad forwards native UE gamepad axes/buttons (left stick = move,
right stick = look, A/B/X/Y, bumpers, analog triggers, D-pad), the same
vocabulary the NitroGen policy uses, so any scene that accepts a controller
will respond. The controller works regardless of which window is focused.

A gamepad needs ``pygame`` (pip install pygame); without it, or without a
controller, the script just runs keyboard + mouse.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import simpledialog

import yaml
from PIL import Image, ImageFile, ImageTk

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Make `omni_game_arena` importable when the script is run from anywhere.
sys.path.insert(0, _REPO_ROOT)

from omni_game_arena.env.client_ue5 import UE5Client  # noqa: E402

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger("manual_control")


# -- Keyboard: Tk keysym -> UE5 FKey name ---------------------------------
# Single letters fall through to ``keysym.upper()`` ("w" -> "W"). Everything
# else that a scene might use is listed explicitly. Names match the mappings
# already proven in omni_game_arena/adapters (keyboard_mouse_chunked, p2p).
_TK_KEYSYM_TO_UE = {
    "space": "SpaceBar",
    "Shift_L": "LeftShift", "Shift_R": "LeftShift",
    "Control_L": "LeftControl", "Control_R": "LeftControl",
    "Alt_L": "LeftAlt", "Alt_R": "LeftAlt",
    "Tab": "Tab",
    "Return": "Enter",
    "Escape": "Escape",
    "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
    "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five",
    "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine", "0": "Zero",
}

# Tk <Button-N> number -> UE5 mouse button name.
_TK_BUTTON_TO_UE = {1: "left", 2: "middle", 3: "right"}

# UE keys we never forward. P opens the in-game Map Select menu, which cannot
# be operated through RemoteInput (the mouse is gameplay input, not a UI
# cursor), so forwarding it just traps the player in an unusable menu. Switch
# maps with the backtick console or --map instead.
_BLOCKED_UE_KEYS = {"P"}


def map_keysym(keysym: str) -> str | None:
    """Translate a Tk keysym into a UE5 FKey name, or None if unmapped."""
    if keysym in _TK_KEYSYM_TO_UE:
        return _TK_KEYSYM_TO_UE[keysym]
    if len(keysym) == 1 and keysym.isalpha():
        return keysym.upper()
    return None


def load_map_registry() -> dict[str, str]:
    """Load configs/maps.yaml -> {friendly key: UE package path}.

    Lets ``--map last_stand`` and the in-app console resolve the same short
    names the benchmark configs use, instead of full ``/Game/...`` paths.
    """
    path = os.path.join(_REPO_ROOT, "configs", "maps.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in (data.get("maps") or {}).items()}
    except Exception as e:
        logger.debug("could not load map registry: %s", e)
        return {}


def resolve_map(name: str, registry: dict[str, str]) -> str:
    """Map a friendly key (e.g. 'last_stand') to its UE path; pass full paths
    and unknown names through unchanged (UE5Client.open_map handles those)."""
    return registry.get(name, name)


def multiplayer_game_names() -> set[str]:
    """Names of registered games whose mode is PvP or Coop (those have a player 2)."""
    try:
        from omni_game_arena.benchmark.games import get_game, list_games
        return {n for n in list_games() if get_game(n).mode in ("pvp", "coop")}
    except Exception as e:
        logger.debug("game registry unavailable: %s", e)
        return {"shared_floor", "handoff_run", "sky_duel", "crystal_guard", "midline_clash"}


def map_supports_two_players(map_value: str) -> bool:
    """True if a --map value refers to a PvP/Coop game (including its variants)."""
    key = (map_value or "").strip()
    if not key:
        return False
    names = multiplayer_game_names()
    if any(key == n or key.startswith(n + "_") for n in names):
        return True
    # Fallback for raw UE paths (e.g. .../CookHouse_Coop, .../BaseAssault_VS).
    low = key.lower()
    return "/" in key and any(t in low for t in ("coop", "_vs", "asycoop"))


# -- Gamepad: pygame index -> UE5 name (Xbox / XInput layout under SDL2) ---
# These match omni_game_arena/adapters/nitrogen_adapter.py, which already
# drives UE5 through these exact axis/button names.
_PAD_BUTTON_TO_UE = {
    0: "Gamepad_FaceButton_Bottom",   # A
    1: "Gamepad_FaceButton_Right",    # B
    2: "Gamepad_FaceButton_Left",     # X
    3: "Gamepad_FaceButton_Top",      # Y
    4: "Gamepad_LeftShoulder",        # LB
    5: "Gamepad_RightShoulder",       # RB
    6: "Gamepad_Special_Left",        # Back / View
    7: "Gamepad_Special_Right",       # Start / Menu
    8: "Gamepad_LeftThumbstick",      # L3
    9: "Gamepad_RightThumbstick",     # R3
}


class ManualControlApp:
    """Tk live-view window that forwards human input to a UE5Client.

    The window streams screenshots from UE5 on a background thread and lets
    the active input backend forward keyboard/mouse or gamepad events. Both
    backends share this window for display; only the input source differs.
    """

    def __init__(
        self,
        client: UE5Client,
        *,
        mode: str,
        host: str,
        port: int,
        fps: int,
        width: int,
        height: int,
        map_registry: dict[str, str] | None = None,
    ):
        self.client = client
        self.mode = mode
        self.map_registry = map_registry or {}
        self.host = host
        self.port = port
        self.fps = max(1, fps)
        self._width = width
        self._height = height

        self.backends: list["_InputBackend"] = []
        self._help_text = ""
        self._status_extra = "connecting..."

        self._running = True
        self._quit_requested = False
        self._stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._photo = None

        self.root = tk.Tk()
        self.root.title(f"OmniGameArena Manual Control [{mode}]")
        self.root.geometry(f"{width}x{height + 28}")
        self.root.configure(bg="black")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        # Backtick (UE console key) opens a command box: switch maps, slomo, etc.
        self.root.bind("<KeyPress-grave>", self._open_console)

        self._status_var = tk.StringVar(value="Waiting for first frame...")
        status = tk.Label(
            self.root, textvariable=self._status_var, bd=1, relief=tk.SUNKEN,
            anchor=tk.W, bg="#222", fg="#0f0", font=("Consolas", 9),
        )
        status.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(
            self.root, width=width, height=height, bg="black", highlightthickness=0,
        )
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # -- backend hooks ---------------------------------------------------

    def set_backends(self, backends: list["_InputBackend"]) -> None:
        self.backends = list(backends)
        self._help_text = "  ||  ".join(b.help_text() for b in self.backends)

    def set_mode_label(self, label: str) -> None:
        """Update the window title / status to reflect the active backends."""
        self.mode = label
        try:
            self.root.title(f"OmniGameArena Manual Control [{label}]")
        except Exception:
            pass

    def center(self) -> tuple[int, int]:
        """Canvas-relative center, used for mouse-look re-centering."""
        w = self.canvas.winfo_width() or self._width
        h = self.canvas.winfo_height() or self._height
        return w // 2, h // 2

    # -- in-app UE console (backtick) ------------------------------------

    def _open_console(self, event=None) -> None:
        """Prompt for a UE console command. The in-game Map Select menu is
        mouse-only and the RemoteInput mouse is camera-relative, so switch
        maps here instead (e.g. ``open last_stand``)."""
        for backend in self.backends:
            backend.prepare_for_dialog()
        try:
            cmd = simpledialog.askstring(
                "UE console",
                "command  (e.g. 'open last_stand', 'slomo 0.5'):",
                parent=self.root,
            )
        except Exception:
            cmd = None
        if cmd:
            self._send_console(cmd.strip())

    def _send_console(self, cmd: str) -> None:
        """Send a console command, expanding maps.yaml keys to UE paths."""
        if not cmd:
            return
        reg = self.map_registry
        if cmd in reg:                      # bare key: "last_stand"
            self.client.open_map(reg[cmd])
            logger.info("open map %s -> %s", cmd, reg[cmd])
        elif cmd.lower().startswith("open "):
            arg = cmd[5:].strip()
            target = reg.get(arg, arg)
            self.client.open_map(target)
            logger.info("open map %s", target)
        else:
            self.client.console_command(cmd)
            logger.info("console: %s", cmd)

    # -- run loop --------------------------------------------------------

    def run(self) -> None:
        self.root.after(0, self.root.focus_force)
        self._stream_thread = threading.Thread(
            target=self._stream_loop, name="manual-stream", daemon=True
        )
        self._stream_thread.start()
        # Ctrl-C: Tk's mainloop swallows KeyboardInterrupt raised inside Tk
        # callbacks, so install a SIGINT handler that just sets a flag and tear
        # down promptly from the periodic _tick on the Tk loop.
        try:
            signal.signal(signal.SIGINT, self._on_interrupt)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported
        self._tick()
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.shutdown()

    def _on_interrupt(self, *_args) -> None:
        self._quit_requested = True

    def _tick(self) -> None:
        if self._quit_requested:
            self.shutdown()
            return
        if self._running:
            self.root.after(100, self._tick)

    def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        for backend in self.backends:
            try:
                backend.release_all()
            except Exception as e:
                logger.debug("backend release_all error: %s", e)
        # Close the socket first so a stream thread blocked inside screenshot()
        # is unblocked immediately; otherwise the join below stalls for seconds.
        try:
            self.client.disconnect()
        except Exception:
            pass
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=1.0)
        try:
            self.root.destroy()
        except Exception:
            pass
        logger.info("Manual control closed")

    # -- screenshot streaming (background thread) ------------------------

    def _stream_loop(self) -> None:
        interval = 1.0 / self.fps
        frame = 0
        consecutive = 0
        poll_every = max(1, self.fps)  # ~once per second
        while self._running and not self._stop.is_set():
            t0 = time.monotonic()
            try:
                result = self.client.screenshot()
                image = Image.open(io.BytesIO(result["data"]))
                image.load()
                consecutive = 0
                if self._running and self.root is not None:
                    self.root.after(0, self._set_image, image)

                frame += 1
                if frame % poll_every == 0:
                    self._poll_status()
                    if self._running and self.root is not None:
                        self.root.after(0, self._refresh_status)
            except Exception as e:
                consecutive += 1
                logger.debug("stream frame error (%d): %s", consecutive, e)
                # A map switch (open ...) or UE restart drops the socket; the
                # viewer recovers itself so manual play survives map changes.
                dropped = isinstance(e, (ConnectionError, OSError)) or not self.client.connected
                if self._running and (dropped or consecutive >= 5):
                    if self.client.reconnect():
                        self.client.resume_world()
                        logger.info("reconnected to UE5")
                        consecutive = 0
                        continue
                if self._stop.wait(0.3):
                    break
                continue

            sleep_for = interval - (time.monotonic() - t0)
            if sleep_for > 0 and self._stop.wait(sleep_for):
                break

    def _poll_status(self) -> None:
        """Query score + game-over for the status bar (stream thread only)."""
        try:
            score = self.client.get_score()
            over = self.client.game_over
            if not over:
                over = self.client.check_game_over(timeout=0.3)
            state = "GAME OVER" if over else "LIVE"
            self._status_extra = f"score={score:.3f} | {state}"
        except Exception as e:
            logger.debug("status poll error: %s", e)

    # -- Tk-thread callbacks ---------------------------------------------

    def _set_image(self, image: Image.Image) -> None:
        if not self._running:
            return
        cw = self.canvas.winfo_width() or self._width
        ch = self.canvas.winfo_height() or self._height
        iw, ih = image.size
        scale = min(cw / iw, ch / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        if (nw, nh) != (iw, ih):
            image = image.resize((nw, nh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(
            (cw - nw) // 2, (ch - nh) // 2, anchor=tk.NW, image=self._photo
        )

    def _refresh_status(self) -> None:
        self._status_var.set(
            f"{self.mode} | {self.host}:{self.port} | {self.fps}fps | "
            f"{self._help_text} | {self._status_extra}"
        )


class _InputBackend:
    """Common interface for input backends."""

    def attach(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def release_all(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def help_text(self) -> str:  # pragma: no cover - interface
        return ""

    def prepare_for_dialog(self) -> None:
        """Called before a modal dialog opens (e.g. release a captured mouse)."""


class KeyboardMouseBackend(_InputBackend):
    """Capture keyboard + mouse from the Tk window and forward to UE5.

    Movement keys forward whenever the window is focused. Click the view to
    capture the mouse for camera look (relative deltas via pointer warp);
    Esc releases capture. While captured the mouse buttons map to LMB/RMB/MMB.
    """

    def __init__(self, app: ManualControlApp, sensitivity: float, invert_look: bool = False):
        self.app = app
        self.sensitivity = sensitivity
        # UE RemoteInput treats Mouse Y>0 as "look up", but screen Y grows
        # downward (moving the mouse up gives a negative delta). Negate by
        # default so pushing up looks up, matching the native UE game.
        self._y_sign = 1.0 if invert_look else -1.0
        self._down: set[str] = set()           # UE keys currently held
        self._buttons: set[str] = set()         # UE mouse buttons currently held
        self._captured = False

    def help_text(self) -> str:
        return "click=capture mouse, Esc=release, WASD/Space/Shift, LMB/RMB, `=console(switch map)"

    def prepare_for_dialog(self) -> None:
        if self._captured:
            self._release_capture()

    def attach(self) -> None:
        root, canvas = self.app.root, self.app.canvas
        root.bind("<KeyPress>", self._on_keypress)
        root.bind("<KeyRelease>", self._on_keyrelease)
        # When the window is deactivated (a level reset, e.g. R, or alt-tab can
        # steal focus), release every held key. Otherwise a key whose key-up
        # landed in another window stays stuck "pressed" in _down and every
        # later tap of it is silently ignored.
        root.bind("<Deactivate>", lambda _e: self.release_all())
        canvas.bind("<Button-1>", self._on_button_down)
        canvas.bind("<Button-2>", self._on_button_down)
        canvas.bind("<Button-3>", self._on_button_down)
        canvas.bind("<ButtonRelease-1>", self._on_button_up)
        canvas.bind("<ButtonRelease-2>", self._on_button_up)
        canvas.bind("<ButtonRelease-3>", self._on_button_up)
        canvas.bind("<Motion>", self._on_motion)

    # -- keyboard --------------------------------------------------------

    def _on_keypress(self, event: "tk.Event") -> None:
        ue = map_keysym(event.keysym)
        if ue is None or ue in _BLOCKED_UE_KEYS:
            return
        if ue == "Escape" and self._captured:
            self._release_capture()
            return
        if ue in self._down:
            return  # Windows auto-repeat: ignore until release
        self._down.add(ue)
        self.app.client.send_key(ue, pressed=True)

    def _on_keyrelease(self, event: "tk.Event") -> None:
        ue = map_keysym(event.keysym)
        if ue is None or ue not in self._down:
            return
        self._down.discard(ue)
        self.app.client.send_key(ue, pressed=False)

    # -- mouse -----------------------------------------------------------

    def _on_button_down(self, event: "tk.Event") -> None:
        if not self._captured:
            self._capture()  # the capturing click is consumed, not forwarded
            return
        btn = _TK_BUTTON_TO_UE.get(event.num)
        if btn and btn not in self._buttons:
            self._buttons.add(btn)
            self.app.client.send_mouse_button(btn, pressed=True)

    def _on_button_up(self, event: "tk.Event") -> None:
        btn = _TK_BUTTON_TO_UE.get(event.num)
        if btn and btn in self._buttons:
            self._buttons.discard(btn)
            self.app.client.send_mouse_button(btn, pressed=False)

    def _on_motion(self, event: "tk.Event") -> None:
        if not self._captured:
            return
        cx, cy = self.app.center()
        dx, dy = event.x - cx, event.y - cy
        if dx == 0 and dy == 0:
            return  # the warp-recenter echo (warps land exactly on center)
        self.app.client.send_mouse_move(
            dx * self.sensitivity, dy * self._y_sign * self.sensitivity
        )
        # Pull the pointer back to center so look never runs into a window edge.
        self.app.canvas.event_generate("<Motion>", warp=True, x=cx, y=cy)

    def _capture(self) -> None:
        self._captured = True
        self.app.canvas.config(cursor="none")
        self.app.canvas.focus_set()
        cx, cy = self.app.center()
        self.app.canvas.event_generate("<Motion>", warp=True, x=cx, y=cy)
        logger.info("Mouse captured (Esc to release)")

    def _release_capture(self) -> None:
        self._captured = False
        self.app.canvas.config(cursor="")
        # Release any mouse buttons so nothing sticks down after letting go.
        for btn in list(self._buttons):
            self.app.client.send_mouse_button(btn, pressed=False)
        self._buttons.clear()
        logger.info("Mouse released")

    def release_all(self) -> None:
        for key in list(self._down):
            self.app.client.send_key(key, pressed=False)
        self._down.clear()
        for btn in list(self._buttons):
            self.app.client.send_mouse_button(btn, pressed=False)
        self._buttons.clear()


class GamepadBackend(_InputBackend):
    """Read a physical controller via pygame and forward native UE5 gamepad
    axes/buttons. Polled from the Tk event loop, so no extra thread.

    Axis indices default to the SDL2 Xbox/XInput layout; override with the
    ``--lx/--ly/--rx/--ry/--lt/--rt`` flags if your controller differs.
    """

    def __init__(self, app: ManualControlApp, args: argparse.Namespace):
        self.app = app
        self.deadzone = max(0.0, min(0.95, args.deadzone))
        self.invert_y = not args.no_invert_y
        self.poll_ms = max(4, int(1000 / max(1, args.poll_hz)))
        self.axis_idx = {
            "lx": args.lx, "ly": args.ly, "rx": args.rx,
            "ry": args.ry, "lt": args.lt, "rt": args.rt,
        }
        self._active = False
        self._poll_id = None
        self._last_axis: dict[str, float] = {}
        self._btn_down: set[str] = set()
        self._dpad_down: set[str] = set()
        self._lt_rest = -1.0
        self._rt_rest = -1.0

        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        try:
            import pygame  # lazy: only required for gamepad mode
        except ImportError as e:
            raise RuntimeError("pygame not installed (pip install pygame for gamepad)") from e

        self.pygame = pygame
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            pygame.quit()
            raise RuntimeError("no controller connected")
        self.js = pygame.joystick.Joystick(0)
        self.js.init()
        # Calibrate trigger rest values (SDL2 rests at -1; some pads rest at 0).
        pygame.event.pump()
        self._lt_rest = self._raw_axis(self.axis_idx["lt"])
        self._rt_rest = self._raw_axis(self.axis_idx["rt"])
        logger.info(
            "Gamepad: %s (%d axes, %d buttons, %d hats)",
            self.js.get_name(), self.js.get_numaxes(),
            self.js.get_numbuttons(), self.js.get_numhats(),
        )

    def help_text(self) -> str:
        return f"{self.js.get_name()} | L-stick move, R-stick look, A/B/X/Y, triggers, D-pad"

    def attach(self) -> None:
        self._active = True
        self._poll()

    # -- polling ---------------------------------------------------------

    def _poll(self) -> None:
        if not self._active:
            return
        try:
            self.pygame.event.pump()
            self._read_sticks()
            self._read_triggers()
            self._read_buttons()
            self._read_dpad()
        except Exception as e:
            logger.debug("gamepad poll error: %s", e)
        self._poll_id = self.app.root.after(self.poll_ms, self._poll)

    def _raw_axis(self, idx: int) -> float:
        try:
            return float(self.js.get_axis(idx))
        except Exception:
            return 0.0

    def _read_sticks(self) -> None:
        self._send_stick("Gamepad_LeftX", self._raw_axis(self.axis_idx["lx"]))
        self._send_stick("Gamepad_LeftY", self._raw_axis(self.axis_idx["ly"]), invert=self.invert_y)
        self._send_stick("Gamepad_RightX", self._raw_axis(self.axis_idx["rx"]))
        self._send_stick("Gamepad_RightY", self._raw_axis(self.axis_idx["ry"]), invert=self.invert_y)

    def _send_stick(self, ue_axis: str, raw: float, *, invert: bool = False) -> None:
        v = raw
        if abs(v) < self.deadzone:
            v = 0.0
        else:  # rescale past the deadzone so the usable range stays full
            sign = 1.0 if v > 0 else -1.0
            v = sign * (abs(v) - self.deadzone) / (1.0 - self.deadzone)
        if invert:
            v = -v
        v = max(-1.0, min(1.0, v))
        if abs(v - self._last_axis.get(ue_axis, 99.0)) >= 0.01:
            self.app.client.send_axis(ue_axis, v)
            self._last_axis[ue_axis] = v

    def _read_triggers(self) -> None:
        self._send_trigger("Gamepad_LeftTriggerAxis", self._raw_axis(self.axis_idx["lt"]), self._lt_rest)
        self._send_trigger("Gamepad_RightTriggerAxis", self._raw_axis(self.axis_idx["rt"]), self._rt_rest)

    def _send_trigger(self, ue_axis: str, raw: float, rest: float) -> None:
        span = 1.0 - rest
        t = (raw - rest) / span if abs(span) > 1e-6 else 0.0
        t = max(0.0, min(1.0, t))
        if t < 0.02:
            t = 0.0
        if abs(t - self._last_axis.get(ue_axis, 99.0)) >= 0.01:
            self.app.client.send_axis(ue_axis, t)
            self._last_axis[ue_axis] = t

    def _read_buttons(self) -> None:
        curr: set[str] = set()
        for idx, ue in _PAD_BUTTON_TO_UE.items():
            try:
                if idx < self.js.get_numbuttons() and self.js.get_button(idx):
                    curr.add(ue)
            except Exception:
                pass
        self._diff_press(self._btn_down, curr)
        self._btn_down = curr

    def _read_dpad(self) -> None:
        curr: set[str] = set()
        try:
            if self.js.get_numhats() > 0:
                hx, hy = self.js.get_hat(0)
                if hx == 1:
                    curr.add("Gamepad_DPad_Right")
                elif hx == -1:
                    curr.add("Gamepad_DPad_Left")
                if hy == 1:
                    curr.add("Gamepad_DPad_Up")
                elif hy == -1:
                    curr.add("Gamepad_DPad_Down")
        except Exception:
            pass
        self._diff_press(self._dpad_down, curr)
        self._dpad_down = curr

    def _diff_press(self, prev: set[str], curr: set[str]) -> None:
        for ue in prev - curr:
            self.app.client.send_key(ue, pressed=False)
        for ue in curr - prev:
            self.app.client.send_key(ue, pressed=True)

    def release_all(self) -> None:
        self._active = False
        if self._poll_id is not None:
            try:
                self.app.root.after_cancel(self._poll_id)
            except Exception:
                pass
        for ue in list(self._btn_down) + list(self._dpad_down):
            self.app.client.send_key(ue, pressed=False)
        self._btn_down.clear()
        self._dpad_down.clear()
        for axis in ("Gamepad_LeftX", "Gamepad_LeftY", "Gamepad_RightX",
                     "Gamepad_RightY", "Gamepad_LeftTriggerAxis", "Gamepad_RightTriggerAxis"):
            self.app.client.send_axis(axis, 0.0)
        try:
            self.pygame.quit()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Manual human control of the UE5 RemoteInput environment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default="127.0.0.1", help="UE5 RemoteInput host.")
    p.add_argument("--port", type=int, default=12345, help="UE5 RemoteInput base port (player 1).")
    p.add_argument(
        "--player", type=int, default=1, choices=(1, 2),
        help="Which player to control in two-player games. The server gives "
             "each player its own port (player 1 = base port, player 2 = base+1).",
    )
    p.add_argument(
        "--map", default="",
        help="Optional: open a map after connecting. A configs/maps.yaml key "
             "(e.g. 'last_stand') or a full '/Game/...' path.",
    )
    p.add_argument("--fps", type=int, default=30, help="Live view capture rate.")
    p.add_argument("--quality", type=int, default=85, help="Screenshot JPEG quality.")
    p.add_argument("--width", type=int, default=1024, help="View window width.")
    p.add_argument("--height", type=int, default=600, help="View window height.")

    kbm = p.add_argument_group("keyboard mode")
    kbm.add_argument(
        "--sensitivity", "-s", type=float, default=0.6,
        help="Mouse-look gain (screen px -> UE rotation). Lower = slower camera.",
    )
    kbm.add_argument(
        "--invert-look", action="store_true",
        help="Invert vertical mouse-look (push the mouse up to look down).",
    )

    pad = p.add_argument_group("gamepad mode")
    pad.add_argument("--deadzone", type=float, default=0.15, help="Stick deadzone.")
    pad.add_argument("--poll-hz", type=int, default=60, help="Gamepad poll rate.")
    pad.add_argument("--no-invert-y", action="store_true",
                     help="Do not invert stick Y (default inverts so up = forward/up).")
    pad.add_argument("--lx", type=int, default=0, help="Axis index: left stick X.")
    pad.add_argument("--ly", type=int, default=1, help="Axis index: left stick Y.")
    pad.add_argument("--rx", type=int, default=2, help="Axis index: right stick X.")
    pad.add_argument("--ry", type=int, default=3, help="Axis index: right stick Y.")
    pad.add_argument("--lt", type=int, default=4, help="Axis index: left trigger.")
    pad.add_argument("--rt", type=int, default=5, help="Axis index: right trigger.")

    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Player 2 only exists in PvP/Coop games. Refuse it for single-player maps;
    # if no --map was given we cannot tell the game type, so just warn.
    if args.player == 2:
        if args.map and not map_supports_two_players(args.map):
            raise SystemExit(
                f"--player 2 is only for PvP/Coop games, but '{args.map}' is "
                f"single-player. Two-player games: "
                f"{', '.join(sorted(multiplayer_game_names()))}."
            )
        if not args.map:
            logger.warning(
                "Player 2 only exists in PvP/Coop games; pass --map <pvp/coop game> "
                "to be sure. Connecting anyway."
            )

    # Each player has its own listener port: base_port + (player - 1).
    port = args.port + (args.player - 1)
    client = UE5Client(host=args.host, port=port, screenshot_quality=args.quality)
    if not client.connect():
        raise SystemExit(
            f"Failed to connect to UE5 at {args.host}:{port} (player {args.player}). "
            "Is the game running, and does it have that many players?"
        )
    logger.info("Connected to UE5 at %s:%d as player %d", args.host, port, args.player)

    registry = load_map_registry()

    if args.map:
        target = resolve_map(args.map, registry)
        client.open_map(target)
        time.sleep(3.0)
        if not client.connected:
            client.reconnect()
            time.sleep(0.5)
        logger.info("Opened map %s", target)

    # Manual play runs in real time; undo any leftover benchmark pause (slomo 0).
    client.resume_world()

    app = ManualControlApp(
        client, mode=f"P{args.player}", host=args.host, port=port,
        fps=args.fps, width=args.width, height=args.height,
        map_registry=registry,
    )

    # Keyboard + mouse are always active; add a gamepad too if one is present.
    # Both forward to the same client on the Tk loop and drive UE5 together.
    backends: list[_InputBackend] = [
        KeyboardMouseBackend(app, args.sensitivity, args.invert_look)
    ]
    try:
        backends.append(GamepadBackend(app, args))
    except Exception as e:
        logger.info("Gamepad off (keyboard+mouse only): %s", e)

    active = "+".join(
        "keyboard" if isinstance(b, KeyboardMouseBackend) else "gamepad" for b in backends
    )
    app.set_mode_label(f"P{args.player} {active}")
    app.set_backends(backends)
    for backend in backends:
        backend.attach()
    logger.info("Manual control ready (%s). Close the window or Ctrl-C to quit.", active)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
