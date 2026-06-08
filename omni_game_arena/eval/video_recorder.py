"""Realtime video recorder for UE5 episodes.

The recorder mirrors ``LiveViewer.start_streaming``: a background thread
polls screenshots from the UE5 client at a target FPS. Unlike the viewer,
it receives the env instead of just the client so it can skip frames while
the benchmark has intentionally paused simulation time.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import threading
import time

from PIL import Image, ImageDraw, ImageFile, ImageFont

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)


class VideoRecorder:
    """Write a real-time MP4 stream from a SoloEnv-like object.

    Frames are sampled only while ``env.world_paused`` is false. This keeps
    paused-decision modes such as PDQ/LCRT from adding duplicate still frames
    during model inference.
    """

    def __init__(
        self,
        output_path: str,
        fps: int = 30,
        quality: float = 7.0,
        with_text_panel: bool = False,
        text_layout: str = "side",
        text_panel_width: int = 520,
        light_text_panel: bool = False,
    ):
        self.output_path = output_path
        self.fps = max(1, int(fps))
        self.quality = float(quality)
        self.with_text_panel = bool(with_text_panel)
        # Policy-agent mode (openp2p / nitrogen). These have no chain-of-thought
        # and step ~20x/s, so two things differ from VLM recordings:
        #   1. the text panel renders a single static "N/A" entry (keeps the
        #      video the same size as VLM side-panel videos for presentation,
        #      without the churning per-step JSON that tanks render time);
        #   2. at close the MP4 is retimed to the fps actually achieved
        #      (frame_count / wall seconds). These agents starve the UE5
        #      screenshot endpoint so capture runs below ``fps``; tagging the
        #      file at a fixed ``fps`` would make it play sped-up. Retiming
        #      makes playback duration match real time. Off by default ->
        #      other agents are untouched.
        self._light_text_panel = bool(light_text_panel)
        self.text_layout = (text_layout or "side").strip().lower()
        if self.text_layout not in {"dashboard", "top", "bottom", "overlay", "side"}:
            raise ValueError(
                "text_layout must be 'dashboard', 'top', 'bottom', 'overlay', or 'side'"
            )
        self.text_panel_width = max(320, int(text_panel_width))
        self.frame_count = 0
        self.error: str | None = None
        # Wall-clock span of written frames, used to retime policy videos.
        self._t_first: float | None = None
        self._t_last: float | None = None
        self._retimed = False

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sink_thread: threading.Thread | None = None
        self._latest_frame: Image.Image | None = None
        self._latest_frame_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._writer = None
        self._size: tuple[int, int] | None = None
        self._text_lock = threading.Lock()
        self._text_step: int | None = None
        self._text_reason = ""
        self._text_action = ""
        self._text_status = ""
        self._text_history: list[dict] = []
        self._font = _load_font(18)
        self._font_small = _load_font(15)
        self._font_bold = _load_font(18, bold=True)

    def push_step(self, step: int, reason: str = "", action: str = "") -> None:
        """Update the optional text panel shown beside future video frames."""
        self._push_text_entry(step, reason=reason, action=action, status="")

    def push_thinking(self, step: int, message: str = "Thinking...") -> None:
        """Show a temporary thinking status for future video frames."""
        self._push_text_entry(step, reason="", action="", status=message)

    def _push_text_entry(
        self,
        step: int,
        *,
        reason: str = "",
        action: str = "",
        status: str = "",
    ) -> None:
        if not self.with_text_panel:
            return
        if self._light_text_panel:
            # Policy agents: the panel is a fixed "N/A" (see
            # _text_history_snapshot); skip the churning per-step text.
            return
        with self._text_lock:
            self._text_step = step
            self._text_reason = str(reason or "").strip()
            self._text_action = str(action or "").strip()
            self._text_status = str(status or "").strip()
            entry = {
                "step": step,
                "reason": self._text_reason,
                "action": self._text_action,
                "status": self._text_status,
            }
            if self._text_history and self._text_history[-1]["step"] == step:
                self._text_history[-1] = entry
            else:
                self._text_history.append(entry)
                self._text_history = self._text_history[-64:]

    def start_streaming(self, env) -> None:
        """Start capturing frames from ``env.client`` in a background thread."""
        self.stop_streaming()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._stream_loop,
            args=(env,),
            daemon=True,
            name="VideoRecorder-stream",
        )
        self._thread.start()
        logger.info("Video recording started: %s", self.output_path)

    def start_frame_sink(self) -> None:
        """Start writing frames supplied by another screenshot stream.

        This mode is used when policy agents share the live viewer's screenshot
        stream, avoiding a second background thread that also calls
        ``client.screenshot()``. The sink writes at ``self.fps`` in real time,
        repeating the newest received frame between sparse screenshots.
        """
        self.stop_streaming()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        self._stop = threading.Event()
        with self._latest_frame_lock:
            self._latest_frame = None
        self._sink_thread = threading.Thread(
            target=self._frame_sink_loop,
            daemon=True,
            name="VideoRecorder-frame-sink",
        )
        self._sink_thread.start()
        logger.info("Video recording started from shared frame stream: %s", self.output_path)

    def enqueue_frame(self, image: Image.Image, timestamp: float | None = None) -> None:
        """Store the newest frame captured by an external stream."""
        if self._stop.is_set() or self._sink_thread is None:
            return
        with self._latest_frame_lock:
            self._latest_frame = image

    def stop_streaming(self) -> None:
        """Stop the background thread and close the MP4 writer."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._thread = None
        if self._sink_thread is not None and self._sink_thread.is_alive():
            self._sink_thread.join(timeout=10.0)
        self._sink_thread = None
        with self._latest_frame_lock:
            self._latest_frame = None
        self._close_writer()
        self._retime_to_wall_clock()

    def _stream_loop(self, env) -> None:
        interval = 1.0 / self.fps
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                client = getattr(env, "client", None)
                if (
                    client is None
                    or not getattr(client, "connected", False)
                    or getattr(env, "world_paused", False)
                ):
                    if self._stop.wait(min(0.05, interval)):
                        break
                    continue

                try:
                    result = client.screenshot()
                    image = Image.open(io.BytesIO(result["data"]))
                    image.load()
                    self.write_frame(image)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Video frame error: %s", e)
                    if self._stop.wait(0.1):
                        break
                    continue

                elapsed = time.monotonic() - t0
                sleep_time = interval - elapsed
                if sleep_time > 0 and self._stop.wait(sleep_time):
                    break
        finally:
            self._close_writer()

    def _frame_sink_loop(self) -> None:
        interval = 1.0 / self.fps
        next_frame_at = time.monotonic()
        try:
            while not self._stop.is_set():
                with self._latest_frame_lock:
                    image = self._latest_frame.copy() if self._latest_frame is not None else None
                if image is None:
                    if self._stop.wait(min(0.05, interval)):
                        break
                    next_frame_at = time.monotonic()
                    continue

                now = time.monotonic()
                if now < next_frame_at:
                    if self._stop.wait(next_frame_at - now):
                        break
                    continue

                self.write_frame(image)
                next_frame_at += interval
                if next_frame_at < now - interval:
                    next_frame_at = now + interval
        finally:
            self._close_writer()

    def write_frame(self, image: Image.Image) -> None:
        """Write one externally supplied frame to the MP4 stream."""
        try:
            with self._write_lock:
                self._write_frame(image)
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            logger.debug("Video frame write error: %s", e)

    def _write_frame(self, image: Image.Image) -> None:
        image = image.convert("RGB")
        if self.with_text_panel:
            if self.text_layout == "dashboard":
                image = self._append_dashboard_text_panel(image)
            elif self.text_layout == "top":
                image = self._append_vertical_text_panel(image, position="top")
            elif self.text_layout == "bottom":
                image = self._append_bottom_text_panel(image)
            elif self.text_layout == "side":
                image = self._append_side_text_panel(image)
            else:
                image = self._overlay_text_panel(image)
        image = self._normalize_frame_size(image)
        if self._writer is None:
            self._open_writer(image.size)
        self._writer.send(image.tobytes())
        self.frame_count += 1
        if self._light_text_panel:
            now = time.monotonic()
            if self._t_first is None:
                self._t_first = now
            self._t_last = now

    def _append_side_text_panel(self, image: Image.Image) -> Image.Image:
        panel_w = self.text_panel_width
        panel_h = image.height
        panel = Image.new("RGB", (panel_w, panel_h), (18, 18, 18))
        draw = ImageDraw.Draw(panel)

        margin = 18
        max_width = panel_w - margin * 2
        max_height = panel_h - margin * 2
        history = self._text_history_snapshot()
        rows = self._bottom_history_rows(draw, history, max_width)
        visible_rows = self._visible_rows(rows, max_height)
        self._draw_text_rows(draw, visible_rows, margin, panel_w)

        out = Image.new("RGB", (image.width + panel_w, image.height), (0, 0, 0))
        out.paste(image, (0, 0))
        out.paste(panel, (image.width, 0))
        return out

    def _append_dashboard_text_panel(self, image: Image.Image) -> Image.Image:
        """Add a right thinking panel plus a bottom compact log panel."""
        panel_w = self.text_panel_width
        total_w = image.width + panel_w
        target_h = int(round(total_w * 9 / 16))
        bottom_h = max(120, target_h - image.height)
        bottom_h = min(bottom_h, max(120, image.height // 3))

        right_panel = Image.new("RGB", (panel_w, image.height), (18, 18, 18))
        right_draw = ImageDraw.Draw(right_panel)
        bottom_panel = Image.new("RGB", (total_w, bottom_h), (14, 14, 14))
        bottom_draw = ImageDraw.Draw(bottom_panel)

        history = self._text_history_snapshot()
        margin = 18

        latest_history = history[-1:] if history else []
        right_rows = self._bottom_history_rows(
            right_draw,
            latest_history,
            panel_w - margin * 2,
        )
        self._draw_text_rows(
            right_draw,
            self._visible_rows(right_rows, image.height - margin * 2),
            margin,
            panel_w,
        )

        self._draw_compact_log_panel(bottom_draw, history, total_w, bottom_h)

        out = Image.new("RGB", (total_w, image.height + bottom_h), (0, 0, 0))
        out.paste(image, (0, 0))
        out.paste(right_panel, (image.width, 0))
        out.paste(bottom_panel, (0, image.height))

        draw = ImageDraw.Draw(out)
        draw.line((image.width, 0, image.width, image.height), fill=(54, 54, 54), width=1)
        draw.line((0, image.height, total_w, image.height), fill=(54, 54, 54), width=1)
        return out

    def _append_bottom_text_panel(self, image: Image.Image) -> Image.Image:
        """Append a non-overlapping text band below the game frame."""
        return self._append_vertical_text_panel(image, position="bottom")

    def _append_vertical_text_panel(
        self,
        image: Image.Image,
        *,
        position: str,
    ) -> Image.Image:
        """Append a non-overlapping text band above or below the game frame."""
        panel_h = max(220, min(360, image.height // 3))
        panel = Image.new("RGB", (image.width, panel_h), (18, 18, 18))
        draw = ImageDraw.Draw(panel)

        margin = 18
        max_width = image.width - margin * 2
        max_height = panel_h - margin * 2
        history = self._text_history_snapshot()
        rows = self._bottom_history_rows(draw, history, max_width)
        visible_rows = self._visible_rows(rows, max_height)
        self._draw_text_rows(draw, visible_rows, margin, image.width)

        out = Image.new("RGB", (image.width, image.height + panel_h), (0, 0, 0))
        if position == "top":
            out.paste(panel, (0, 0))
            out.paste(image, (0, panel_h))
        else:
            out.paste(image, (0, 0))
            out.paste(panel, (0, image.height))
        return out

    def _text_history_snapshot(self) -> list[dict]:
        if self._light_text_panel:
            # Policy agents have no reasoning to show; render a single static
            # "N/A" entry so the panel (and thus the video size) matches VLM
            # recordings while staying cheap to draw every frame.
            return [{"step": None, "reason": "N/A", "action": "", "status": ""}]
        with self._text_lock:
            history = [dict(item) for item in self._text_history]
            if not history:
                history = [{
                    "step": self._text_step,
                    "reason": self._text_reason or "(waiting for first response)",
                    "action": self._text_action,
                    "status": self._text_status,
                }]
            return history

    def _visible_rows(self, rows: list[dict], max_height: int) -> list[dict]:
        visible_rows: list[dict] = []
        total_h = 0
        for row in rows:
            row_h = row["height"]
            if visible_rows and total_h + row_h > max_height:
                break
            visible_rows.append(row)
            total_h += row_h
        return visible_rows

    def _draw_text_rows(
        self,
        draw: ImageDraw.ImageDraw,
        rows: list[dict],
        margin: int,
        panel_width: int,
    ) -> None:
        y = margin
        for row in rows:
            row_h = row["height"]
            if row["kind"] == "divider":
                line_y = y + row_h // 2
                draw.line(
                    (margin, line_y, panel_width - margin, line_y),
                    fill=(70, 70, 70),
                    width=1,
                )
            elif row["kind"] == "segments":
                x = margin
                for text, fill, font in row["segments"]:
                    if text:
                        draw.text((x, y), text, fill=fill, font=font)
                        x += _text_width(draw, text, font)
            else:
                draw.text(
                    (margin, y),
                    row["text"],
                    fill=row["fill"],
                    font=row["font"],
                )
            y += row_h

    def _draw_compact_log_panel(
        self,
        draw: ImageDraw.ImageDraw,
        history: list[dict],
        panel_width: int,
        panel_height: int,
    ) -> None:
        margin = 18
        y = 12
        max_width = panel_width - margin * 2
        line_h = _line_height(self._font_small) + 5

        draw.text((margin, y), "Action timeline", fill=(255, 97, 136), font=self._font_bold)
        y += _line_height(self._font_bold) + 8

        for entry in reversed(history):
            if y + line_h > panel_height - 8:
                break

            step = entry.get("step")
            step_text = "Step -" if step is None else f"Step {step}"
            status = entry.get("status") or ""
            reason = " ".join(str(entry.get("reason") or "").split())
            action = " ".join(str(entry.get("action") or "").split())

            if status:
                detail = status
                detail_fill = (255, 216, 102)
            elif action:
                detail = action
                detail_fill = (120, 220, 232)
            else:
                detail = reason or "(no visible reasoning)"
                detail_fill = (225, 225, 225)

            x = margin
            label = f"{step_text}: "
            draw.text((x, y), label, fill=(255, 97, 136), font=self._font_bold)
            x += _text_width(draw, label, self._font_bold)
            remaining_width = max(40, max_width - (x - margin))
            draw.text(
                (x, y),
                _ellipsize_text(draw, detail, self._font_small, remaining_width),
                fill=detail_fill,
                font=self._font_small,
            )
            y += line_h

    def _bottom_history_rows(
        self,
        draw: ImageDraw.ImageDraw,
        history: list[dict],
        max_width: int,
    ) -> list[dict]:
        rows: list[dict] = []
        small_h = _line_height(self._font_small) + 3
        bold_h = _line_height(self._font_bold) + 4
        ordered_history = list(reversed(history))
        for idx, entry in enumerate(ordered_history):
            step = entry.get("step")
            step_text = "Step -" if step is None else f"Step {step}"
            rows.append(_text_row(step_text, (255, 97, 136), self._font_bold, bold_h))

            status = entry.get("status") or ""
            if status:
                for line in _wrap_text(draw, status, self._font_bold, max_width):
                    rows.append(_text_row(line, (255, 216, 102), self._font_bold, bold_h))
                if idx != len(ordered_history) - 1:
                    rows.append(_divider_row())
                continue

            reason = entry.get("reason") or "(no visible reasoning)"
            rows.extend(self._bottom_labeled_rows(
                draw,
                "Reason",
                reason,
                max_width,
                body_fill=(225, 225, 225),
                small_h=small_h,
                bold_h=bold_h,
            ))

            action = entry.get("action") or ""
            if action:
                rows.extend(self._bottom_labeled_rows(
                    draw,
                    "Action",
                    action,
                    max_width,
                    body_fill=(120, 220, 232),
                    small_h=small_h,
                    bold_h=bold_h,
                ))

            if idx != len(ordered_history) - 1:
                rows.append(_divider_row())
        return rows

    def _bottom_labeled_rows(
        self,
        draw: ImageDraw.ImageDraw,
        label: str,
        text: str,
        max_width: int,
        *,
        body_fill: tuple[int, int, int],
        small_h: int,
        bold_h: int,
    ) -> list[dict]:
        label_text = f"{label}: "
        label_fill = (255, 216, 102)
        label_w = _text_width(draw, label_text, self._font_bold)
        first_width = max(48, max_width - label_w)
        body_lines = _wrap_text_with_first_width(
            draw,
            text,
            self._font_small,
            first_width,
            max_width,
        ) or [""]
        rows = [{
            "kind": "segments",
            "segments": [
                (label_text, label_fill, self._font_bold),
                (body_lines[0], body_fill, self._font_small),
            ],
            "height": max(bold_h, small_h),
        }]
        for line in body_lines[1:]:
            rows.append(_text_row(line, body_fill, self._font_small, small_h))
        return rows

    def _overlay_text_panel(self, image: Image.Image) -> Image.Image:
        """Draw a compact bottom overlay while preserving video dimensions."""
        image_rgba = image.convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        with self._text_lock:
            step = self._text_step
            reason = self._text_reason
            action = self._text_action
            status = self._text_status

        panel_h = min(max(150, image.height // 4), max(80, image.height - 8))
        x0 = 0
        y0 = image.height - panel_h
        draw.rectangle(
            (x0, y0, image.width, image.height),
            fill=(16, 16, 16, 218),
        )
        draw.line((0, y0, image.width, y0), fill=(255, 97, 136, 230), width=2)

        margin = 18
        y = y0 + 12
        step_text = "Step -"
        if step is not None:
            step_text = f"Step {step}"
        draw.text((margin, y), step_text, fill=(255, 97, 136, 255), font=self._font_bold)
        y += _line_height(self._font_bold) + 8

        if status:
            draw.text(
                (margin, y),
                status,
                fill=(255, 216, 102, 255),
                font=self._font_bold,
            )
            return Image.alpha_composite(image_rgba, overlay).convert("RGB")

        action_block_h = _line_height(self._font_bold) + _line_height(self._font_small) + 12
        reason_max_y = image.height - margin - action_block_h
        y = self._draw_labeled_text(
            draw,
            "Reason",
            reason or "(waiting for first response)",
            x=margin,
            y=y,
            max_width=image.width - margin * 2,
            max_y=reason_max_y,
            body_fill=(225, 225, 225, 255),
        )
        y = min(y + 6, image.height - margin - action_block_h + 6)
        self._draw_labeled_text(
            draw,
            "Action",
            action,
            x=margin,
            y=y,
            max_width=image.width - margin * 2,
            max_y=image.height - margin,
            body_fill=(120, 220, 232, 255),
        )
        return Image.alpha_composite(image_rgba, overlay).convert("RGB")

    def _draw_labeled_text(
        self,
        draw: ImageDraw.ImageDraw,
        label: str,
        text: str,
        *,
        x: int,
        y: int,
        max_width: int,
        max_y: int,
        body_fill: tuple[int, int, int],
    ) -> int:
        draw.text((x, y), f"{label}: ", fill=(255, 216, 102), font=self._font_bold)
        y += _line_height(self._font_bold) + 4
        if not text:
            return y

        line_h = _line_height(self._font_small) + 3
        for line in _wrap_text(draw, text, self._font_small, max_width):
            if y + line_h > max_y:
                draw.text((x, y), "...", fill=body_fill, font=self._font_small)
                return y + line_h
            draw.text((x, y), line, fill=body_fill, font=self._font_small)
            y += line_h
        return y

    def _normalize_frame_size(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        width -= width % 2
        height -= height % 2
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid video frame size: {image.size}")
        if image.size != (width, height):
            image = image.crop((0, 0, width, height))
        if self._size is not None and image.size != self._size:
            image = image.resize(self._size, Image.Resampling.LANCZOS)
        return image

    def _open_writer(self, size: tuple[int, int]) -> None:
        try:
            import imageio_ffmpeg
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "Video recording requires imageio_ffmpeg to be installed"
            ) from e

        self._size = size
        writer = imageio_ffmpeg.write_frames(
            self.output_path,
            size,
            fps=self.fps,
            quality=self.quality,
            codec="libx264",
            pix_fmt_in="rgb24",
            pix_fmt_out="yuv420p",
            macro_block_size=2,
            ffmpeg_log_level="error",
            output_params=["-movflags", "+faststart"],
        )
        writer.send(None)
        self._writer = writer

    def _close_writer(self) -> None:
        with self._write_lock:
            if self._writer is None:
                return
            try:
                self._writer.close()
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                logger.warning("Video writer close failed: %s", e)
            finally:
                self._writer = None

    def _retime_to_wall_clock(self) -> None:
        """Re-tag a finished policy video so its duration matches real time.

        Policy agents capture below the nominal ``fps`` (they hammer the UE5
        screenshot endpoint), so a file tagged at ``fps`` plays sped-up. We
        rewrite the container timestamps to the fps actually achieved
        (frame_count / wall span) via a lossless stream copy (``-itsscale`` +
        ``-c copy``) - no re-encode. Only runs for ``light_text_panel``; other
        agents' files are left exactly as written.
        """
        if self._retimed or not self._light_text_panel:
            return
        if self.frame_count < 2 or self._t_first is None or self._t_last is None:
            return  # nothing recorded (e.g. stop_streaming before any frame)
        span = self._t_last - self._t_first
        if span <= 0:
            return
        self._retimed = True  # real recording present -> retime at most once
        real_fps = self.frame_count / span
        if abs(real_fps - self.fps) <= self.fps * 0.03:
            return  # already within ~3% of nominal; not worth a remux
        if not os.path.exists(self.output_path):
            return
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:  # noqa: BLE001
            logger.warning("Cannot retime video (no ffmpeg): %s", e)
            return
        scale = self.fps / real_fps  # multiplier applied to input timestamps
        tmp_path = f"{self.output_path}.retime.mp4"
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-loglevel", "error",
                    "-itsscale", f"{scale:.6f}",
                    "-i", self.output_path,
                    "-c", "copy",
                    tmp_path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            os.replace(tmp_path, self.output_path)
            logger.info(
                "Retimed policy video to %.1f fps (%d frames over %.1fs; "
                "was tagged %d fps)",
                real_fps, self.frame_count, span, self.fps,
            )
        except Exception as e:  # noqa: BLE001
            self.error = f"retime: {type(e).__name__}: {e}"
            logger.warning("Video retime failed: %s", e)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def _text_row(text: str, fill: tuple[int, int, int], font, height: int) -> dict:
    return {
        "kind": "text",
        "text": text,
        "fill": fill,
        "font": font,
        "height": height,
    }


def _divider_row(height: int = 14) -> dict:
    return {
        "kind": "divider",
        "height": height,
    }


def _font_file_candidates(bold: bool) -> list[str]:
    """Scalable-font paths to try, in priority order, across OSes.

    Monospace is preferred first so Linux/macOS output matches the Windows
    Consolas look. Windows absolute paths come first for parity with the
    original behavior; every other entry is a best-effort path that the
    caller silently skips when absent. matplotlib (a hard dependency) bundles
    DejaVu on every platform, so it is included as a guaranteed fallback that
    actually honors ``size`` -- unlike ``ImageFont.load_default()``, which is
    locked at ~10px and was the cause of the cramped, tiny text on Linux.
    """
    if bold:
        win = ["consolab.ttf", "arialbd.ttf"]
        cross = ["DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf", "DejaVuSans-Bold.ttf"]
    else:
        win = ["consola.ttf", "arial.ttf"]
        cross = ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "DejaVuSans.ttf"]

    paths = ["C:/Windows/Fonts/" + name for name in win]

    search_dirs = [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/dejavu",
        "/Library/Fonts",
        "/System/Library/Fonts",
    ]
    try:
        import matplotlib
        search_dirs.append(
            os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
        )
    except Exception:  # noqa: BLE001
        pass

    for directory in search_dirs:
        for name in cross:
            paths.append(os.path.join(directory, name))
    return paths


def _load_font(size: int, *, bold: bool = False):
    for path in _font_file_candidates(bold):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    # Last resort. Pillow >= 10.1 honors a size arg (scalable DejaVu); older
    # Pillow ignores it and returns a fixed ~10px bitmap font.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _line_height(font) -> int:
    try:
        bbox = font.getbbox("Ag")
        return bbox[3] - bbox[1]
    except Exception:
        return 16


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 8


def _ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> str:
    text = str(text)
    if _text_width(draw, text, font) <= max_width:
        return text
    suffix = "..."
    if _text_width(draw, suffix, font) > max_width:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_width(draw, text[:mid] + suffix, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + suffix


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).replace("\r\n", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            while _text_width(draw, current, font) > max_width and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and _text_width(draw, current[:cut], font) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        if current:
            lines.append(current)
    return lines


def _wrap_text_with_first_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    first_width: int,
    max_width: int,
) -> list[str]:
    """Wrap text with a narrower first line after an inline label."""
    lines: list[str] = []
    use_first_width = True

    for paragraph in str(text).replace("\r\n", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            use_first_width = False
            continue

        current = ""
        for word in words:
            limit = first_width if use_first_width else max_width
            candidate = word if not current else f"{current} {word}"
            if _text_width(draw, candidate, font) <= limit:
                current = candidate
                continue

            if current:
                lines.append(current)
                use_first_width = False
                current = word
            else:
                current = word

            while len(current) > 1:
                limit = first_width if use_first_width else max_width
                if _text_width(draw, current, font) <= limit:
                    break
                cut = len(current) - 1
                while cut > 1 and _text_width(draw, current[:cut], font) > limit:
                    cut -= 1
                lines.append(current[:cut])
                use_first_width = False
                current = current[cut:]

        if current:
            lines.append(current)
            use_first_width = False

    return lines
