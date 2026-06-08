"""Experiment configuration loader & matrix expander.

YAML shape
----------
    game: <name>                     # required - picks a GameSpec
    router_config: configs/router.yaml # optional; defaults to configs/router.yaml
    extends:                         # optional - single string OR list
      - ../bases/lumine_main.yaml
      - ./env_defaults.yaml

    env:
      host: 127.0.0.1
      port: 12345
      task: "..."                    # optional, falls back to GameSpec.default_task
      max_steps: 220
      screenshot_quality: 85
      map: obstacle_run_2d
      map_wait: 3.0
      obs_delay: 0.0

    agents_defaults:                 # merged into every entry in `agents`
      kind: lumine

    agents:                          # axis A
      - { name: ..., kind: ..., model: ..., extra: {...} }

    params:                          # axis B - cartesian product
      history_len: [10]              #   list -> sweep
      temperature: 0.3               #   scalar -> single value
      ...

    episodes_per_cell: 3             # axis C

Inheritance semantics
---------------------
``extends`` is resolved depth-first: each parent path is loaded with its
own ``extends`` chain applied first, then parents are merged left-to-right
(later overrides earlier), and finally the child YAML overrides them all.
This lets scene YAMLs mixin a ``lumine_main`` base and a per-game env base
independently.

The expander produces plain dataclasses - no I/O, no side effects - so
it's trivial to unit-test and dry-run.
"""

from __future__ import annotations

import copy
import itertools
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


# -- Data classes --------------------------------------------------------

@dataclass
class EnvSpec:
    host: str = "127.0.0.1"
    port: int = 12345
    task: str = ""
    max_steps: int = 220
    screenshot_quality: int = 85
    # UE5 map to switch to on reset (via console ``open /Game/Maps/<name>``).
    # Empty = don't switch maps; whatever scene UE5 currently has stays.
    # Accepts a short name (``ObstacleRun2D``) or a full package path.
    map: str = ""
    # Seconds to wait after opening ``map`` before taking the first
    # observation. Defaults to the historical benchmark behavior.
    map_wait: float = 3.0
    # ``None`` -> fall through to ``GameSpec.obs_delay``. Use explicit 0.0
    # to override a non-zero game default (e.g. disable the landing wait
    # in obstacle_run_2d). Don't use ``or`` - 0.0 is falsy.
    obs_delay: float | None = None


@dataclass
class AgentProfile:
    """
    One agent under test.

    ``model`` is the single identifier - both the API model name (e.g.
    ``claude-opus-4-6``) and the slug used in output dirs / logs.

    ``kind`` picks the agent class:
        - ``vlm``      : ``omni_game_arena.models.VLMAgent`` (Backend x MethodStyle).
        - ``openp2p``  : ``omni_game_arena.models.OpenP2PAgent`` (OpenP2P policy).
        - ``nitrogen`` : ``omni_game_arena.models.NitroGenAgent`` (NitroGen policy).

    ``method`` selects the VLM output style (only meaningful when
    ``kind == "vlm"``):
        - ``lumine``    : main-table chunked-action format (default).

    ``extra`` passes arbitrary kwargs through to the constructor - most
    commonly ``base_url`` / ``api_key`` for OpenAI-compatible VLMs, or
    ``url`` / ``timeout`` for the policy-server agents.
    """
    model: str
    kind: str = "vlm"
    method: str = "lumine"
    extra: dict = field(default_factory=dict)
    # Markdown snippets injected into VLM prompts as reusable play experience.
    # Policy agents (openp2p / nitrogen) ignore these.
    prompt_skills: list[str] = field(default_factory=list)


@dataclass
class PlayerSpec:
    """One player in a two-player PvP/Coop match."""

    player_index: int
    agent: AgentProfile
    host: str = "127.0.0.1"
    port: int = 12345
    task: str = ""


