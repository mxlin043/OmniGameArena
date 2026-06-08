"""Solo environment for single-agent testing."""

import io
import time
import logging

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

from .base import BaseEnv, Observation, Info
from .client_ue5 import UE5Client
from ..adapters.base import BaseActionAdapter

logger = logging.getLogger(__name__)


class SoloEnv(BaseEnv):
    """Single-agent environment backed by UE5 RemoteInput."""

    def __init__(
        self,
        adapter: BaseActionAdapter,
        host: str = "127.0.0.1",
        port: int = 12345,
        task: str = "",
        max_steps: int = 0,
        screenshot_quality: int = 85,
        reload: bool = False,
        obs_delay: float = 0.0,
        map: str = "",
    ):
        self.adapter = adapter
        self.host = host
        self.port = port
        self.task = task
        self.max_steps = max_steps
        self.screenshot_quality = screenshot_quality
        self.reload = reload
        self.obs_delay = obs_delay
        self.map = map

        self.client: UE5Client | None = None
        self.observation_provider = None
        self.step_count = 0
        self.start_time = 0.0
        self.max_score_seen: float | None = None
        self.world_paused = False

    def reset(self) -> Observation:
        """Connect to UE5 and return initial observation."""
        if self.client:
            self.client.disconnect()

        self.client = UE5Client(
            host=self.host,
            port=self.port,
            screenshot_quality=self.screenshot_quality,
        )
        if not self.client.connect():
            raise RuntimeError(
                f"Failed to connect to UE5 at {self.host}:{self.port}"
            )

        if self.map:
            self.client.open_map(self.map)
            time.sleep(3)
            if not self.client.connected:
                logger.warning(
                    "UE5 connection closed during map switch; reconnecting before initial observation"
                )
                if not self.client.reconnect():
                    raise RuntimeError(
                        f"Failed to reconnect to UE5 at {self.host}:{self.port} after map switch"
                    )
                time.sleep(0.5)
            logger.info("Map switched to %s", self.map)
        elif self.reload:
            self.client.reset_level()
            time.sleep(3)  # wait for map reload
            logger.info("Map reloaded")

        self.resume()
        self.step_count = 0
        self.start_time = time.time()
        self.max_score_seen = None

        logger.info("Environment reset. Task: %s", self.task)
        return self._make_observation()

    def step(
        self,
        action: dict,
        *,
        pause_before_observe: bool = False,
    ) -> tuple[Observation, float, bool, Info]:
        """Execute action and return (obs, reward, done, info)."""
        # The UE5 world keeps running while the model thinks. If the character
        # falls during that latency window, do not execute the newly proposed
        # action and accidentally attribute the terminal state to it.
        try:
            if not self.client.game_over:
                self.client.check_game_over(timeout=0.2)
        except Exception:
            pass

        if self.client.game_over:
            elapsed = time.time() - self.start_time
            obs = self._make_observation()

            try:
                self.client.get_score()
            except Exception:
                pass
            self._update_max_score_seen(self.client.score)

            info = {
                "step": self.step_count,
                "elapsed": elapsed,
                "action": action,
                "done_reason": "game_over",
                "score": self.client.score,
                "survival_time": self.client.survival_time,
                "character_position": self.client.character_position,
                "max_score_seen": self.max_score_seen,
                "action_executed": False,
                "terminal_timing": "before_action",
            }
            logger.info(
                "Episode already done before executing step %d action (%.1fs), "
                "reason: %s, score: %s",
                self.step_count + 1, elapsed, info["done_reason"],
                self.client.score,
            )
            return obs, 0.0, True, info

        action_game_time_s = 0.0
        try:
            if pause_before_observe:
                self.resume()

            t_game = time.perf_counter()
            # Execute action through adapter
            self.adapter.execute(self.client, action)

            # Wait for game state to settle before capturing observation
            if self.obs_delay > 0:
                time.sleep(self.obs_delay)
            action_game_time_s = time.perf_counter() - t_game
        finally:
            if pause_before_observe:
                self.pause()

        self.step_count += 1
        elapsed = time.time() - self.start_time

        # Capture observation - screenshot still works after game_over,
        # game_over lines are drained silently and the flag is set on client.
        obs = self._make_observation()

        # reward is 0.0 placeholder - computed externally by eval layer
        reward = 0.0

        # Poll score throughout the episode, not only at the terminal step.
        # Some scenes expose progress while playing but report a reset/zero
        # score after the timeout overlay appears; keeping the max preserves
        # partial progress such as CueChase's completed-task count.
        try:
            self.client.get_score()
        except Exception:
            pass
        self._update_max_score_seen(self.client.score)

        # Some UE5 scenes do not push an async game_over event during
        # screenshots, so actively poll the request-response endpoint too.
        try:
            if not self.client.game_over:
                self.client.check_game_over(timeout=0.5)
        except Exception:
            pass

        # Check game_over flag (set by screenshot push or active poll above)
        game_over = self.client.game_over
        agent_done = action.get("done", False)
        step_limit = self.max_steps > 0 and self.step_count >= self.max_steps
        done = agent_done or step_limit or game_over

        if game_over:
            done_reason = "game_over"
        elif agent_done:
            done_reason = "agent"
        elif step_limit:
            done_reason = "max_steps"
        else:
            done_reason = None

        # On terminal step, explicitly query UE5 for the final score.
        # `get_score` is game-mode specific: score=1.0 means the player
        # reached the finish/win condition; 0 means unimplemented.
        if done:
            try:
                self.client.get_score()
            except Exception:
                pass
            self._update_max_score_seen(self.client.score)

        info = {
            "step": self.step_count,
            "elapsed": elapsed,
            "action": action,
            "done_reason": done_reason,
            "score": self.client.score,
            "survival_time": self.client.survival_time,
            "character_position": self.client.character_position,
            "max_score_seen": self.max_score_seen,
            "action_executed": True,
            "terminal_timing": "after_action" if done else None,
            "action_game_time_s": round(action_game_time_s, 6),
        }

        if done:
            logger.info(
                "Episode done after %d steps (%.1fs), reason: %s, score: %s",
                self.step_count, elapsed, info["done_reason"], self.client.score,
            )

        return obs, reward, done, info

    def _update_max_score_seen(self, score):
        if score is None:
            return
        try:
            value = float(score)
        except (TypeError, ValueError):
            return
        if self.max_score_seen is None or value > self.max_score_seen:
            self.max_score_seen = value

    def observe(self) -> Observation:
        """Take observation without acting."""
        return self._make_observation()

    def pause(self):
        """Freeze UE simulation with idempotent ``slomo 0``."""
        client = self._require_client()
        client.pause_world()
        self.world_paused = True

    def resume(self):
        """Resume UE simulation with idempotent ``slomo 1``."""
        client = self._require_client()
        client.resume_world()
        self.world_paused = False

    def advance_game_time(self, seconds: float, *, pause_after: bool = True):
        """Run UE world for a controlled duration, then optionally pause.

        This is the primitive that LCRT can use later to inject calibrated or
        fixed reaction delay while still keeping API/network wait outside the
        simulated game timeline.
        """
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.resume()
        try:
            if seconds > 0:
                time.sleep(seconds)
        finally:
            if pause_after:
                self.pause()
            else:
                self.world_paused = False

    def sleep_game_time(self, seconds: float, *, pause_after: bool = True):
        """Alias for ``advance_game_time`` used by latency-control runners."""
        self.advance_game_time(seconds, pause_after=pause_after)

    def close(self):
        """Release held keys and disconnect from UE5."""
        if self.client:
            if hasattr(self.adapter, 'release_all'):
                self.adapter.release_all(self.client)
            try:
                self.resume()
            except Exception:
                pass
            self.client.disconnect()
            self.client = None
            self.world_paused = False
        logger.info("Environment closed")

    def _make_observation(self) -> Observation:
        """Capture screenshot and build observation dict."""
        if self.observation_provider is not None:
            obs = self.observation_provider()
            if obs is not None:
                return obs

        result = self.client.screenshot()
        jpeg_data = result["data"]

        # Decode JPEG to PIL Image (force immediate decode, avoid lazy loading issues)
        image = Image.open(io.BytesIO(jpeg_data))
        image.load()

        return {
            "image": image,
            "width": result["width"],
            "height": result["height"],
            "timestamp": time.time(),
        }

    def _require_client(self) -> UE5Client:
        if self.client is None or not self.client.connected:
            raise RuntimeError("Environment is not connected to UE5")
        return self.client
