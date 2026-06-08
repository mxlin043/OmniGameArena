"""Skill text extraction utilities.

The analyzer normally calls ``submit_skill`` with the memo text in a
tool argument - that path needs no parsing. But if the analyzer falls
off the loop (writes a final text turn instead of calling submit),
we look for a ``<|skill_start|>...<|skill_end|>`` block in its output
as a fallback, mirroring how the Lumine method parses actions.
"""

from __future__ import annotations

import re

_SKILL_TAG_RE = re.compile(
    r"<\|skill_start\|[>}](.*?)<\|skill_end\|[>}]", re.DOTALL
)


def extract_skill(text: str) -> str | None:
    """Return the memo text from a ``<|skill_start|>...<|skill_end|>`` block,
    or None if no such block is present.

    The same brace-tolerant pattern as Lumine's action tag - accepts both
    ``|>`` and ``|}`` endings to absorb common typos.
    """
    if not text:
        return None
    m = _SKILL_TAG_RE.search(text)
    if not m:
        return None
    memo = m.group(1).strip()
    return memo or None