@dataclass
class ParamsPoint:
    """A single point in the params matrix.

    Defaults reflect the **main table** baseline: Lumine-style chunked
    agent at 512-px resolution with a 5-frame history (+ current), no
    token compression, game-prompt enabled.
    """
    history_len: int = 5
    # Number of most-recent historical VLM responses whose reasoning text
    # is kept in the packed prompt. 0 = keep only compact action chunks.
    history_reasoning_len: int = 0
    temperature: float | None = 0.3
    resize_size: int = 512
    hold_duration: float = 0.2
    with_game_prompt: bool = True
    # Whether Lumine-style prompts ask the model to include a short reasoning
    # sentence before the action. False requests action-only output.
    with_reasoning: bool = True
    # ``None`` -> fall through to EnvSpec / GameSpec. Explicit 0.0 overrides
    # a non-zero game default. Don't use ``or`` - 0.0 is falsy.
    obs_delay: float | None = None
    # Chunk length for Lumine-style chunked adapter.
    # ``None`` -> fall through to ``GameSpec.chunk_steps`` (per-game default,
    # e.g. 8 for obstacle_run_2d). Set a list to sweep: ``[4, 8, 16, 32]``.
    chunk_steps: int | None = None
    # FramePack-style token compression for history frames (see
    # ``benchmark.frame_pack``). Kernel values:
    #   "none" | "geometric" | "level_duplication"
    #   | "temporal_kernel" | "important_start"
    frame_pack: str = "none"
    frame_pack_min_size: int = 112

    def short_id(self) -> str:
        """Compact string used in output dir names."""
        gp = "gp" if self.with_game_prompt else "nogp"
        t = "none" if self.temperature is None else f"{self.temperature:g}"
        r = "native" if self.resize_size <= 0 else str(self.resize_size)
        if self.frame_pack == "none":
            fp = ""
        else:
            alias = {
                "geometric": "geo",
                "level_duplication": "lvl",
                "temporal_kernel": "tk",
                "important_start": "istart",
            }.get(self.frame_pack, self.frame_pack)
            fp = f"_fp-{alias}"
        cs = f"_cs{self.chunk_steps}" if self.chunk_steps is not None else ""
        hr = (
            f"_hr{self.history_reasoning_len}"
            if self.history_reasoning_len > 0
            else ""
        )
        od = "default" if self.obs_delay is None else f"{self.obs_delay:g}"
        reasoning = "" if self.with_reasoning else "_noreason"
        return (
            f"h{self.history_len}_t{t}_r{r}"
            f"_hd{self.hold_duration:g}_od{od}_{gp}{reasoning}{fp}{cs}{hr}"
        )


@dataclass
class Experiment:
    """One concrete run: game + env + agent + params + seed/episode."""
    game: str
    env: EnvSpec
    agent: AgentProfile
    params: ParamsPoint
    episode_idx: int
    run_id: str

    def to_dict(self) -> dict:
        d = {
            "game": self.game,
            "env": asdict(self.env),
            "agent": asdict(self.agent),
        }
        # VLM param knobs only matter for VLM agents; policy agents
        # (nitrogen / openp2p) are configured entirely via agent.extra,
        # so omit the irrelevant params block for them.
        if self.agent.kind == "vlm":
            d["params"] = asdict(self.params)
        d["episode_idx"] = self.episode_idx
        d["run_id"] = self.run_id
        return d


# -- Loading -------------------------------------------------------------

@dataclass
class TwoPlayerExperiment:
    """One concrete two-player match run."""

    game: str
    env: EnvSpec
    players: list[PlayerSpec]
    params: ParamsPoint
    episode_idx: int
    run_id: str

    def to_dict(self) -> dict:
        return {
            "game": self.game,
            "env": asdict(self.env),
            "players": [_player_spec_output_dict(p) for p in self.players],
            "params": asdict(self.params),
            "episode_idx": self.episode_idx,
            "run_id": self.run_id,
        }


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge - override takes precedence."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_benchmark_config(path: str) -> dict:
    """Load YAML with multi-parent ``extends`` inheritance.

    ``extends`` may be a single path or a list of paths (relative to the
    current YAML). Parents are merged left-to-right; the child overrides
    all of them. Parents may themselves chain further ``extends``.
    """
    import yaml  # noqa: PLC0415

    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    parents = cfg.pop("extends", None)
    if parents is None:
        return cfg
    if isinstance(parents, str):
        parents = [parents]

    here = os.path.dirname(path)
    merged: dict = {}
    for p in parents:
        parent_path = p if os.path.isabs(p) else os.path.join(here, p)
        merged = _deep_merge(merged, load_benchmark_config(parent_path))
    return _deep_merge(merged, cfg)


