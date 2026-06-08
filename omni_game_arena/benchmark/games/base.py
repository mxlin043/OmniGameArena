"""GameSpec - per-game plugin contract for the benchmark runner.

Each game is a small class carrying:
  - ``name``          : the YAML ``game:`` key (snake_case).
  - ``prompt_key``    : argument passed to ``omni_game_arena.prompts.load_game_prompt``
                        - typically the CamelCase scene name, e.g. ``"ObstacleRun2D"``.
  - ``default_task``  : natural-language instruction used when the YAML
                        omits ``env.task``.

Hooks (all default to no-op so trivial games only override what they need):
  - ``extract_terminal_metrics(info)`` - turn the final step's info dict
    into game-specific signals (e.g. ``finished``, ``done_reason``).
  - ``aggregate_episode_metrics(eps)`` - reduce per-episode metric dicts
    into a cell-level dict (e.g. ``success_rate``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class GameSpec:
    name: str
    prompt_key: str
    default_task: str
    mode: Literal["solo", "pvp", "coop"] = "solo"
    num_agents: int = 1
    # How a coop match-level score should be derived from per-player UE
    # get_score reads. "shared" means every player port reports the same
    # team score; "sum" means each port reports that player's contribution.
    coop_score_aggregation: Literal["shared", "sum"] = "shared"
    # Canonical UE5 map for this game. Solo configs may still override
    # env.map explicitly; multi runners can use this as the default map.
    map: str = ""
    # Optional per-player prompt keys. PvP/Coop games can provide player
    # 0/1 prompts without baking color names like red/blue into the API.
    player_prompt_keys: tuple[str, ...] = ()
    # Which mouse axes the game actually uses. The adapter exposes
    # exactly these to the VLM prompt, and the parser only reads this
    # many numeric tokens. Examples:
    #   ("X", "Y", "Z") - full camera + scroll (default)
    #   ("X",)          - horizontal-only camera (e.g. ObstacleRun3D)
    #   ()              - no mouse at all (pure 2D side-scroller)
    mouse_axes: tuple[str, ...] = ("X", "Y", "Z")

    # Game-specific key bindings {VLM_KEY_NAME: semantic description}.
    # When ``None``, the adapter falls back to its own defaults
    # (WASD + Space). Override per game to trim/rename the action
    # vocabulary (e.g. ObstacleRun2D only has A / D / Space).
    key_bindings: dict | None = None

    # Keys that should be pulsed on every chunk step where they appear,
    # instead of being held across consecutive steps. This is useful for
    # edge-triggered interaction keys such as CookHouse's F key.
    tap_keys: tuple[str, ...] = ()

    # Chunk length for Lumine-style chunked actions, per game. Different
    # games can prefer different chunk depths (e.g. a slow platformer
    # might want 8, a twitchy shooter might want 4).
    chunk_steps: int = 8

    # Seconds to wait after a chunk finishes before capturing the next
    # observation for the VLM. Useful when the character is still
    # airborne / sliding when the chunk ends - e.g. a jump in
    # ObstacleRun2D takes ~500ms past the chunk boundary to land.
    # Ablation YAML can still override via ``params.obs_delay``.
    obs_delay: float = 0.0

    def prompt_key_for_player(self, player_index: int | None = None) -> str:
        """Return the prompt key for a specific player.

        Solo callers can omit ``player_index`` and receive ``prompt_key``.
        Multi-agent callers pass 0-based player indices and get the matching
        per-player prompt key when the game provides one.
        """
        if player_index is None:
            return self.prompt_key
        if player_index < 0:
            raise ValueError(
                f"Player index {player_index} is out of range for "
                f"{self.name} prompts"
            )
        if self.player_prompt_keys:
            try:
                return self.player_prompt_keys[player_index]
            except IndexError as exc:
                raise ValueError(
                    f"Player index {player_index} is out of range for "
                    f"{self.name} prompts"
                ) from exc
        return self.prompt_key

    def extract_terminal_metrics(self, terminal_info: dict) -> dict[str, Any]:
        """Pull game-specific fields out of the last step's info dict."""
        return {}

    def aggregate_episode_metrics(
        self, episode_metrics: list[dict]
    ) -> dict[str, Any]:
        """Produce cell-level aggregates from a list of episode metric dicts.

        Each entry in ``episode_metrics`` already contains a ``game``
        sub-dict produced by ``extract_terminal_metrics``.
        """
        return {}
