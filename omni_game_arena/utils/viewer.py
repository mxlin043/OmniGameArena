"""Live screenshot viewer using Tkinter - runs in a background thread."""

import io
import threading
import time
import tkinter as tk
import logging

from PIL import Image, ImageTk, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)


class LiveViewer:
    """Displays game screenshots in a Tkinter window (non-blocking).

    Layout::

        +--------------------------------+----------------------+
        |                                |  Step log (scroll)   |
        |         canvas (image)         |  --- Step N ---      |
        |                                |  reason: ...         |
        |                                |  action: ...         |
        +--------------------------------+----------------------+
        | status bar                                            |
        +-------------------------------------------------------+

    Supports two modes:
    - Manual: call update(image, status) to push frames
    - Streaming: call start_streaming(client, fps) to auto-capture at target FPS

    Usage:
        viewer = LiveViewer()
        viewer.start()
        viewer.start_streaming(client, fps=30)          # auto-capture mode
        viewer.set_status("Step 1 | Thinking...")       # status text
        viewer.push_step(1, "I see a gap...", "D ; D Space ; ...")
        viewer.clear_log()                              # at episode boundary
        ...
        viewer.stop()
    """

    # Right-side log panel width in pixels.
    _LOG_PANEL_WIDTH = 460
    _PROGRESS_PANEL_WIDTH = 360

    def __init__(
        self,
        width: int = 1024,
        height: int = 600,
        title: str = "Omni Game Arena Viewer",
        show_log_panel: bool = True,
        show_progress_panel: bool = False,
        progress_panel_width: int | None = None,
        progress_title: str = "Progress",
        progress_initial: str = "",
        progress_log_prefix: str = "",
        scale_to_fit: bool = True,
    ):
        """
        Args:
            width: Canvas (image) region width in px. When
                ``show_log_panel=True`` the window is wider by
                ``_LOG_PANEL_WIDTH`` to fit the right-hand log.
            height: Canvas height in px.
            title: Window title.
            show_log_panel: If True (default), reserve a right-hand
                scrollable text panel for ``push_step()`` / ``clear_log()``.
                Set False for callers that only need image + status bar
                (e.g. evolution / replay / solo scripts).
            show_progress_panel: If True, reserve an extra far-right panel
                for run-level progress such as IDC rounds and episodes.
            progress_panel_width: Optional width for the progress panel.
            progress_title: Header text for the progress panel.
            progress_initial: Initial status text in the progress panel.
            progress_log_prefix: Optional logger prefix for progress updates.
            scale_to_fit: If True (default), resize frames to fit the canvas.
                If False, display frames at their native screenshot resolution.
        """
        self._width = width
        self._height = height
        self._title = title
        self._show_log_panel = bool(show_log_panel)
        self._show_progress_panel = bool(show_progress_panel)
        self._progress_panel_width = (
            max(280, int(progress_panel_width))
            if progress_panel_width is not None
            else self._PROGRESS_PANEL_WIDTH
        )
        self._progress_title = str(progress_title or "Progress")
        self._progress_initial = str(progress_initial or "")
        self._progress_log_prefix = str(progress_log_prefix or "")
        self._scale_to_fit = bool(scale_to_fit)
        self._root = None
        self._canvas = None
        self._photo = None
        self._native_image_size = None
        self._status_var = None
        self._log_text = None
        self._progress_text = None
        self._progress_status_var = None
        self._thread = None
        self._stream_thread = None
        self._stream_stop = threading.Event()
        self._stream_image_lock = threading.Lock()
        self._stream_pending_image = None
        self._stream_update_scheduled = False
        self._ready = threading.Event()
        self._running = False
        self._status_text = ""
        self._status_busy = False
        self._status_anim_idx = 0

    def start(self):
        """Start the viewer window in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_gui, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self):
        """Close the viewer window and stop streaming."""
        self._running = False
        self.stop_streaming()
        root = self._root
        if root:
            try:
                root.after(0, root.destroy)
            except Exception:
                pass

    def start_streaming(
        self,
        client,
        fps: int = 30,
        frame_callback=None,
        drop_stale_frames: bool = False,
    ):
        """Start background thread that captures screenshots at target FPS.

        Any previously running stream (e.g. from the previous episode)
        is stopped and joined before the new one starts, so the viewer
        always reads from the current env's client.

        Args:
            client: UE5Client instance (thread-safe screenshot method)
            fps: Target frames per second for display
            frame_callback: Optional callable that receives each decoded
                PIL frame. Used by policy agents to share the live screenshot
                stream with video recording.
            drop_stale_frames: If True, coalesce pending GUI image updates so
                only the newest frame is displayed. Keep False by default to
                preserve the historical vanilla viewer behavior.
        """
        self.stop_streaming()
        self._stream_stop = threading.Event()
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            args=(client, fps, frame_callback, drop_stale_frames),
            daemon=True,
            name="LiveViewer-stream",
        )
        self._stream_thread.start()
        logger.info("Viewer streaming started at %d fps", fps)

    def stop_streaming(self):
        """Signal the stream thread to exit and wait briefly for it."""
        self._stream_stop.set()
        if self._stream_thread is not None and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=5.0)
        self._stream_thread = None
        with self._stream_image_lock:
            self._stream_pending_image = None
            self._stream_update_scheduled = False

    def set_status(self, status: str, busy: bool = False):
        """Update status bar text only (thread-safe)."""
        self._status_text = str(status or "")
        self._status_busy = bool(busy)
        self._status_anim_idx = 0
        if not self._running or not self._root:
            return
        try:
            self._root.after(0, self._update_status, self._status_text)
        except Exception:
            pass

    def update(self, image: Image.Image, status: str = ""):
        """Update the displayed image (thread-safe). For manual mode."""
        if not self._running or not self._root:
            return
        try:
            self._root.after(0, self._set_image, image, status)
        except Exception:
            pass

    # -- Step log panel --------------------------------------------------

    def push_step(self, step: int, reason: str = "", action: str = ""):
        """Append one step's reasoning + action to the side log (thread-safe).

        Auto-scrolls to the bottom so the newest step is always visible.
        Empty ``reason`` / ``action`` fields are omitted from the entry.
        """
        if not self._running or not self._root:
            return
        try:
            self._root.after(0, self._append_step, step, reason, action)
        except Exception:
            pass

    def clear_log(self, header: str = ""):
        """Clear the side log (thread-safe).

        Call at episode boundaries so the panel doesn't accumulate across
        runs. ``header``, when given, is inserted as the first line so
        you can label the new episode.
        """
        if not self._running or not self._root:
            return
        try:
            self._root.after(0, self._clear_log_impl, header)
        except Exception:
            pass

    def set_progress(self, text: str):
        """Replace the far-right progress panel text (thread-safe)."""
        if not self._running or not self._root:
            return
        try:
            self._root.after(0, self._set_progress_impl, text)
        except Exception:
            pass
        first_line = (text or "").split("\n", 1)[0]
        if first_line:
            prefix = f"{self._progress_log_prefix} " if self._progress_log_prefix else ""
            logger.info("%s%s", prefix, first_line)

    def _animate_status(self):
        """Animate busy status text so long reflection calls look alive."""
        try:
            if self._status_busy and self._running:
                self._status_anim_idx = (self._status_anim_idx + 1) % 4
                text = f"{self._status_text}{'.' * self._status_anim_idx}"
                self._update_status(text)
        except Exception:
            pass
        if self._root is not None and self._running:
            try:
                self._root.after(450, self._animate_status)
            except Exception:
                pass

    @staticmethod
    def _progress_line_tag(line: str) -> str:
        stripped = line.strip()
        low = stripped.lower()
        if "fail" in low or "error" in low:
            return "p_fail"
        if line.startswith(">"):
            return "p_cur"
        if stripped.startswith("mean="):
            return "p_summary"
        if stripped in ("IDC Progress", "Round status"):
            return "p_hdr"
        return ""

    def _stream_loop(
        self,
        client,
        fps: int,
        frame_callback=None,
        drop_stale_frames: bool = False,
    ):
        """Continuously capture screenshots and update the viewer."""
        interval = 1.0 / fps
        while self._running and not self._stream_stop.is_set():
            t0 = time.monotonic()
            try:
                result = client.screenshot()
                jpeg_data = result["data"]
                image = Image.open(io.BytesIO(jpeg_data))
                image.load()

                if drop_stale_frames:
                    self._queue_stream_image(image)
                elif self._running and self._root:
                    self._root.after(0, self._set_image_only, image)
                if frame_callback is not None:
                    try:
                        frame_callback(image, time.monotonic())
                    except TypeError:
                        frame_callback(image)
                    except Exception as e:
                        logger.debug("Stream frame callback error: %s", e)
            except Exception as e:
                logger.debug("Stream frame error: %s", e)
                if self._stream_stop.wait(0.1):
                    break
                continue

            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0 and self._stream_stop.wait(sleep_time):
                break

    def _queue_stream_image(self, image: Image.Image):
        """Queue the newest streaming frame without building a Tk backlog."""
        if not self._running or not self._root:
            return

        schedule = False
        with self._stream_image_lock:
            self._stream_pending_image = image
            if not self._stream_update_scheduled:
                self._stream_update_scheduled = True
                schedule = True

        if schedule:
            try:
                self._root.after(0, self._flush_stream_image)
            except Exception:
                with self._stream_image_lock:
                    self._stream_update_scheduled = False

    def _flush_stream_image(self):
        """Display only the latest queued frame, dropping stale ones."""
        with self._stream_image_lock:
            image = self._stream_pending_image
            self._stream_pending_image = None

        if image is not None:
            self._set_image_only(image)

        with self._stream_image_lock:
            has_next = self._stream_pending_image is not None
            self._stream_update_scheduled = has_next

        if has_next and self._running and self._root:
            try:
                self._root.after(0, self._flush_stream_image)
            except Exception:
                with self._stream_image_lock:
                    self._stream_update_scheduled = False

    def _run_gui(self):
        """Tkinter main loop (runs in background thread)."""
        try:
            self._root = tk.Tk()
            self._root.title(self._title)
            # Extra width for optional right-hand panels.
            total_w = self._width
            if self._show_log_panel:
                total_w += self._LOG_PANEL_WIDTH
            if self._show_progress_panel:
                total_w += self._progress_panel_width
            self._root.geometry(f"{total_w}x{self._height + 30}")
            self._root.configure(bg="black")
            self._root.protocol("WM_DELETE_WINDOW", self._on_close)
            if not self._scale_to_fit:
                self._root.resizable(False, False)

            # Status bar (bottom, full width)
            self._status_var = tk.StringVar(value="Waiting for first frame...")
            status_bar = tk.Label(
                self._root,
                textvariable=self._status_var,
                bd=1,
                relief=tk.SUNKEN,
                anchor=tk.W,
                bg="#222",
                fg="#0f0",
                font=("Consolas", 10),
            )
            status_bar.pack(side=tk.BOTTOM, fill=tk.X)

            # Far-right progress panel (optional, fixed width, vertical scroll).
            # Pack this before the step log so it occupies the outermost right edge.
            if self._show_progress_panel:
                progress_frame = tk.Frame(
                    self._root, bg="#111111", width=self._progress_panel_width
                )
                progress_frame.pack(side=tk.RIGHT, fill=tk.Y)
                progress_frame.pack_propagate(False)

                progress_header = tk.Label(
                    progress_frame,
                    text=self._progress_title,
                    bg="#1f1f1f",
                    fg="#ffffff",
                    font=("Consolas", 13, "bold"),
                )
                progress_header.pack(fill=tk.X)

                self._progress_status_var = tk.StringVar(value=self._progress_initial)
                progress_status = tk.Label(
                    progress_frame,
                    textvariable=self._progress_status_var,
                    bd=1,
                    relief=tk.SUNKEN,
                    anchor=tk.W,
                    bg="#222",
                    fg="#ffd866",
                    font=("Consolas", 9, "bold"),
                )
                progress_status.pack(fill=tk.X)

                progress_scrollbar = tk.Scrollbar(progress_frame)
                progress_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

                self._progress_text = tk.Text(
                    progress_frame,
                    bg="#171717",
                    fg="#d0d0d0",
                    font=("Consolas", 9),
                    wrap=tk.WORD,
                    yscrollcommand=progress_scrollbar.set,
                    state=tk.DISABLED,
                    padx=8,
                    pady=6,
                    borderwidth=0,
                    highlightthickness=0,
                )
                self._progress_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                progress_scrollbar.config(command=self._progress_text.yview)
                self._progress_text.tag_configure(
                    "p_hdr", foreground="#ffffff", font=("Consolas", 9, "bold")
                )
                self._progress_text.tag_configure(
                    "p_cur", foreground="#ffd866", font=("Consolas", 9, "bold")
                )
                self._progress_text.tag_configure("p_summary", foreground="#78dce8")
                self._progress_text.tag_configure("p_fail", foreground="#ff6188")

            # Right-hand step log panel (optional, fixed width, vertical scroll).
            if self._show_log_panel:
                log_frame = tk.Frame(self._root, bg="#1a1a1a", width=self._LOG_PANEL_WIDTH)
                log_frame.pack(side=tk.RIGHT, fill=tk.Y)
                log_frame.pack_propagate(False)  # keep the fixed width

                scrollbar = tk.Scrollbar(log_frame)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

                self._log_text = tk.Text(
                    log_frame,
                    bg="#1a1a1a",
                    fg="#ccc",
                    font=("Consolas", 9),
                    wrap=tk.WORD,
                    yscrollcommand=scrollbar.set,
                    state=tk.DISABLED,
                    padx=8,
                    pady=6,
                    borderwidth=0,
                    highlightthickness=0,
                )
                self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scrollbar.config(command=self._log_text.yview)

                # Color tags for the log text widget.
                self._log_text.tag_configure(
                    "step_header", foreground="#0f0",
                    font=("Consolas", 10, "bold"), spacing1=6, spacing3=2,
                )
                self._log_text.tag_configure("label", foreground="#ffd866")
                self._log_text.tag_configure("reason", foreground="#cccccc")
                self._log_text.tag_configure("action", foreground="#78dce8")
                self._log_text.tag_configure(
                    "episode_marker", foreground="#ff6188",
                    font=("Consolas", 10, "bold"), spacing1=8, spacing3=4,
                )

            # Canvas (remaining space on the left)
            self._canvas = tk.Canvas(
                self._root,
                width=self._width,
                height=self._height,
                bg="black",
                highlightthickness=0,
            )
            if self._scale_to_fit:
                self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                self._canvas.pack(side=tk.LEFT)

            self._root.after(450, self._animate_status)
            self._ready.set()
            self._root.mainloop()
        finally:
            self._running = False
            self._photo = None
            self._status_var = None
            self._log_text = None
            self._progress_text = None
            self._canvas = None
            self._root = None

    def _set_image(self, image: Image.Image, status: str):
        """Update canvas with new image + status (must be called from Tk thread)."""
        self._set_image_only(image)
        if status:
            self._status_text = str(status)
            self._status_busy = False
            self._update_status(self._status_text)

    def _set_image_only(self, image: Image.Image):
        """Update canvas with new image, keep current status (Tk thread)."""
        if not self._canvas:
            return

        img_w, img_h = image.size
        if self._scale_to_fit:
            canvas_w = self._canvas.winfo_width()
            canvas_h = self._canvas.winfo_height()
            if canvas_w <= 1 or canvas_h <= 1:
                canvas_w, canvas_h = self._width, self._height

            scale = min(canvas_w / img_w, canvas_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)

            if new_w > 0 and new_h > 0:
                image = image.resize((new_w, new_h), Image.LANCZOS)

            x_offset = (canvas_w - new_w) // 2
            y_offset = (canvas_h - new_h) // 2
        else:
            new_w, new_h = img_w, img_h
            if self._native_image_size != (new_w, new_h):
                self._canvas.config(width=new_w, height=new_h)
                total_w = new_w + (
                    self._LOG_PANEL_WIDTH if self._show_log_panel else 0
                )
                if self._show_progress_panel:
                    total_w += self._progress_panel_width
                if self._root:
                    self._root.geometry(f"{total_w}x{new_h + 30}")
                self._native_image_size = (new_w, new_h)
            x_offset = 0
            y_offset = 0

        self._photo = ImageTk.PhotoImage(image)
        self._canvas.delete("all")
        self._canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=self._photo)

    def _update_status(self, status: str):
        """Update status text only (Tk thread)."""
        if self._status_var:
            self._status_var.set(status)
        if self._progress_status_var:
            self._progress_status_var.set(status)

    def _append_step(self, step: int, reason: str, action: str):
        """Append step entry to the log text widget (Tk thread)."""
        if not self._log_text:
            return
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"--- Step {step} ---\n", "step_header")
        if reason:
            self._log_text.insert(tk.END, "reason: ", "label")
            self._log_text.insert(tk.END, f"{reason}\n", "reason")
        if action:
            self._log_text.insert(tk.END, "action: ", "label")
            self._log_text.insert(tk.END, f"{action}\n", "action")
        self._log_text.insert(tk.END, "\n")
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    def _clear_log_impl(self, header: str):
        """Wipe the log widget and optionally insert a header line (Tk thread)."""
        if not self._log_text:
            return
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        if header:
            self._log_text.insert(tk.END, f"{header}\n", "episode_marker")
        self._log_text.config(state=tk.DISABLED)

    def _set_progress_impl(self, text: str):
        """Replace progress panel contents (Tk thread)."""
        if not self._progress_text:
            return
        self._progress_text.config(state=tk.NORMAL)
        self._progress_text.delete("1.0", tk.END)
        for line in (text or "").splitlines():
            self._progress_text.insert(
                tk.END, line + "\n", self._progress_line_tag(line)
            )
        self._progress_text.config(state=tk.DISABLED)
        self._progress_text.see("1.0")

    def _on_close(self):
        self._running = False
        self._root.destroy()