# -- Endpoint routing -----------------------------------------------------

_ROUTER_CACHE: dict[str, Any] | None = None
_MAPS_CACHE: dict[str, Any] | None = None


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_router_config(path: str | bool | None = None) -> dict[str, Any]:
    """Load the shared endpoint router, if present.

    The router is intentionally optional: old benchmark YAMLs still work.
    Explicit per-agent ``extra`` values always win over router defaults.
    """
    global _ROUTER_CACHE
    if path is False:
        return {}
    if _ROUTER_CACHE is not None and path in (None, "", True):
        return _ROUTER_CACHE

    import yaml  # noqa: PLC0415

    raw_path = (
        os.getenv("OMNI_ARENA_ROUTER_CONFIG")
        or (path if isinstance(path, str) else None)
        or os.path.join(_repo_root(), "configs", "router.yaml")
    )
    router_path = (
        raw_path
        if os.path.isabs(raw_path)
        else os.path.join(_repo_root(), raw_path)
    )
    if not os.path.exists(router_path):
        if path in (None, "", True):
            _ROUTER_CACHE = {}
        return {}

    with open(router_path, "r", encoding="utf-8") as f:
        router = yaml.safe_load(f) or {}
    _apply_router_environment(router)
    if path in (None, "", True):
        _ROUTER_CACHE = router
    return router


def _load_maps_config(path: str | bool | None = None) -> dict[str, Any]:
    """Load the shared UE map registry, if present.

    Map resolution is optional: configs can still pass literal UE package
    paths directly in ``env.map``.
    """
    global _MAPS_CACHE
    if path is False:
        return {}
    if _MAPS_CACHE is not None and path in (None, "", True):
        return _MAPS_CACHE

    import yaml  # noqa: PLC0415

    raw_path = (
        os.getenv("OMNI_ARENA_MAPS_CONFIG")
        or (path if isinstance(path, str) else None)
        or os.path.join(_repo_root(), "configs", "maps.yaml")
    )
    maps_path = (
        raw_path
        if os.path.isabs(raw_path)
        else os.path.join(_repo_root(), raw_path)
    )
    if not os.path.exists(maps_path):
        if path in (None, "", True):
            _MAPS_CACHE = {}
        return {}

    with open(maps_path, "r", encoding="utf-8") as f:
        maps = yaml.safe_load(f) or {}
    if path in (None, "", True):
        _MAPS_CACHE = maps
    return maps


