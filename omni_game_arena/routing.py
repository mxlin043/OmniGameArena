"""Select the same router config for players, reflectors, and model backends."""
from pathlib import Path
import os


def router_path(explicit: str | None = None, *, root: str | Path | None = None) -> Path:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    selected = os.getenv('OMNI_ARENA_ROUTER_CONFIG') or explicit
    if selected:
        path = Path(selected).expanduser()
        return path if path.is_absolute() else root / path
    return root / 'configs/router.yaml'
