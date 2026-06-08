"""Analyzer-side prompts and tool schemas.

The analyzer agent is a separate LLM call (typically the same model as
the player) whose job is to read one round's output directory and
distill a tactical memo for the next round. It does this via a small
read-only tool harness — see ``sandbox.py`` for the underlying
implementations.

The tool schemas here follow the Anthropic Messages API ``tools``
format. The same names / shapes work with other backends once they
grow tool-use support; only the wire encoding differs.
"""

from __future__ import annotations

# ── Tool schemas (Anthropic format) ────────────────────────────────────
# Kept deliberately small. The model already understands list_dir /
# read_text / read_image / grep from countless CLI traces in its
# pretraining; we don't need round-specific high-level wrappers.

TOOL_SPECS: list[dict] = [
    {
        "name": "list_dir",
        "description": (
            "List files and subdirectories under a path inside the round "
            "directory. Use this first to discover what's available. Each "
            "entry includes name, type (dir/file), and size_bytes for files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subpath": {
                    "type": "string",
                    "description": (
                        "Path relative to the round directory. Empty string "
                        "(default) lists the round root."
                    ),
                },
            },
        },
    },
    {
        "name": "read_text",
        "description": (
            "Read a text file (JSON, JSONL, TXT, MD) and return its full "
            "contents. Use this to inspect result.json (final score / "
            "done_reason), summary.json (per-step actions / vlm_response / "
            "score), recap_meta.json, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the round directory.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "Optional: truncate to this many characters. "
                        "Omit to read the whole file (subject to a 1 MB hard cap)."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_image",
        "description": (
            "Load a JPEG/PNG frame as a multimodal input. The image becomes "
            "visible to you in the next turn alongside any text you've gathered. "
            "Use this to verify what the player actually saw at a specific step "
            "(e.g. read step_0007.jpg to see frame 7)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the round directory.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Search a regex pattern across text files inside the round "
            "directory. Returns up to 200 matching lines as "
            "(file, line, content). Use this to find specific events across "
            "all episodes (e.g. pattern='done_reason.*game_over' to locate "
            "every failure step)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python regex pattern.",
                },
                "glob_pattern": {
                    "type": "string",
                    "description": (
                        "Glob filter for files to search "
                        "(default: '**/*.json'). Use '**/*' for everything."
                    ),
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive matching (default false).",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "get_previous_skill",
        "description": (
            "Retrieve the tactical memo produced after the previous round, "
            "if any. Returns null on round 1. When a previous memo exists, "
            "refine it: keep what still applies, revise what didn't work."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_skill",
        "description": (
            "Submit your final tactical memo and end the analysis. The memo "
            "will be injected into the next round's system prompt verbatim. "
            "Under 200 words. Heuristic, transferable — not a walkthrough "
            "of the level you just saw. Calling this terminates the "
            "analyzer loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memo": {
                    "type": "string",
                    "description": (
                        "The tactical memo text. Heuristic bullets like "
                        "'when <visual cue>, prefer <response class>'. "
                        "No literal action strings, no level-specific names, "
                        "no restating controls. Plain prose / bullet list, "
                        "no <|skill_start|> wrappers needed."
                    ),
                },
            },
            "required": ["memo"],
        },
    },
]


# ── System prompt ──────────────────────────────────────────────────────
# Hard-won lesson from the first iteration of this harness: when the
# memo is allowed to contain literal action strings and level-specific
# descriptions, the next round's player blindly copies them and gets
# WORSE — because the level layout shifts slightly and the analyzer's
# theory of which-platform-killed-us is mostly hallucinated anyway.
#
# So the prompt below aggressively forbids three things:
#   1. Literal action strings (the player can plan its own actions).
#   2. Map / level memorization (the layout may differ next time, and
#      the player already sees the screen).
#   3. Restating controls / keybindings (already in the system prompt).
#
# What we DO want is transferable visual-cue → response-class heuristics.
ANALYZER_SYSTEM_PROMPT = """\
You are reviewing your own past attempts at a game and writing a tactical \
memo for your future self (the same model, playing the next round).

You have read-only tools to explore one round's output directory. Layout:

  - skill_in.txt           the memo (if any) carried over from the previous round.
                           Empty file for round 1.
  - round_summary.json     fast overview: per-episode score, done_reason, steps,
                           plus mean_score across episodes.
  - episode_NN/            one subdirectory per episode this round, each holding:
      - result.json        final score, done_reason, agent / params config
      - summary.json       per-step list: action, vlm_response (your reasoning
                           at each step), score, max_score_seen
      - step_NNNN.jpg      the frame you observed at each step (step_0000.jpg
                           is the reset / initial frame; the last step's jpg
                           shows the terminal state)

# Important: done_reason is unreliable

Many games report `done_reason == "game_over"` for BOTH success (reached the
finish line, completed the objective) AND failure (fell, died, ran out of
time). Do NOT use done_reason alone to tell a win from a loss. Use:
  - the score trajectory across steps,
  - the score at the terminal step vs the game's expected max,
  - and the last frame in the episode (load step_NNNN.jpg at the highest N).

# What the memo is for

The memo is injected verbatim into the next round's system prompt. The \
next round may play a SLIGHTLY DIFFERENT level (different obstacle \
positions, different enemy placement, different platform shapes), so \
the memo must be a set of GENERALIZABLE HEURISTICS, not a walkthrough of \
the level you just observed.

# What the memo MUST be

  - HEURISTIC: rules of the form "when <visual cue / situation>, prefer
    <kind of response>". Cue → response class, not cue → exact action.
  - TRANSFERABLE: should still help if the level layout changes. If your
    rule depends on "the third platform" or "the yellow-striped one",
    it isn't transferable.
  - HONEST: include failure patterns, not just wins. If you don't have
    evidence for a claim, say so or drop it.
  - SHORT: under 200 words total.

# What the memo MUST NOT contain

  - LITERAL ACTION STRINGS such as `0 0 ; W ; W ; W Space ; W ; W ; W`.
    The future you can compose its own actions; prescribing exact
    sequences makes it copy you blindly even when the situation has
    changed.
  - LEVEL / MAP MEMORIZATION such as "platform 2 has yellow-black
    stripes" or "the 3rd platform is shorter". These facts may not
    generalize, and the future you already sees the current screen
    so it doesn't need them written down.
  - BACKWARD-LOOKING RAW SCORE OBSERVATIONS such as "ep 0 scored 0.27,
    ep 1 scored 0.14". Per-episode raw numbers from what already
    happened don't help future episodes; convert them into heuristics
    instead. NOTE: general game-mechanic numbers ARE allowed and useful
    — e.g. "each chunk is ~1.2s", "an upgrade cycle costs ~8 steps",
    "with <60s left, prefer short tasks". Those are rules of the game,
    not specific past observations.
  - RESTATING CONTROLS or KEYBINDINGS. The next round's system prompt
    already includes the controls list; don't duplicate it.
  - PRESCRIBED STEP COUNTS or SUB-STEP TIMINGS such as "jump at
    sub-step 3". These are level-specific.

# Good memo bullets (style examples)

  - "When the next jump target looks small in the frame, prefer fewer
    forward inputs before Space — momentum tends to overshoot."
  - "When the character drifts off-centre, correct in the SAME chunk
    that contains the next jump; spending a whole chunk on pure
    alignment loses momentum."
  - "If a target is below your current eyeline, it's usually closer
    than perspective suggests."

# Bad memo bullets (avoid)

  - "Use: 0 0 ; W ; W ; W Space ; W ; W ; W"        (literal action)
  - "Platform 2 is the moving yellow-striped one"   (level memorization)
  - "Ep 0 scored 0.27, ep 1 scored 0.14"            (raw score numbers)
  - "Press W to move forward, Space to jump"        (duplicates controls)

# Suggested process

  1. Read round_summary.json first for a one-shot view. Sort by score
     to pick which episode is worth digging into (best vs worst). Do
     NOT trust done_reason on its own — load the last frame of an
     episode to see whether "game_over" was a win or a loss.
  2. For one or two interesting episodes, open summary.json and skim
     past vlm_response entries — that's your own reasoning at each step.
  3. When reasoning alone is ambiguous, load the matching step_NNNN.jpg
     to verify what you actually saw.
  4. Look for PATTERNS across episodes — what error repeats?
  5. Promote each pattern into ONE general heuristic (cue → response
     class).
  6. If a previous memo exists, fetch and refine. Drop rules whose
     prediction didn't match what you now observe; keep rules that
     held.
  7. Call submit_skill with the final memo.

Be efficient — explore enough to be confident, then submit. There is a \
hard cap on tool-call iterations.\
"""


# ── Seed user message ──────────────────────────────────────────────────
def build_seed_message(
    *,
    game: str,
    round_idx: int,
    n_episodes: int,
    has_previous_skill: bool,
) -> str:
    """The first user turn the analyzer sees, describing this round."""
    skill_note = (
        "A previous round's memo exists — use get_previous_skill to fetch "
        "and refine it."
        if has_previous_skill
        else "This is round 1, so there is no previous memo to refine."
    )
    return (
        f"You just finished round {round_idx + 1} of {game}, consisting of "
        f"{n_episodes} episodes. Their outputs are in this round directory.\n\n"
        f"{skill_note}\n\n"
        f"Start by exploring (list_dir is a good first call), then write and "
        f"submit your memo for round {round_idx + 2}."
    )
