"""
Ablation #5 - FramePack-style context compression for VLM history.

Paper reference
---------------
"Packing Input Frame Context in Next-Frame Prediction Models for Video
Generation" (Zhang et al., arXiv:2504.12626).  FramePack assigns
**different patch kernels** to frames at different temporal distances:
the closer a frame is to "now", the finer its kernel (more tokens);
the farther away, the coarser (fewer tokens).  This yields a
time-proximity-aware context whose total token count is bounded even
for infinitely long videos (geometric sums converge).

Translation to the VLM benchmark setting
----------------------------------------
A VLM exposes **image resolution** as its only knob that directly
controls patch count - typical vision transformers patchify at a fixed
pixel stride (e.g. 14 px), so token count scales with `H*W`.  Therefore:

    "larger patch kernel"  ==  "lower input resolution"  ==  "fewer tokens"

We re-implement the FramePack kernels (Figure 1(c) of the paper) by
**resizing each history frame to a different resolution** before the
VLM sees it, according to that frame's age in the sliding window.

Supported kernels
-----------------
All kernels accept a **base resolution** `base_size` (applied to
F_0, i.e. the most recent frame - this is also what `resize_size`
already does in `VLMAgent.__init__`) and produce a per-age resolution
schedule `r[t]` (t = 0 is newest).

* ``none``                 - disabled, return frames untouched (baseline).
* ``geometric``            - r[t] = base_size * (factor ** t), clamped
                             to ``min_size``.  Paper's canonical choice.
                             Default factor = 1/sqrt2 (tokens halve each step).
* ``level_duplication``    - exponentially growing levels, each level
                             contains 2x more frames.  Matches "Level
                             duplication" in Figure 1(c).
* ``temporal_kernel``      - average-pool groups of consecutive old
                             frames into a single compressed frame
                             (time-axis kernel).  Matches "Temporal
                             kernel".
* ``important_start``      - geometric progression, but the OLDEST frame
                             in the window is restored to full size
                             (identity/anchor frame).  Matches
                             "Progression with important start".

Each kernel returns a list of ``PackedFrame`` tuples - newest first,
same order as ``VLMAgent._history`` after reversal.

Zero core-code footprint
------------------------
This module only knows about PIL.Image - it never imports from
``omni_game_arena`` or subclasses any agent.  It's used by
``agent_wrapper.py`` to build a drop-in ``act`` replacement that
mutates ``agent._history`` in place only for the duration of one call.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable

from PIL import Image

logger = logging.getLogger(__name__)


# -- Data types ----------------------------------------------------------

@dataclass
class PackedFrame:
    """One history frame after FramePack compression."""
    image: Image.Image        # the (possibly resized) image shown to the VLM
    action: str               # the assistant-side response text, unchanged
    age: int                  # 0 = newest frame in history window
    resolution: int           # longest side, px (for logging only)
    # How many original frames were collapsed into this token group.
    # >1 only for ``temporal_kernel`` - otherwise always 1.
    group_size: int = 1


@dataclass
class PackStats:
    """Per-call summary, persisted into ``info`` for downstream analysis."""
    kernel: str
    n_input_frames: int            # len(history) before packing
    n_output_frames: int           # len(history) after packing (<= input for temporal_kernel)
    base_size: int
    min_size: int
    resolutions: list[int]         # one per output frame, newest first
    approx_token_ratio: float      # total tokens vs uncompressed baseline (1.0 = no compression)


# -- Kernel registry ----------------------------------------------------
#
# A "kernel" is a pure function:
#     history (newest-first list of (image, action)) -> list[PackedFrame]
#
# We pass them through a tiny registry so new kernels can be added
# without touching the wrapper / factory / runner code.

KernelFn = Callable[
    [list[tuple[Image.Image, str]], "FramePackConfig"],
    list[PackedFrame],
]

_KERNELS: dict[str, KernelFn] = {}


def register_kernel(name: str) -> Callable[[KernelFn], KernelFn]:
    def _wrap(fn: KernelFn) -> KernelFn:
        _KERNELS[name] = fn
        return fn
    return _wrap


def available_kernels() -> list[str]:
    return ["none", *sorted(_KERNELS.keys())]


# -- Config -------------------------------------------------------------

@dataclass
class FramePackConfig:
    """Parameters for the compression schedule."""
    kernel: str = "none"
    # F_0 (newest) resolution - defaults to the agent's own resize_size
    # when this config is attached, kept independent so callers can also
    # experiment with larger "anchor" resolutions than the base one.
    base_size: int = 512
    # Lower-bound on the longest edge so the smallest frames are still
    # readable by vision encoders (most expect >= 224).
    min_size: int = 112
    # For geometric / important_start: r[t] = base * factor**t.
    # factor = 1/sqrt(2) means tokens halve every step.
    shrink_factor: float = 1.0 / math.sqrt(2.0)
    # For level_duplication: at level L there are 2**L frames and each
    # is sized base * level_shrink**L.
    level_shrink: float = 0.5
    # For temporal_kernel: consecutive groups of this many old frames
    # are merged (via average blending) into a single "kernel" frame.
    temporal_group_base: int = 2

    def __post_init__(self) -> None:
        self.kernel = (self.kernel or "none").lower()
        if self.kernel not in available_kernels():
            raise ValueError(
                f"Unknown frame_pack kernel {self.kernel!r}. "
                f"Expected one of {available_kernels()}."
            )
        if self.base_size <= 0:
            raise ValueError("base_size must be positive")
        if self.min_size <= 0:
            raise ValueError("min_size must be positive")


# -- Helpers ------------------------------------------------------------

def _resize(img: Image.Image, longest: int) -> Image.Image:
    """Resize keeping aspect ratio; longest edge = ``longest`` px."""
    w, h = img.size
    if max(w, h) == longest:
        return img
    scale = longest / float(max(w, h))
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return img.resize(new_size, Image.BILINEAR)


def _clamp_size(size: float, cfg: FramePackConfig) -> int:
    return max(cfg.min_size, int(round(size)))


def _tokens_for(res: int, base_res: int) -> float:
    """Relative token count for a square-ish patchified input."""
    return (res * res) / float(base_res * base_res)


# -- Kernel implementations ---------------------------------------------

@register_kernel("geometric")
def _geometric(
    history: list[tuple[Image.Image, str]],
    cfg: FramePackConfig,
) -> list[PackedFrame]:
    """r[t] = base * shrink_factor ** t  (t=0 newest)."""
    out: list[PackedFrame] = []
    for t, (img, act) in enumerate(history):
        target = _clamp_size(cfg.base_size * (cfg.shrink_factor ** t), cfg)
        out.append(PackedFrame(
            image=_resize(img, target),
            action=act,
            age=t,
            resolution=target,
        ))
    return out


@register_kernel("level_duplication")
def _level_duplication(
    history: list[tuple[Image.Image, str]],
    cfg: FramePackConfig,
) -> list[PackedFrame]:
    """
    Level 0: 1 frame  at base
    Level 1: 2 frames at base * level_shrink
    Level 2: 4 frames at base * level_shrink**2
    ...

    Matches Figure 1(c) "Level duplication".
    """
    out: list[PackedFrame] = []
    level = 0
    idx = 0
    while idx < len(history):
        frames_at_level = 1 << level
        target = _clamp_size(cfg.base_size * (cfg.level_shrink ** level), cfg)
        for _ in range(frames_at_level):
            if idx >= len(history):
                break
            img, act = history[idx]
            out.append(PackedFrame(
                image=_resize(img, target),
                action=act,
                age=idx,
                resolution=target,
            ))
            idx += 1
        level += 1
    return out


@register_kernel("temporal_kernel")
def _temporal_kernel(
    history: list[tuple[Image.Image, str]],
    cfg: FramePackConfig,
) -> list[PackedFrame]:
    """
    Collapse groups of consecutive older frames into one compressed
    frame via pixel-space average blending (the visual analogue of
    a time-axis patchify kernel).

    Group sizes grow exponentially:
        group 0 : 1 frame  at base
        group 1 : temporal_group_base**1 frames at base * shrink
        group 2 : temporal_group_base**2 frames at base * shrink**2
        ...

    This is strictly compressing - the output list can be SHORTER than
    the input, which mirrors the paper's intent of reducing total token
    count, not just per-frame token count.
    """
    out: list[PackedFrame] = []
    idx = 0
    group_idx = 0
    while idx < len(history):
        group_size = cfg.temporal_group_base ** group_idx if group_idx > 0 else 1
        target = _clamp_size(cfg.base_size * (cfg.shrink_factor ** group_idx), cfg)

        group = history[idx : idx + group_size]
        if not group:
            break
        blended = _blend([g[0] for g in group], target)
        # Keep the LATEST action-string within the group - that one is
        # the one most strongly attributable to "what came next".
        _, last_action = group[-1]
        out.append(PackedFrame(
            image=blended,
            action=last_action,
            age=idx,
            resolution=target,
            group_size=len(group),
        ))
        idx += group_size
        group_idx += 1
    return out


@register_kernel("important_start")
def _important_start(
    history: list[tuple[Image.Image, str]],
    cfg: FramePackConfig,
) -> list[PackedFrame]:
    """Geometric progression + restore the OLDEST frame to full resolution.

    Empirically the anchor/identity frame (F_{T-1}) matters for drift
    prevention; this kernel keeps that invariant.
    """
    packed = _geometric(history, cfg)
    if packed:
        last = packed[-1]
        # Re-sample from the original to avoid double-resize artefacts.
        orig_img = history[last.age][0]
        packed[-1] = PackedFrame(
            image=_resize(orig_img, cfg.base_size),
            action=last.action,
            age=last.age,
            resolution=cfg.base_size,
        )
    return packed


def _blend(images: list[Image.Image], target_size: int) -> Image.Image:
    """Average-blend a group of images at ``target_size`` (longest edge)."""
    if not images:
        raise ValueError("_blend requires at least one image")
    if len(images) == 1:
        return _resize(images[0], target_size)
    resized = [_resize(img, target_size).convert("RGB") for img in images]
    # Resolve to a common canvas (all should already match after _resize,
    # but guard against minor off-by-one rounding).
    w = min(im.size[0] for im in resized)
    h = min(im.size[1] for im in resized)
    resized = [im.crop((0, 0, w, h)) for im in resized]

    acc = Image.new("RGB", (w, h))
    # Incremental blend keeps memory at one extra image.
    alpha_steps = [1.0 / (i + 1) for i in range(len(resized))]
    acc = resized[0]
    for i in range(1, len(resized)):
        acc = Image.blend(acc, resized[i], alpha_steps[i])
    return acc


# -- Public API ---------------------------------------------------------

def pack_history(
    history: list[tuple[Image.Image, str]],
    cfg: FramePackConfig,
) -> tuple[list[PackedFrame], PackStats]:
    """Apply the configured kernel to a history window (newest-first).

    Returns the compressed frame list and a ``PackStats`` describing the
    achieved compression ratio - useful for benchmark-level metrics.
    """
    if not history or cfg.kernel == "none":
        stats = PackStats(
            kernel=cfg.kernel, n_input_frames=len(history),
            n_output_frames=len(history), base_size=cfg.base_size,
            min_size=cfg.min_size,
            resolutions=[cfg.base_size] * len(history),
            approx_token_ratio=1.0,
        )
        return [
            PackedFrame(image=img, action=act, age=i, resolution=cfg.base_size)
            for i, (img, act) in enumerate(history)
        ], stats

    fn = _KERNELS[cfg.kernel]
    packed = fn(history, cfg)

    resolutions = [p.resolution for p in packed]
    total_tokens = sum(_tokens_for(r, cfg.base_size) for r in resolutions)
    baseline_tokens = float(len(history))  # each frame would be 1.0 uncompressed
    token_ratio = total_tokens / baseline_tokens if baseline_tokens else 1.0

    stats = PackStats(
        kernel=cfg.kernel,
        n_input_frames=len(history),
        n_output_frames=len(packed),
        base_size=cfg.base_size,
        min_size=cfg.min_size,
        resolutions=resolutions,
        approx_token_ratio=round(token_ratio, 4),
    )
    logger.debug(
        "FramePack[%s]: %d->%d frames, resolutions=%s, token_ratio=%.3f",
        cfg.kernel, stats.n_input_frames, stats.n_output_frames,
        resolutions, token_ratio,
    )
    return packed, stats
