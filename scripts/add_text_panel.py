"""Add the recorder's right-side text panel to existing episode videos.

Two modes (both reuse ``VideoRecorder``'s exact ``side`` panel, so output matches
the live algorithm and the VLM videos pixel-for-pixel, and never overwrite the
source - a NEW ``<name><suffix>.mp4`` is written next to it):

* default ("N/A"): a single static "N/A" entry (policy agents have no
  chain-of-thought). Used for the nitrogen backups.
* ``--steps``: read ``actions.jsonl`` next to each video and scroll the real
  per-step action log in the panel, synced to the video.

Timing note for ``--steps``: ``actions.jsonl`` timestamps are second-resolution
and the backup videos are time-compressed, so frames are mapped to steps *by
episode fraction* (frame i of N -> step active at wall-fraction i/N, sub-ordered
within each second). This tracks the whole episode start-to-end with ~0.5s
accuracy; it is not frame-exact (the source data has no sub-second timestamps).
Original fps / frame count are preserved, so playback speed is unchanged.

Usage:
    python scripts/add_text_panel.py [root] [--steps] [--limit N] [--overwrite]
    # default root: runs_backup/nitrogen   (use runs_backup/openp2p --steps)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

import imageio_ffmpeg as iio
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from omni_game_arena.eval.video_recorder import VideoRecorder  # noqa: E402


def _even(value: int) -> int:
    return value - (value % 2)


def _writer(dst: str, size, fps: float, quality: float):
    w = iio.write_frames(
        dst, size, fps=fps, quality=quality,
        codec="libx264", pix_fmt_in="rgb24", pix_fmt_out="yuv420p",
        macro_block_size=2, ffmpeg_log_level="error",
        output_params=["-movflags", "+faststart"],
    )
    w.send(None)
    return w


def _render_panel(rec: VideoRecorder, w: int, h: int) -> Image.Image:
    """Crop just the right-side panel for the recorder's current text state."""
    composite = rec._append_side_text_panel(Image.new("RGB", (w, h)))
    return composite.crop((w, 0, composite.width, h)).copy()


# --------------------------------------------------------------------------- #
# N/A mode (nitrogen)
# --------------------------------------------------------------------------- #

def add_panel(src: str, dst: str, rec: VideoRecorder, cache: dict, quality: float) -> tuple:
    reader = iio.read_frames(src)
    meta = next(reader)
    w, h = meta["size"]
    fps = meta["fps"]
    if h not in cache:
        cache[h] = _render_panel(rec, w, h)
    panel = cache[h]
    out_w, out_h = _even(w + panel.width), _even(h)

    writer = _writer(dst, (out_w, out_h), fps, quality)
    frames = 0
    try:
        for frame_bytes in reader:
            frame = Image.frombytes("RGB", (w, h), frame_bytes)
            out = Image.new("RGB", (w + panel.width, h), (0, 0, 0))
            out.paste(frame, (0, 0))
            out.paste(panel, (w, 0))
            if out.size != (out_w, out_h):
                out = out.crop((0, 0, out_w, out_h))
            writer.send(out.tobytes())
            frames += 1
    finally:
        writer.close()
    return frames, fps, (out_w, out_h)


# --------------------------------------------------------------------------- #
# steps mode (openp2p): scroll the real actions.jsonl log, synced to the video
# --------------------------------------------------------------------------- #

def _load_steps_any(ep_dir: str, actions_name: str) -> list[dict]:
    """Load per-step records, preferring ``actions.jsonl`` (openp2p backups:
    action is already a dict and ``ts`` is present), falling back to
    ``reflection_trace/steps.jsonl`` (nitrogen v2: no ``ts`` and ``action`` is a
    double-encoded JSON string, so decode it back to a dict)."""
    def _read(path):
        out = []
        for line in open(path, encoding="utf-8").read().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    af = os.path.join(ep_dir, actions_name)
    if os.path.exists(af):
        return [r for r in _read(af) if "step" in r]

    tf = os.path.join(ep_dir, "reflection_trace", "steps.jsonl")
    if os.path.exists(tf):
        recs = []
        for r in _read(tf):
            if "step" not in r:
                continue
            a = r.get("action")
            if isinstance(a, str):
                try:
                    r["action"] = json.loads(a)  # un-double-encode
                except (ValueError, TypeError):
                    pass
            recs.append(r)
        return recs
    return []


