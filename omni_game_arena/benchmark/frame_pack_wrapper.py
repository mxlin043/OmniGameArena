"""
Attach FramePack compression to any VLMAgent / LumineVLMAgent **without
subclassing or editing omni_game_arena**.

Mechanism
---------
Both ``VLMAgent`` and ``LumineVLMAgent`` store history as
``self._history: deque[(PIL.Image, str)]`` and re-read it inside every
``act(...)`` call to build the message list.  We exploit that contract
to intercept ``act`` at runtime:

    1. Before ``act`` runs, snapshot the original (full-resolution)
       history and temporarily REPLACE ``agent._history`` with a version
       whose older frames are resized by the chosen FramePack kernel.
    2. Let the original ``act`` do its thing - it will build messages
       using the resized history and append the NEW current frame
       (original resolution, the way it always did) to the deque.
    3. After ``act`` returns, restore the original history and
       re-append the new current frame so that the next call starts
       from the ground truth.

Because we only mutate instance state that the class already owns, no
inheritance or monkey-patching of methods is necessary - we just bind a
new ``act`` using ``types.MethodType``.  Undoing this is as simple as
removing the attribute.

This wrapper is intentionally defensive: if the agent does not expose
``_history`` (e.g. non-VLM agents from ``openp2p``/``nitrogen``) the wrapper
becomes a no-op and logs a warning instead of crashing.
"""

from __future__ import annotations

import logging
import types
from collections import deque
from typing import Any

from .frame_pack import FramePackConfig, PackStats, pack_history

logger = logging.getLogger(__name__)


_ATTR_STATS = "_framepack_last_stats"     # latest PackStats
_ATTR_CFG = "_framepack_cfg"              # the attached config
_ATTR_ORIG_ACT = "_framepack_original_act"


def attach_frame_pack(agent: Any, cfg: FramePackConfig) -> Any:
    """Decorate ``agent.act`` with FramePack compression, in place.

    Parameters
    ----------
    agent
        A VLMAgent- or LumineVLMAgent-like object that exposes a
        ``_history: deque[(image, action_str)]`` attribute and an
        ``act(obs, task, schema)`` method.
    cfg
        FramePack configuration.  If ``cfg.kernel == "none"`` this
        function is a no-op so callers can pass the params config
        unconditionally.

    Returns
    -------
    The same agent, with ``act`` rebound.  Idempotent: calling twice
    with a different config re-wraps cleanly.
    """
    if cfg.kernel == "none":
        # Still record the config for downstream logging, but do not
        # wrap the method - zero overhead baseline.
        setattr(agent, _ATTR_CFG, cfg)
        return agent

    if not hasattr(agent, "_history") or not isinstance(agent._history, deque):
        logger.warning(
            "attach_frame_pack: agent %s has no deque _history; "
            "falling back to no-op.",
            type(agent).__name__,
        )
        return agent

    # Peel off any existing wrap so re-attaching is safe.
    _detach(agent)

    original_act = agent.act
    setattr(agent, _ATTR_ORIG_ACT, original_act)
    setattr(agent, _ATTR_CFG, cfg)
    setattr(agent, _ATTR_STATS, None)

    def _wrapped_act(self, obs, task, action_schema):  # noqa: ANN001
        hist_deque: deque = self._history

        # Snapshot original (newest-first order matches paper convention)
        original = list(hist_deque)[::-1]   # deque iterates oldest->newest

        packed_frames, stats = pack_history(original, cfg)
        setattr(self, _ATTR_STATS, stats)

        # Re-populate deque in original order (oldest->newest) with
        # compressed frames.  Use a fresh deque with the same maxlen so
        # .append semantics in the original act() still work.
        maxlen = hist_deque.maxlen
        compressed = deque(
            [(p.image, p.action) for p in reversed(packed_frames)],
            maxlen=maxlen,
        )
        self._history = compressed
        # Marker tuple that both VLMAgent and LumineVLMAgent will keep
        # at the TAIL: the wrapped act() appends exactly one new entry
        # (current_frame, response_string) at the end - we recover it
        # by filtering out tuples whose identity we already know.
        pre_call_ids = {id(item) for item in compressed}

        try:
            action = original_act(obs, task, action_schema)
        finally:
            # Recover the new entry appended by original_act. It is
            # always full-resolution because original_act feeds
            # obs["image"] directly - perfect for the ground-truth
            # history we rebuild below.
            new_entry: tuple | None = None
            for item in reversed(compressed):
                if id(item) not in pre_call_ids:
                    new_entry = item
                    break

            rebuilt: deque = deque(hist_deque, maxlen=maxlen)   # full-res copy of pre-call history
            if new_entry is not None:
                rebuilt.append(new_entry)
            self._history = rebuilt

        return action

    agent.act = types.MethodType(_wrapped_act, agent)
    logger.info(
        "FramePack attached: kernel=%s base_size=%d min_size=%d",
        cfg.kernel, cfg.base_size, cfg.min_size,
    )
    return agent


def _detach(agent: Any) -> None:
    """Undo a previous ``attach_frame_pack`` (no-op if not attached)."""
    if hasattr(agent, _ATTR_ORIG_ACT):
        agent.act = getattr(agent, _ATTR_ORIG_ACT)
        delattr(agent, _ATTR_ORIG_ACT)


def last_stats(agent: Any) -> PackStats | None:
    """Return the most recent pack stats, or None if never packed."""
    return getattr(agent, _ATTR_STATS, None)
