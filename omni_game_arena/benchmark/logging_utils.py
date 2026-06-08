"""Per-experiment logging utilities.

Experiment cells attach an extra FileHandler only when detailed per-run debug
artifacts are requested. Logs still stream to stdout. Namespace is parameterized
by ``game_name`` so filters work identically across scenes.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

_BENCHMARK_ROOT = "omni_game_arena.benchmark"


def benchmark_logger_name(game_name: str = "") -> str:
    return f"{_BENCHMARK_ROOT}.{game_name}" if game_name else _BENCHMARK_ROOT


class _ExcludeNamespaceFilter(logging.Filter):
    def __init__(self, namespace: str):
        super().__init__()
        self.namespace = namespace

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == self.namespace
            or record.name.startswith(f"{self.namespace}.")
        )


class _BenchmarkConsoleFilter(logging.Filter):
    """Keep default console output concise: only top-level benchmark progress."""

    def __init__(self, namespace: str, verbose: bool = False):
        super().__init__()
        self.namespace = namespace
        self.allowed = {namespace, f"{namespace}.runner"}
        self.verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if self.verbose:
            return (
                record.name == self.namespace
                or record.name.startswith(f"{self.namespace}.")
            )
        return record.name in self.allowed


def setup_root_logger(
    output_dir: str,
    game_name: str,
    verbose: bool = False,
) -> logging.Logger:
    """Configure root + benchmark loggers.

    Writes to:
      - stdout (benchmark progress only, unless ``verbose``)
    """
    os.makedirs(output_dir, exist_ok=True)
    ns = benchmark_logger_name(game_name)

    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    root_console = logging.StreamHandler(sys.stdout)
    root_console.setFormatter(fmt)
    root_console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root_console.addFilter(_ExcludeNamespaceFilter(ns))
    root.addHandler(root_console)

    bench_logger = logging.getLogger(ns)
    bench_logger.setLevel(level)
    for h in list(bench_logger.handlers):
        bench_logger.removeHandler(h)

    bench_console = logging.StreamHandler(sys.stdout)
    bench_console.setFormatter(fmt)
    bench_console.setLevel(level)
    bench_console.addFilter(_BenchmarkConsoleFilter(ns, verbose=verbose))
    bench_logger.addHandler(bench_console)
    bench_logger.propagate = True

    bench_logger.info("Benchmark logging initialized -> %s", output_dir)
    return bench_logger


class ExperimentLogContext:
    """Attach per-run logging for the duration of one experiment cell."""

    def __init__(
        self,
        run_dir: str,
        name: str = "experiment",
        game_name: str = "",
        write_file: bool = True,
    ):
        self.run_dir = run_dir
        self.name = name
        self.game_name = game_name
        self.write_file = write_file
        self._handler: logging.Handler | None = None
        self._logger: logging.Logger | None = None

    def __enter__(self) -> logging.Logger:
        os.makedirs(self.run_dir, exist_ok=True)
        fmt = logging.Formatter(
            fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        if self.write_file:
            self._handler = logging.FileHandler(
                os.path.join(self.run_dir, f"{self.name}.log"), encoding="utf-8"
            )
            self._handler.setLevel(logging.DEBUG)
            self._handler.setFormatter(fmt)
            logging.getLogger().addHandler(self._handler)

        self._logger = logging.getLogger(
            f"{benchmark_logger_name(self.game_name)}.{self.name}"
        )
        self._logger.info("-- Experiment started: %s --", self.name)
        self._logger.info("Output dir: %s", self.run_dir)
        return self._logger

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._logger:
            if exc is not None:
                self._logger.error(
                    "Experiment ended with error: %s: %s",
                    exc_type.__name__, exc,
                )
            else:
                self._logger.info("-- Experiment finished cleanly --")
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler.close()
            self._handler = None


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def reserve_timestamped_run_dir(parent_dir: str) -> tuple[str, str]:
    """Create ``parent_dir/<YYYYMMDD_HHMMSS>`` and return ``(path, slug)``.

    The slug intentionally has second precision for readable run folders. If
    another episode already used the current second, wait until the next
    available second instead of appending a suffix.
    """
    while True:
        slug = timestamp_slug()
        run_dir = os.path.join(parent_dir, slug)
        try:
            os.makedirs(run_dir)
            return run_dir, slug
        except FileExistsError:
            time.sleep(1.0)


def timestamp_slug_fine() -> str:
    """Timestamp with microseconds - avoids collisions when a single
    benchmark invocation runs multiple episodes of the same cell in
    quick succession (each gets its own directory)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")