def _step_fractions(recs: list[dict]) -> list[float]:
    """Episode-fraction in [0,1) at which each step becomes current.

    With second-resolution ``ts`` (openp2p actions.jsonl), steps sharing a
    second are spread evenly across it before normalising by the wall span.
    Without timestamps (reflection_trace/steps.jsonl), fall back to even-by-
    index. Either way the panel tracks the whole episode start-to-end.
    """
    n = len(recs)
    if n <= 1:
        return [0.0] * n
    if not all("ts" in r for r in recs):
        return [k / n for k in range(n)]  # no timestamps -> even by step index
    rel = [(_dt.datetime.fromisoformat(r["ts"])
            - _dt.datetime.fromisoformat(recs[0]["ts"])).total_seconds()
           for r in recs]
    span = rel[-1]
    if span <= 0:
        return [k / n for k in range(n)]
    eff = [0.0] * len(rel)
    i = 0
    while i < len(rel):
        j = i
        while j < len(rel) and rel[j] == rel[i]:
            j += 1
        for m in range(j - i):
            eff[i + m] = rel[i] + m / (j - i)
        i = j
    return [min(0.999999, e / span) for e in eff]


def _truthy(v) -> bool:
    try:
        return float(v) != 0
    except (TypeError, ValueError):
        return bool(v)


def _fmt_line(r: dict) -> str:
    a = r.get("action")
    if not isinstance(a, dict):
        return "done" if (r.get("game_over") or r.get("done")) else ""
    # nitrogen gamepad action -> compact: sticks at 2 decimals, only pressed
    # buttons listed, e.g.  "j_left": [0.35, -0.90], ..., "buttons": ["RIGHT_TRIGGER"]
    if "j_left" in a or "j_right" in a or "buttons" in a:
        def _stick(v):
            try:
                return "[" + ", ".join(f"{float(x):.2f}" for x in v) + "]"
            except (TypeError, ValueError):
                return json.dumps(v, ensure_ascii=False)
        parts = []
        if "j_left" in a:
            parts.append(f'"j_left": {_stick(a.get("j_left") or [])}')
        if "j_right" in a:
            parts.append(f'"j_right": {_stick(a.get("j_right") or [])}')
        pressed = [n for n, val in (a.get("buttons") or {}).items() if _truthy(val)]
        parts.append('"buttons": ' + json.dumps(pressed, ensure_ascii=False))
        return ", ".join(parts)
    # openp2p / others -> raw fields verbatim
    return json.dumps(a, ensure_ascii=False)[1:-1]


def _count_frames(src: str) -> int:
    """Exact number of frames ``read_frames`` yields. Container metadata
    (duration*fps, count_frames_and_secs) is unreliable for the retimed
    (``-itsscale``) videos, so count what the decoder actually produces."""
    r = iio.read_frames(src)
    next(r)
    n = sum(1 for _ in r)
    try:
        r.close()
    except Exception:  # noqa: BLE001
        pass
    return n


