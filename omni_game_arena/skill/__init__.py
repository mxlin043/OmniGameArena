"""Analyzer tools for skill distillation.

``AnalyzerHarness`` runs a multi-turn tool-use loop over one benchmark
round directory. An analyzer agent explores the read-only artifacts and
submits a tactical memo. IDC uses that memo as the next round's
``VLMAgent.system_experience``, which is wired into
``compose_vlm_system`` through the ``prompt_skill`` argument.

Public entry points:
    AnalyzerHarness  - run one analyzer agent loop over one round_dir.
    extract_skill    - parse ``<|skill_start|>...<|skill_end|>`` blocks.
    TOOL_SPECS       - Anthropic-shape tool schema list.
"""

from .harness import AnalyzerHarness
from .parser import extract_skill
from .prompts import ANALYZER_SYSTEM_PROMPT, TOOL_SPECS, build_seed_message

__all__ = [
    "AnalyzerHarness",
    "extract_skill",
    "ANALYZER_SYSTEM_PROMPT",
    "TOOL_SPECS",
    "build_seed_message",
]
