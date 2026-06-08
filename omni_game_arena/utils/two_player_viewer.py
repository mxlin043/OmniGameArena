"""Two-player live viewer for asynchronous PvP/Coop matches."""

from __future__ import annotations

import io
import logging
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)


class TwoPlayerLiveViewer:
    """Show two player observations and their latest reasoning/action."""

    def __init__(
        self,
        width: int = 720,
        height: int = 420,
        title: str = "Omni Game Arena Two Player Viewer",
        show_progress_panel: bool = False,
        progress_panel_width: int = 420,
        progress_title: str = "Progress",
        progress_initial: str = "",
        progress_log_prefix: str = "",
    ):
        self._width = width
        self._height = height
        self._title = title
        self._show_progress_panel = bool(show_progress_panel)
        self._progress_panel_width = max(280, int(progress_panel_width))
        self._progress_title = str(progress_title or "Progress")
        self._progress_initial = str(progress_initial or "")
        self._progress_log_prefix = str(progress_log_prefix or "")
        self._root = None
        self._thread = None
        self._ready = threading.Event()
        self._running = False
        self._canvases: dict[int, tk.Canvas] = {}
        self._texts: dict[int, tk.Text] = {}
        self._status_vars: dict[int, tk.StringVar] = {}
        self._player_endpoint: dict[int, str] = {}
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._stream_threads: dict[int, threading.Thread] = {}
        self._stream_stops: dict[int, threading.Event] = {}
        self._logged_steps: dict[int, set[int]] = {}
        # Side-panel status - surfaced both in window title (for
        # at-a-glance) and in the right-side progress panel (for the
        # full per-round table).
        self._panel_status: str = ""
        self._panel_progress: str = ""
        self._panel_status_var: tk.StringVar | None = None
        self._panel_progress_text: tk.Text | None = None
        self._status_busy: bool = False
        self._status_anim_idx: int = 0

    def _display_player(self, player_index: int) -> int:
        return player_index + 1

    def set_player_endpoint(self, player_index: int, text: str) -> None:
        """Prefix shown at the FRONT of a player's status line, e.g.
        ``"ip=.. port=.."`` - mirrors the solo viewer's status bar."""
        self._player_endpoint[player_index] = str(text or "")

    # -- Status / progress side-panel API -------------------------------------
    # A driver (e.g. the IDC runner) calls viewer.set_status / set_progress on whatever
    # viewer it was given. LiveViewer (solo) implements these via its
    # status bar / right-side progress panel; TwoPlayerLiveViewer mirrors
    # the same API and renders the content into its own right-side
    # progress panel (set via show_progress_panel=True at construction).
    def set_status(self, status: str, busy: bool = False) -> None:
        self._panel_status = str(status or "")
        self._status_busy = bool(busy)
        self._status_anim_idx = 0
        self._apply_title_update()
        self._apply_status_var_update()

    def set_progress(self, text: str) -> None:
        self._panel_progress = str(text or "")
        self._apply_progress_panel_update()
        # Also keep an info log so users without GUI still see top line.
        first_line = self._panel_progress.split("\n", 1)[0]
        if first_line:
            prefix = f"{self._progress_log_prefix} " if self._progress_log_prefix else ""
            logger.info("%s%s", prefix, first_line)

    def _apply_title_update(self) -> None:
        if not self._root or not self._running:
            return
        try:
            base = self._title
            suffix = self._panel_status.strip()
            new_title = f"{base} - {suffix}" if suffix else base
            self._root.after(0, lambda: self._root.title(new_title))
        except Exception:
            pass

    def _apply_status_var_update(self) -> None:
        if self._panel_status_var is None or not self._root or not self._running:
            return
        try:
            self._root.after(0, lambda: self._panel_status_var.set(self._panel_status))
        except Exception:
            pass

    def _animate_status(self) -> None:
        # Heartbeat: while a driver marked the status "busy" (e.g. a long
        # blocking reflection), cycle trailing dots so the panel visibly
        # shows it's working rather than looking frozen.
        try:
            if (
                self._status_busy
                and self._panel_status_var is not None
                and self._running
            ):
                self._status_anim_idx = (self._status_anim_idx + 1) % 4
                self._panel_status_var.set(
                    f"{self._panel_status}{'.' * self._status_anim_idx}"
                )
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

    def _apply_progress_panel_update(self) -> None:
        if self._panel_progress_text is None or not self._root or not self._running:
            return
        text_widget = self._panel_progress_text
        content = self._panel_progress
        def _do_update():
            try:
                text_widget.configure(state=tk.NORMAL)
                text_widget.delete("1.0", tk.END)
                for line in content.split("\n"):
                    text_widget.insert(
                        tk.END, line + "\n", self._progress_line_tag(line)
                    )
                text_widget.configure(state=tk.DISABLED)
            except Exception:
                pass
        try:
            self._root.after(0, _do_update)
        except Exception:
            pass

    # -- Per-episode reset (mirrors solo LiveViewer.clear_log) -----------
    # Solo viewer wipes its per-step reasoning panel at every episode
    # boundary so old steps don't pile up indefinitely. Coop viewer needs
    # the same per-player to avoid 200KB+ Text widgets after a few rounds.
    def clear_player_logs(
        self,
        player_index: int | None = None,
        header: str = "",
    ) -> None:
        """Wipe one player's thinking panel (or both when player_index is None)."""
        if not self._running or not self._root:
            return
        targets = (
            [player_index] if player_index is not None else list(self._texts.keys())
        )
        for pid in targets:
            text = self._texts.get(pid)
            if text is None:
                continue
            # Reset our "already-logged this step" tracker so the new
            # episode starts step numbering fresh.
            self._logged_steps[pid] = set()
            self._schedule_clear(text, header)

    def clear_log(self, header: str = "") -> None:
        """API parity with solo LiveViewer.clear_log - clears both players."""
        self.clear_player_logs(player_index=None, header=header)

    def _schedule_clear(self, text_widget: tk.Text, header: str) -> None:
        def _do_clear():
            try:
                text_widget.configure(state=tk.NORMAL)
                text_widget.delete("1.0", tk.END)
                if header:
                    text_widget.insert(tk.END, f"{header}\n", "step")
                text_widget.configure(state=tk.DISABLED)
            except Exception:
                pass
        try:
            self._root.after(0, _do_clear)
        except Exception:
            pass

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_gui, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        self._running = False
        self.stop_streaming()
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def start_streaming_player(self, player_index: int, client, fps: int = 30) -> None:
        """Start realtime screenshot streaming for one player."""
        self.stop_streaming_player(player_index)
        stop = threading.Event()
        thread = threading.Thread(
            target=self._stream_loop,
            args=(player_index, client, fps, stop),
            daemon=True,
            name=f"TwoPlayerLiveViewer-p{player_index}",
        )
        self._stream_stops[player_index] = stop
        self._stream_threads[player_index] = thread
        thread.start()
        logger.info("Player %s viewer streaming started at %d fps", player_index, fps)

    def stop_streaming_player(self, player_index: int) -> None:
        """Stop realtime screenshot streaming for one player."""
        stop = self._stream_stops.pop(player_index, None)
        if stop is not None:
            stop.set()
        thread = self._stream_threads.pop(player_index, None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def stop_streaming(self) -> None:
        """Stop all player stream threads."""
        for player_index in list(self._stream_threads):
            self.stop_streaming_player(player_index)

    def update_player(
        self,
        player_index: int,
        image: Image.Image | None = None,
        *,
        step: int | None = None,
        reason: str = "",
        action: str = "",
        status: str = "",
    ) -> None:
        if not self._running or not self._root:
            return
        try:
            self._root.after(
                0,
                self._update_player_impl,
                player_index,
                image.copy() if image is not None else None,
                step,
                reason,
                action,
                status,
            )
        except Exception:
            pass

    def _stream_loop(self, player_index: int, client, fps: int, stop: threading.Event) -> None:
        interval = 1.0 / max(1, fps)
        while self._running and not stop.is_set():
            t0 = time.monotonic()
            try:
                result = client.screenshot()
                image = Image.open(io.BytesIO(result["data"]))
                image.load()
                self.update_player(player_index, image)
            except Exception as exc:
                logger.debug("Player %s stream frame error: %s", player_index, exc)
                if stop.wait(0.1):
                    break
                continue

            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0 and stop.wait(sleep_time):
                break

    def _run_gui(self) -> None:
        self._root = tk.Tk()
        self._root.title(self._title)
        divider_w = 4
        total_w = self._width * 2 + divider_w + (
            self._progress_panel_width if self._show_progress_panel else 0
        )
        self._root.geometry(f"{total_w}x{self._height + 260}")
        self._root.configure(bg="#111")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        columns = tk.Frame(self._root, bg="#111")
        columns.pack(fill=tk.BOTH, expand=True)

        for pid in (0, 1):
            if pid == 1:
                # Vertical divider so the two player views don't look joined.
                divider = tk.Frame(columns, bg="#555", width=divider_w)
                divider.pack(side=tk.LEFT, fill=tk.Y)
                divider.pack_propagate(False)
            frame = tk.Frame(columns, bg="#111")
            frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            title = tk.Label(
                frame,
                text=f"Player {self._display_player(pid)}",
                bg="#1f1f1f",
                fg="#ffffff",
                font=("Consolas", 13, "bold"),
            )
            title.pack(fill=tk.X)

            canvas = tk.Canvas(
                frame,
                width=self._width,
                height=self._height,
                bg="black",
                highlightthickness=0,
            )
            canvas.pack(fill=tk.BOTH, expand=True)
            self._canvases[pid] = canvas

            status_var = tk.StringVar(value="Waiting for reset...")
            status = tk.Label(
                frame,
                textvariable=status_var,
                bd=1,
                relief=tk.SUNKEN,
                anchor=tk.W,
                bg="#222",
                fg="#0f0",
                font=("Consolas", 9),
            )
            status.pack(fill=tk.X)
            self._status_vars[pid] = status_var

            text = tk.Text(
                frame,
                height=12,
                bg="#171717",
                fg="#d0d0d0",
                font=("Consolas", 9),
                wrap=tk.WORD,
                state=tk.DISABLED,
                padx=8,
                pady=6,
                borderwidth=0,
                highlightthickness=0,
            )
            text.pack(fill=tk.BOTH)
            text.tag_configure("step", foreground="#78dce8")
            text.tag_configure("label", foreground="#ffd866")
            text.tag_configure("reason", foreground="#d0d0d0")
            text.tag_configure("action", foreground="#a9dc76")
            self._texts[pid] = text

        # -- Status / progress side-panel (right column) ---------------------------
        if self._show_progress_panel:
            panel = tk.Frame(
                columns, bg="#111", width=self._progress_panel_width,
            )
            panel.pack(side=tk.LEFT, fill=tk.BOTH)
            panel.pack_propagate(False)

            header = tk.Label(
                panel,
                text=self._progress_title,
                bg="#1f1f1f",
                fg="#ffffff",
                font=("Consolas", 13, "bold"),
            )
            header.pack(fill=tk.X)

            self._panel_status_var = tk.StringVar(value=self._progress_initial)
            status_label = tk.Label(
                panel,
                textvariable=self._panel_status_var,
                bd=1,
                relief=tk.SUNKEN,
                anchor=tk.W,
                bg="#222",
                fg="#ffd866",
                font=("Consolas", 9, "bold"),
            )
            status_label.pack(fill=tk.X)

            progress_text = tk.Text(
                panel,
                bg="#171717",
                fg="#d0d0d0",
                font=("Consolas", 9),
                wrap=tk.WORD,
                state=tk.DISABLED,
                padx=8,
                pady=6,
                borderwidth=0,
                highlightthickness=0,
            )
            progress_text.pack(fill=tk.BOTH, expand=True)
            progress_text.tag_configure(
                "p_hdr", foreground="#ffffff", font=("Consolas", 9, "bold")
            )
            progress_text.tag_configure(
                "p_cur", foreground="#ffd866", font=("Consolas", 9, "bold")
            )
            progress_text.tag_configure("p_summary", foreground="#78dce8")
            progress_text.tag_configure("p_fail", foreground="#ff6188")
            self._panel_progress_text = progress_text

            # If set_status / set_progress were called before the GUI was
            # ready, flush them now.
            if self._panel_status:
                self._panel_status_var.set(self._panel_status)
            if self._panel_progress:
                progress_text.configure(state=tk.NORMAL)
                progress_text.insert(tk.END, self._panel_progress)
                progress_text.configure(state=tk.DISABLED)

        self._root.after(450, self._animate_status)
        self._ready.set()
        self._root.mainloop()
        self._running = False

    def _update_player_impl(
        self,
        player_index: int,
        image: Image.Image | None,
        step: int | None,
        reason: str,
        action: str,
        status: str,
    ) -> None:
        if status and player_index in self._status_vars:
            endpoint = self._player_endpoint.get(player_index, "")
            self._status_vars[player_index].set(
                f"{endpoint} | {status}" if endpoint else status
            )
        if image is not None:
            self._set_image(player_index, image)
        if step is not None:
            logged = self._logged_steps.setdefault(player_index, set())
            if step in logged:
                return
            logged.add(step)
            self._append_log(player_index, step, reason, action)

    def _set_image(self, player_index: int, image: Image.Image) -> None:
        canvas = self._canvases.get(player_index)
        if canvas is None:
            return

        canvas_w = canvas.winfo_width()
        canvas_h = canvas.winfo_height()
        if canvas_w <= 1 or canvas_h <= 1:
            canvas_w, canvas_h = self._width, self._height

        img_w, img_h = image.size
        scale = min(canvas_w / img_w, canvas_h / img_h)
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        image = image.resize((new_w, new_h), Image.LANCZOS)

        photo = ImageTk.PhotoImage(image)
        self._photos[player_index] = photo
        canvas.delete("all")
        canvas.create_image(
            (canvas_w - new_w) // 2,
            (canvas_h - new_h) // 2,
            anchor=tk.NW,
            image=photo,
        )

    def _append_log(
        self,
        player_index: int,
        step: int,
        reason: str,
        action: str,
    ) -> None:
        text = self._texts.get(player_index)
        if text is None:
            return
        text.config(state=tk.NORMAL)
        text.insert(tk.END, f"--- Step {step} ---\n", "step")
        if reason:
            text.insert(tk.END, "thinking: ", "label")
            text.insert(tk.END, f"{reason}\n", "reason")
        if action:
            text.insert(tk.END, "action: ", "label")
            text.insert(tk.END, f"{action}\n", "action")
        text.insert(tk.END, "\n")
        text.see(tk.END)
        text.config(state=tk.DISABLED)

    def _on_close(self) -> None:
        self._running = False
        self._root.destroy()