def add_step_panel(src: str, dst: str, recs: list[dict], quality: float,
                   width: int, crop_right: int = 0) -> tuple:
    n_frames = max(1, _count_frames(src))
    reader = iio.read_frames(src)
    meta = next(reader)
    w, h = meta["size"]
    duration = float(meta.get("duration") or 0)
    # Write at the effective fps (real frames / real seconds) so the output
    # keeps the same duration even when the container's tagged fps is off.
    fps = n_frames / duration if duration > 0 else float(meta["fps"])
    fracs = _step_fractions(recs)
    n_steps = len(recs)
    game_w = max(1, w - crop_right)   # drop an existing right-side panel, if any

    rec = VideoRecorder(dst, with_text_panel=True, light_text_panel=False,
                        text_layout="side", text_panel_width=width)
    out_w, out_h = _even(game_w + rec.text_panel_width), _even(h)
    writer = _writer(dst, (out_w, out_h), fps, quality)

    k = -1            # index of last step pushed into the panel
    panel = None
    frames = 0
    try:
        for frame_bytes in reader:
            frame = Image.frombytes("RGB", (w, h), frame_bytes)
            if crop_right:
                frame = frame.crop((0, 0, game_w, h))
            frac = min(0.999999, frames / n_frames)
            target = k
            while target + 1 < n_steps and fracs[target + 1] <= frac:
                target += 1
            if target < 0:
                target = 0
            if target != k or panel is None:
                for s in range(k + 1, target + 1):
                    rec.push_thinking(recs[s]["step"], _fmt_line(recs[s]))
                k = target
                panel = _render_panel(rec, game_w, h)
            out = Image.new("RGB", (game_w + rec.text_panel_width, h), (0, 0, 0))
            out.paste(frame, (0, 0))
            out.paste(panel, (game_w, 0))
            if out.size != (out_w, out_h):
                out = out.crop((0, 0, out_w, out_h))
            writer.send(out.tobytes())
            frames += 1
    finally:
        writer.close()
    return frames, fps, (out_w, out_h)


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=os.path.join("runs_backup", "nitrogen"))
    ap.add_argument("--suffix", default="_panel")
    ap.add_argument("--width", type=int, default=520, help="panel width in px")
    ap.add_argument("--quality", type=float, default=7.0)
    ap.add_argument("--steps", action="store_true",
                    help="scroll actions.jsonl per-step log (synced) instead of N/A")
    ap.add_argument("--actions-name", default="actions.jsonl")
    ap.add_argument("--crop-right", type=int, default=0,
                    help="crop this many px off the right of each input frame "
                         "(e.g. an existing N/A panel) before adding the new panel")
    ap.add_argument("--limit", type=int, default=0, help="process at most N files (0=all)")
    ap.add_argument("--overwrite", action="store_true",
                    help="rebuild even if the output already exists")
    args = ap.parse_args()

    na_rec = VideoRecorder(
        os.path.join(args.root, "_unused.mp4"),
        with_text_panel=True, light_text_panel=True,
        text_layout="side", text_panel_width=args.width,
    )
    na_cache: dict = {}

    targets = []
    for dirpath, _dirs, files in os.walk(args.root):
        for name in files:
            if name.lower().endswith(".mp4") and not name.endswith(args.suffix + ".mp4"):
                targets.append(os.path.join(dirpath, name))
    targets.sort()
    if args.limit:
        targets = targets[: args.limit]

    print(f"found {len(targets)} source video(s) under {args.root} "
          f"(mode={'steps' if args.steps else 'N/A'})")
    done = skipped = failed = 0
    for i, src in enumerate(targets, 1):
        stem, ext = os.path.splitext(src)
        dst = stem + args.suffix + ext
        rel = os.path.relpath(src, args.root)
        if os.path.exists(dst) and not args.overwrite:
            print(f"[{i}/{len(targets)}] skip (exists): {rel}")
            skipped += 1
            continue
        try:
            if args.steps:
                recs = _load_steps_any(os.path.dirname(src), args.actions_name)
                if not recs:
                    print(f"[{i}/{len(targets)}] skip (no step data): {rel}")
                    skipped += 1
                    continue
                frames, fps, size = add_step_panel(
                    src, dst, recs, args.quality, args.width, args.crop_right,
                )
                extra = f"{len(recs)} steps"
            else:
                frames, fps, size = add_panel(src, dst, na_rec, na_cache, args.quality)
                extra = "N/A"
            print(f"[{i}/{len(targets)}] {rel} -> {os.path.basename(dst)}  "
                  f"{size[0]}x{size[1]} {frames}f @{fps:.0f}  [{extra}]")
            done += 1
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(targets)}] FAIL {rel}: {type(e).__name__}: {e}")
            failed += 1
    print(f"done={done} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