def resolve_map_name(value: str, maps_config: str | bool | None = None) -> str:
    """Resolve a map registry key to a UE package path.

    Unknown values are returned unchanged so ad-hoc literal map paths keep
    working during debugging.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""

    maps_cfg = _load_maps_config(maps_config)
    if not maps_cfg:
        return raw

    lookup = _build_map_lookup(maps_cfg)
    return lookup.get(raw) or lookup.get(raw.lower()) or raw


def _build_map_lookup(maps_cfg: dict[str, Any]) -> dict[str, str]:
    entries = maps_cfg.get("maps") if isinstance(maps_cfg, dict) else None
    if not isinstance(entries, dict):
        return {}

    lookup: dict[str, str] = {}
    for name, entry in entries.items():
        path = _map_entry_path(entry)
        if not path:
            continue
        _put_map_lookup(lookup, str(name), path)
    return lookup


def _map_entry_path(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("path") or "")
    return ""


def _put_map_lookup(lookup: dict[str, str], key: str, path: str) -> None:
    key = key.strip()
    if not key:
        return
    lookup[key] = path
    lookup[key.lower()] = path


def _env_value(name: str | None, default=None):
    if not name:
        return default
    value = os.getenv(str(name))
    return default if value in (None, "") else value


def _materialize_route_extra(route: dict | None, *, keep_unknown: bool) -> dict:
    if not isinstance(route, dict):
        return {}

    route = copy.deepcopy(route)
    out = {
        k: v
        for k, v in route.items()
        if not k.endswith("_env") and k not in {"description", "provider"}
    }

    for field in ("base_url", "api_key", "url"):
        env_name = route.get(f"{field}_env")
        env_val = _env_value(env_name)
        if env_val is not None and field not in out:
            out[field] = env_val

    if keep_unknown:
        return out

    allowed = {
        "base_url",
        "api_key",
        "request_model",
        "max_tokens",
        "enable_thinking",
        "request_timeout",
        "max_retries",
        "include_history_actions",
        "empty_response_max_attempts",
    }
    return {k: v for k, v in out.items() if k in allowed}


def _router_model_route(router: dict, model: str) -> dict:
    vlm = router.get("vlm") or {}
    models = vlm.get("models") if isinstance(vlm, dict) else {}
    if not isinstance(models, dict):
        return {}
    model_l = (model or "").lower()
    for key, route in models.items():
        if str(key).lower() == model_l:
            return route or {}
        aliases = route.get("aliases", []) if isinstance(route, dict) else []
        if any(str(alias).lower() == model_l for alias in aliases):
            return route or {}
    return {}


def _apply_router_environment(router: dict) -> None:
    """Set commercial backend env defaults from router values.

    We only set env vars that are currently empty, so shell/user overrides win.
    This keeps commercial backends unchanged while allowing a single router
    file to define their base URLs and keys.
    """
    commercial = (router or {}).get("commercial") or {}
    if not isinstance(commercial, dict):
        return
    for route in commercial.values():
        if not isinstance(route, dict):
            continue
        for field in ("base_url", "api_key"):
            env_name = route.get(f"{field}_env")
            value = route.get(field)
            if env_name and value not in (None, "") and not os.getenv(str(env_name)):
                os.environ[str(env_name)] = str(value)


def _apply_router_to_agent(agent_cfg: dict, router: dict) -> dict:
    if not router:
        return agent_cfg

    merged = copy.deepcopy(agent_cfg)
    kind = str(merged.get("kind") or "vlm").lower()
    model = str(merged.get("model") or "")
    explicit_extra = copy.deepcopy(merged.get("extra") or {})

    route_extra: dict = {}
    if kind in {"openp2p", "nitrogen"}:
        route = ((router.get("policy") or {}).get(kind) or {})
        route_extra = _materialize_route_extra(route, keep_unknown=True)
    elif kind == "vlm":
        route_extra = _materialize_route_extra(
            _router_model_route(router, model),
            keep_unknown=False,
        )

    if route_extra:
        merged["extra"] = _deep_merge(route_extra, explicit_extra)
    return merged


# -- Matrix expansion ----------------------------------------------------

def _build_env(cfg: dict, game_default_task: str) -> EnvSpec:
    env_cfg = dict(cfg.get("env") or {})
    if not env_cfg.get("task"):
        env_cfg["task"] = game_default_task
    if env_cfg.get("map"):
        env_cfg["map"] = resolve_map_name(
            env_cfg["map"],
            cfg.get("maps_config", cfg.get("maps")),
        )
    return EnvSpec(**env_cfg)


def _build_agents(cfg: dict) -> list[AgentProfile]:
    """Parse the ``agents:`` list. Entries may be either:
      - a plain string (the model identifier), or
      - a dict with at least ``model:``, plus optional ``kind:`` / ``extra:``.
    ``agents_defaults`` is merged into every entry.
    """
    defaults = cfg.get("agents_defaults") or {}
    router = _load_router_config(cfg.get("router_config", cfg.get("router")))
    global_prompt_skills = _as_str_list(cfg.get("prompt_skills") or [])
    raw = cfg.get("agents") or []
    if not raw:
        raise ValueError(
            "Benchmark config must define at least one agent under `agents:`"
        )
    out: list[AgentProfile] = []
    for a in raw:
        if isinstance(a, str):
            a = {"model": a}
        merged = _deep_merge(defaults, a)
        merged["prompt_skills"] = _dedupe_preserve_order(
            global_prompt_skills
            + _as_str_list(defaults.get("prompt_skills") or [])
            + _as_str_list(merged.get("prompt_skills") or [])
        )
        merged = _apply_router_to_agent(merged, router)
        _reject_legacy_backend_field(merged)
        out.append(AgentProfile(**merged))
    return out


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _build_players(cfg: dict, env: EnvSpec, num_players: int) -> list[PlayerSpec]:
    """Parse two-player ``players:`` entries."""
    defaults = cfg.get("players_defaults") or cfg.get("agents_defaults") or {}
    router = _load_router_config(cfg.get("router_config", cfg.get("router")))
    raw = cfg.get("players") or []
    if not raw:
        raise ValueError(
            "Two-player config must define `players:` with one entry per player"
        )
    if len(raw) != num_players:
        raise ValueError(
            f"Expected {num_players} player entries, got {len(raw)}"
        )

    raw_player_ids = [
        int(item.get("id", idx)) if isinstance(item, dict) else idx
        for idx, item in enumerate(raw)
    ]
    display_ids = set(range(1, num_players + 1))
    ids_are_display_ids = set(raw_player_ids) == display_ids

    players: list[PlayerSpec] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            item = {"model": item}
        item = copy.deepcopy(item)

        raw_player_index = int(item.pop("id", idx))
        player_index = (
            raw_player_index - 1 if ids_are_display_ids else raw_player_index
        )
        host = item.pop("host", env.host)
        port = int(item.pop("port", env.port + player_index))
        task = item.pop("task", env.task)

        nested_agent = item.pop("agent", None)
        if nested_agent is not None:
            agent_cfg = _deep_merge(defaults, nested_agent)
        else:
            agent_cfg = _deep_merge(defaults, item)
        agent_cfg = _apply_router_to_agent(agent_cfg, router)
        _reject_legacy_backend_field(agent_cfg)

        players.append(
            PlayerSpec(
                player_index=player_index,
                host=host,
                port=port,
                task=task,
                agent=AgentProfile(**agent_cfg),
            )
        )

    seen = {p.player_index for p in players}
    expected = set(range(num_players))
    if seen != expected:
        raise ValueError(
            "Player ids must be either "
            f"{sorted(expected)} (internal/UE ids) or "
            f"{sorted(display_ids)} (display ids), got {sorted(raw_player_ids)}"
        )
    return sorted(players, key=lambda p: p.player_index)


def _reject_legacy_backend_field(agent_cfg: dict) -> None:
    """Reject removed backend-routing fields before constructing AgentProfile."""
    model = str(agent_cfg.get("model") or "<unknown>")
    if "backend" in agent_cfg:
        raise ValueError(
            "`backend` is no longer supported in agent configs. "
            f"Select the VLM by `model` instead (agent model: {model})."
        )
    extra = agent_cfg.get("extra")
    if isinstance(extra, dict) and "backend" in extra:
        raise ValueError(
            "`extra.backend` is no longer supported in agent configs. "
            f"Select the VLM by `model` instead (agent model: {model})."
        )


def _expand_params(cfg: dict) -> list[ParamsPoint]:
    """Cartesian product of runtime-parameter knobs (YAML ``params:`` block).

    Each knob may be:
      - missing        -> use dataclass default (single-value grid)
      - a scalar       -> single value (one cell)
      - a list         -> iterate over all values (sweep)
    """
    defaults = ParamsPoint()
    params_cfg = cfg.get("params") or {}

    def _as_list(v):
        if v is None:
            return [None]
        if isinstance(v, list):
            return v if v else [None]
        return [v]

    knobs: dict[str, list] = {}
    for name, default in asdict(defaults).items():
        knobs[name] = _as_list(params_cfg.get(name, default))

    keys = list(knobs.keys())
    combos = list(itertools.product(*[knobs[k] for k in keys]))
    return [ParamsPoint(**dict(zip(keys, c))) for c in combos]


def expand_experiments(
    cfg: dict,
    game_name: str,
    game_default_task: str,
) -> list[Experiment]:
    """Expand a resolved config into concrete experiments."""
    env = _build_env(cfg, game_default_task)
    agents = _build_agents(cfg)
    param_points = _expand_params(cfg)
    episodes_per_cell = int(cfg.get("episodes_per_cell", 1))

    include = _as_str_list(cfg.get("include_agents") or [])
    exclude = set(cfg.get("exclude_agents") or [])
    if include:
        include_set = set(include)
        defined = {a.model for a in agents}
        agents = [a for a in agents if a.model in include_set]
        # `--include` is additive: a requested model that the YAML never listed
        # under `agents:` is synthesized on the fly from `agents_defaults`
        # (+ router + prompt_skills), exactly as a bare-string `agents:` entry
        # would be. This lets the CLI run any model without first editing the
        # config, mirroring how two-player `players:` can name any model.
        # (A genuine typo still fails - loudly - later at backend resolution.)
        missing = _dedupe_preserve_order([m for m in include if m not in defined])
        if missing:
            agents = agents + _build_agents({**cfg, "agents": missing})
    if exclude:
        agents = [a for a in agents if a.model not in exclude]

    experiments: list[Experiment] = []
    for agent in agents:
        for ab in param_points:
            for ep in range(episodes_per_cell):
                # Each episode is saved under its own timestamped directory.
                # Keep run_id at the cell level; episode_idx carries the repeat index.
                # Policy agents (nitrogen / openp2p) ignore the VLM param knobs,
                # so give them a plain kind-based id instead of the VLM short_id.
                run_id = ab.short_id() if agent.kind == "vlm" else agent.kind
                experiments.append(
                    Experiment(
                        game=game_name,
                        env=env,
                        agent=agent,
                        params=ab,
                        episode_idx=ep,
                        run_id=run_id,
                    )
                )
    return experiments


def expand_two_player_experiments(
    cfg: dict,
    game_name: str,
    game_default_task: str,
    num_players: int,
) -> list[TwoPlayerExperiment]:
    """Expand a two-player PvP/Coop config into concrete match runs."""
    env = _build_env(cfg, game_default_task)
    players = _build_players(cfg, env, num_players)
    param_points = _expand_params(cfg)
    episodes_per_cell = int(cfg.get("episodes_per_cell", 1))
    player_slug = _players_slug(players)

    experiments: list[TwoPlayerExperiment] = []
    for ab in param_points:
        for ep in range(episodes_per_cell):
            run_id = f"{player_slug}/{ab.short_id()}"
            experiments.append(
                TwoPlayerExperiment(
                    game=game_name,
                    env=env,
                    players=players,
                    params=ab,
                    episode_idx=ep,
                    run_id=run_id,
                )
            )
    return experiments


def _players_slug(players: list[PlayerSpec]) -> str:
    return "_vs_".join(
        f"player{_player_display_id(p.player_index)}-{p.agent.model}"
        for p in players
    )


def _player_display_id(player_index: int) -> int:
    return player_index + 1


def _player_label(player_index: int) -> str:
    return f"player_{_player_display_id(player_index)}"


def _player_spec_output_dict(player: PlayerSpec) -> dict:
    data = asdict(player)
    internal_index = player.player_index
    data["ue_player_index"] = internal_index
    data["player_index"] = _player_display_id(internal_index)
    data["player_id"] = _player_display_id(internal_index)
    data["player_label"] = _player_label(internal_index)
    return data


def iter_experiments(
    cfg: dict,
    game_name: str,
    game_default_task: str,
) -> Iterable[Experiment]:
    yield from expand_experiments(cfg, game_name, game_default_task)
