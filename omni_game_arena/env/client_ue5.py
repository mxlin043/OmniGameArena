"""
UE5 RemoteInput TCP Client

Clean TCP client for communicating with UE5 RemoteInput plugin.
Handles keyboard/mouse/gamepad input and screenshot capture.

Protocol:
    - Commands: line-delimited JSON (\\n terminated)
    - Screenshot response: JSON header line + binary JPEG data
    - Default port: 12345
"""

import socket
import json
import time
import threading
import logging

logger = logging.getLogger(__name__)


class GameOverError(Exception):
    """Raised when UE5 reports game over."""
    def __init__(self, reason: str = "unknown"):
        self.reason = reason
        super().__init__(f"Game over: {reason}")


class UE5Client:
    """TCP client for UE5 RemoteInput plugin.

    Thread safety: all request-response operations (save, load, reset,
    check_game_over, get_score, screenshot) are fully atomic - the
    command send and response read happen inside the same lock.  This
    prevents response desync when a viewer thread streams screenshots
    through the same client.

    Fire-and-forget commands (send_key, send_mouse_move, etc.) only
    acquire _send_lock briefly and never read from the socket.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 12345,
        screenshot_quality: int = 85,
        screenshot_timeout: float = 10.0,
        request_timeout: float = 2.0,
        welcome_timeout: float = 1.0,
    ):
        self.host = host
        self.port = port
        self.screenshot_quality = screenshot_quality
        self.screenshot_timeout = screenshot_timeout
        self.request_timeout = request_timeout
        self.welcome_timeout = welcome_timeout

        self.sock: socket.socket | None = None
        self.connected = False
        self.game_over = False
        self.score: float | None = None
        self.survival_time: float | None = None
        self.score_payload: dict | None = None
        self.character_position: dict[str, float] | None = None
        self.player_index: int | None = None
        self._game_over_check_supported: bool | None = None
        self._game_over_check_timeouts = 0
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()
        self._recv_buffer = b""

    def connect(self) -> bool:
        """Connect to UE5 RemoteInput server."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.connected = True
            self.game_over = False
            self._game_over_check_supported = None
            self._game_over_check_timeouts = 0

            # Some builds accept the socket but do not send an initial
            # banner. Treat the banner as optional so connect() cannot
            # hang before the caller gets to send commands.
            self.sock.settimeout(self.welcome_timeout)
            try:
                data = self.sock.recv(4096)
                if data:
                    self._recv_buffer = data
                    if b"\n" in self._recv_buffer:
                        idx = self._recv_buffer.index(b"\n")
                        welcome = self._recv_buffer[:idx].decode("utf-8").strip()
                        self._recv_buffer = self._recv_buffer[idx + 1 :]
                        logger.info("Server response: %s", welcome)
                    else:
                        logger.info(
                            "Server response: %s",
                            data.decode("utf-8").strip(),
                        )
                        self._recv_buffer = b""
            except socket.timeout:
                logger.info("No welcome message from server; continuing")
            finally:
                self.sock.settimeout(None)

            logger.info("Connected to %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Connection failed: %s", e)
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from server."""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self._recv_buffer = b""
        self._game_over_check_supported = None
        self._game_over_check_timeouts = 0
        logger.info("Disconnected")

    def reconnect(self) -> bool:
        """Disconnect and reconnect."""
        self.disconnect()
        return self.connect()

    # -- Fire-and-forget commands (no response expected) --------------

    def send_command(self, cmd: dict):
        """Send a JSON command (thread-safe)."""
        if not self.connected:
            return
        try:
            line = json.dumps(cmd, ensure_ascii=False) + "\n"
            with self._send_lock:
                self.sock.sendall(line.encode("utf-8"))
        except Exception as e:
            logger.error("Send failed: %s", e)
            self.connected = False

    def _send_raw(self, cmd: dict):
        """Send a JSON command (caller must already hold a lock that
        prevents interleaving; used inside _recv_lock sections)."""
        if not self.connected:
            return
        try:
            line = json.dumps(cmd, ensure_ascii=False) + "\n"
            with self._send_lock:
                self.sock.sendall(line.encode("utf-8"))
        except Exception as e:
            logger.error("Send failed: %s", e)
            self.connected = False

    def send_key(self, key: str, pressed: bool):
        """Send key press/release event."""
        self.send_command({
            "type": "key",
            "key": key,
            "event": "pressed" if pressed else "released",
        })

    def send_mouse_move(self, dx: float, dy: float):
        """Send relative mouse movement."""
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return
        self.send_command({"type": "mouse", "dx": dx, "dy": dy})

    def send_mouse_button(self, button: str, pressed: bool):
        """Send mouse button press/release. button: 'left', 'right', 'middle'."""
        self.send_command({
            "type": "mouse_button",
            "button": button,
            "event": "pressed" if pressed else "released",
        })

    def send_axis(self, axis: str, value: float):
        """Send axis input (e.g. gamepad stick)."""
        self.send_command({"type": "axis", "axis": axis, "value": value})

    def console_command(self, cmd: str):
        """Send a UE5 console command (fire-and-forget).

        The RemoteInput plugin forwards this to ``GEngine->Exec(World, ...)``
        on the game thread. Payload shape must match what the plugin
        parses: ``{"type": "console", "command": "..."}``.
        """
        self.send_command({"type": "console", "command": cmd})

    def set_time_scale(self, scale: float):
        """Set UE world time scale via the ``slomo`` console command.

        This is intentionally fire-and-forget like ``console_command``. Use
        ``pause_world`` and ``resume_world`` for the benchmark pause protocol:
        unlike the UE ``pause`` toggle, repeated ``slomo 0`` or ``slomo 1``
        commands keep the world in the requested state.
        """
        self.console_command(f"slomo {scale:g}")

    def pause_world(self):
        """Freeze UE world simulation with an idempotent ``slomo 0`` command."""
        self.set_time_scale(0.0)

    def resume_world(self):
        """Resume normal UE world simulation with ``slomo 1``."""
        self.set_time_scale(1.0)

    def advance_game_time(self, seconds: float, *, pause_after: bool = True):
        """Resume the UE world, wait, then optionally freeze it again.

        With the current ``slomo``-based pause protocol, normal speed means
        one wall-clock second corresponds to roughly one game second. This is
        a low-level primitive for latency-controlled evaluation; higher-level
        runners decide the actual delay value.
        """
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.resume_world()
        if seconds > 0:
            time.sleep(seconds)
        if pause_after:
            self.pause_world()

    def sleep_game_time(self, seconds: float, *, pause_after: bool = True):
        """Alias for ``advance_game_time`` used by latency-control callers."""
        self.advance_game_time(seconds, pause_after=pause_after)

    def open_map(self, map_name: str):
        """Debug helper: switch the active level.

        Accepts either a short name (``MyMap``) or a full package path
        (``/Game/Maps/MyMap``). Anything without a slash is prefixed with
        ``/Game/Maps/`` automatically.
        """
        path = map_name if "/" in map_name else f"/Game/Maps/{map_name}"
        self.console_command(f"open {path}")
        self.game_over = False
        self._game_over_check_supported = None
        self._game_over_check_timeouts = 0

    # -- Request-response commands (atomic send+receive) -------------

    def save_state(self):
        """Save current game state. Waits for server response."""
        with self._recv_lock:
            self._send_raw({"type": "save"})
            self._drain_response_inner("Save")

    def load_state(self):
        """Load saved game state. Waits for server response. Clears game_over flag."""
        self.game_over = False
        self._game_over_check_supported = None
        self._game_over_check_timeouts = 0
        with self._recv_lock:
            self._send_raw({"type": "load"})
            self._drain_response_inner("Load")

    def _drain_response_inner(self, label: str):
        """Read and discard a server response (caller must hold _recv_lock)."""
        old_timeout = self._begin_request_timeout_inner()
        try:
            line = self._recv_line()
            result = json.loads(line)
            # If server sent a screenshot response, drain the binary JPEG data too
            if result.get("type") == "screenshot" and "size" in result:
                self._recv_exact(result["size"])
                logger.info("%s result: screenshot (%d bytes drained)", label, result["size"])
            else:
                logger.info("%s result: %s", label, result)
        except socket.timeout:
            logger.warning(
                "%s response timed out after %.1fs",
                label, self.request_timeout,
            )
        except Exception as e:
            logger.warning("Failed to read %s response: %s", label, e)
        finally:
            self._restore_timeout_inner(old_timeout)

    def check_game_over(self, timeout: float | None = None) -> bool:
        """Actively query UE5 whether the character can still act.

        Sends {"type": "game_over_check"} and expects
        {"type": "game_over_check", "game_over": true/false, "player_index": N}.
        game_over=true means the character cannot act anymore (fell OR finished -
        the distinction is captured by `get_score`: score=1.0 means finished).
        """
        if not self.connected:
            return self.game_over
        if self._game_over_check_supported is False:
            return self.game_over

        with self._recv_lock:
            self._send_raw({"type": "game_over_check"})
            old_timeout = self._begin_request_timeout_inner(timeout)
            try:
                line = self._recv_line()
                result = json.loads(line)
                self._game_over_check_supported = True
                self._game_over_check_timeouts = 0
                if "player_index" in result:
                    self.player_index = result.get("player_index")
                if result.get("game_over"):
                    self.game_over = True
                    logger.info("Game over check: TRUE")
                else:
                    logger.debug("Game over check: FALSE")
            except socket.timeout:
                logger.warning(
                    "game_over_check timed out after %.1fs",
                    timeout or self.request_timeout,
                )
                self._game_over_check_timeouts += 1
                if (
                    self._game_over_check_supported is None
                    and self._game_over_check_timeouts >= 3
                ):
                    self._game_over_check_supported = False
            except Exception as e:
                logger.warning("Failed to read game_over_check response: %s", e)
            finally:
                self._restore_timeout_inner(old_timeout)
        return self.game_over

    def get_score(self, player_index: int | None = None) -> float:
        """Query the current score from UE5. Safe to call anytime during gameplay.

        Sends {"type": "get_score"} and expects a JSON object containing
        {"score": float}. Some games include extra metric fields such as
        {"survival_time": float} or {"character_position": {"x": ..., ...}}.
        The returned value is game-mode specific; unimplemented games return 0.0.
        Updates self.score, optional metric fields, caches the full payload,
        and returns the score.
        """
        if not self.connected:
            return self.score if self.score is not None else 0.0

        msg: dict = {"type": "get_score"}
        if player_index is not None:
            msg["player_index"] = player_index

        with self._recv_lock:
            self._send_raw(msg)
            old_timeout = self._begin_request_timeout_inner()
            try:
                line = self._recv_line()
                result = json.loads(line)
                self.score_payload = result
                self.score = float(result.get("score", 0.0))
                if "survival_time" in result:
                    self.survival_time = float(result["survival_time"])
                else:
                    self.survival_time = None
                self.character_position = self._parse_vector3(
                    result.get("character_position", result.get("position"))
                )
                if "player_index" in result:
                    self.player_index = result.get("player_index")
                logger.debug("Score query: %.4f", self.score)
            except socket.timeout:
                logger.warning(
                    "get_score timed out after %.1fs",
                    self.request_timeout,
                )
                return self.score if self.score is not None else 0.0
            except Exception as e:
                logger.warning("Failed to read get_score response: %s", e)
                return self.score if self.score is not None else 0.0
            finally:
                self._restore_timeout_inner(old_timeout)
        return self.score

    def reset_level(self):
        """Send reset command to reload the current map. Waits for server response."""
        self.game_over = False
        self.score = None
        self.survival_time = None
        self.score_payload = None
        self.character_position = None
        self._game_over_check_supported = None
        self._game_over_check_timeouts = 0
        with self._recv_lock:
            self._send_raw({"type": "reset"})
            old_timeout = self._begin_request_timeout_inner()
            try:
                line = self._recv_line()
                result = json.loads(line)
                logger.info("Reset result: %s", result)
            except socket.timeout:
                logger.warning("reset timed out after %.1fs", self.request_timeout)
            except Exception as e:
                logger.warning("Failed to read reset response: %s", e)
            finally:
                self._restore_timeout_inner(old_timeout)

    # -- Low-level recv helpers --------------------------------------

    def _begin_request_timeout_inner(self, timeout: float | None = None):
        """Set a finite timeout for a request-response read.

        Caller must hold _recv_lock so no other operation observes the
        temporary socket timeout.
        """
        if not self.sock:
            return None
        old_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout or self.request_timeout)
        return old_timeout

    def _restore_timeout_inner(self, old_timeout):
        if self.sock:
            try:
                self.sock.settimeout(old_timeout)
            except Exception:
                pass

    @staticmethod
    def _parse_vector3(value) -> dict[str, float] | None:
        if not isinstance(value, dict):
            return None

        out: dict[str, float] = {}
        for key in ("x", "y", "z"):
            raw = value.get(key, value.get(key.upper()))
            if raw is None:
                return None
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                return None
        return out

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes from socket (buffered)."""
        while len(self._recv_buffer) < n:
            chunk = self.sock.recv(max(4096, n - len(self._recv_buffer)))
            if not chunk:
                raise ConnectionError("Connection closed")
            self._recv_buffer += chunk
        data = self._recv_buffer[:n]
        self._recv_buffer = self._recv_buffer[n:]
        return data

    def _recv_line(self) -> str:
        """Read one JSON line from socket (buffered).

        Handles binary JPEG data that may leak into the buffer when the
        server reports a screenshot size smaller than the actual data.
        The leaked bytes can be concatenated with the next JSON response
        on the same "line" (no \\n between them), so we scan each segment
        for embedded '{"' to recover the JSON part.
        """
        while True:
            while b"\n" not in self._recv_buffer:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed")
                self._recv_buffer += chunk

            idx = self._recv_buffer.index(b"\n")
            raw = self._recv_buffer[:idx]
            self._recv_buffer = self._recv_buffer[idx + 1 :]

            if not raw.strip():
                continue

            # Try each '{"' position as a potential JSON start.
            # This handles: pure JSON, binary prefix + JSON, and
            # false '{"' in binary data (try next position).
            search_from = 0
            while search_from < len(raw):
                json_pos = raw.find(b'{"', search_from)
                if json_pos < 0:
                    break
                try:
                    return raw[json_pos:].decode("utf-8")
                except UnicodeDecodeError:
                    search_from = json_pos + 2

    # -- Screenshot --------------------------------------------------

    def screenshot(
        self, quality: int | None = None, timeout: float | None = None
    ) -> dict:
        """
        Capture a screenshot from UE5.

        Args:
            quality: JPEG quality 1-100 (default: self.screenshot_quality)
            timeout: Timeout in seconds (default: self.screenshot_timeout)

        Returns:
            dict: {"width": int, "height": int, "format": str, "data": bytes}
        """
        if not self.connected:
            raise RuntimeError("Not connected to server")

        quality = quality or self.screenshot_quality
        timeout = timeout or self.screenshot_timeout

        with self._recv_lock:
            old_timeout = self.sock.gettimeout()
            self.sock.settimeout(timeout)
            try:
                # Send ONE screenshot request
                self._send_raw({"type": "screenshot", "quality": quality})

                # Read response lines until we get a screenshot header.
                # Handles: game_over events, "already pending" (wait for
                # the pending response instead of sending more requests),
                # and stale responses from other commands (game_over_check,
                # score, etc.)
                header = None
                for _ in range(30):
                    header_line = self._recv_line()
                    header = json.loads(header_line)

                    if header.get("type") == "game_over":
                        self.game_over = True
                        logger.info("Game over received")
                        continue

                    if header.get("type") == "screenshot_error":
                        error_msg = header.get("error", "unknown")
                        if "pending" in error_msg:
                            # Server is still rendering a previous screenshot.
                            # Don't send another request - just wait for the
                            # pending response to arrive.
                            logger.debug("Screenshot pending, waiting for response...")
                            continue
                        raise RuntimeError(
                            f"Screenshot error from server: {error_msg}"
                        )

                    if header.get("type") == "screenshot":
                        break

                    # Skip unexpected stale responses (game_over_check, score, etc.)
                    logger.debug("Skipping stale response during screenshot: %s",
                                 header.get("type"))
                    continue
                else:
                    raise RuntimeError("Too many unexpected responses during screenshot")

                # Read JPEG binary data
                size = header["size"]
                jpeg_data = self._recv_exact(size)

                return {
                    "width": header["width"],
                    "height": header["height"],
                    "format": header.get("format", "jpeg"),
                    "data": jpeg_data,
                }
            except socket.timeout:
                raise TimeoutError(
                    f"Screenshot timed out ({timeout}s). Is the game window active?"
                )
            finally:
                try:
                    self.sock.settimeout(old_timeout)
                except Exception:
                    pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
