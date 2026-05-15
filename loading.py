from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from enum import Enum, auto
from typing import Callable

# No longer need PIL, Path, or config for the icon

logger = logging.getLogger(__name__)

APP_VERSION = "2.1.0"
class LoadingScreen:
    class AnimationState(Enum):
        INITIAL_DELAY = auto()
        DRAWING_BRACKETS = auto()
        SCANNING = auto()
        SHOWING_CHECKMARK = auto()
        PAUSE_AT_END = auto()
        RESETTING = auto()



    """A simple loading screen to show while the main application initializes."""

    def __init__(self, root: tk.Tk, on_load_complete: Callable[[], None]):
        """
        Initialize the loading screen.

        Args:
            root: The main Tkinter root window.
            on_load_complete: A callback function to execute when loading is "done".
        """
        self.root = root
        self.on_load_complete = on_load_complete
        self.frame: ttk.Frame | None = None
        self.status_label: ttk.Label | None = None
        self.animation_after_id: str | None = None
        self.animation_dots = 0
        self.spinner_canvas: tk.Canvas | None = None
        self.spinner_after_id: str | None = None
        self.spinner_angle: int = 0
        self.face_scan_canvas: tk.Canvas | None = None
        self.face_scan_after_id: str | None = None
        self.animation_state = self.AnimationState.INITIAL_DELAY
        self.animation_progress = 0.0
        
        self._setup_ui()


    def _setup_ui(self) -> None:
        """Build and display the loading screen widgets."""
        self.frame = ttk.Frame(self.root, style="App.TFrame")
        self.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure((0, 4), weight=1) # Add more weight to top/bottom rows for centering

        content_frame = ttk.Frame(self.frame, style="App.TFrame")
        content_frame.grid(row=1, column=0)

        # --- Face Scan Animation ---
        self.face_scan_canvas = tk.Canvas(content_frame, width=100, height=100, bg="#e9eef5", highlightthickness=0)
        self.face_scan_canvas.pack(pady=(0, 20))
        self._animate_face_scan()

        # --- Title ---
        ttk.Label(
            content_frame,
            text="RollCount",
            font=("Georgia", 48, "bold"),
            foreground="#12324a",
            background="#e9eef5",
        ).pack(pady=(0, 12))

        # --- Status Label (for animation) ---
        self.status_label = ttk.Label(
            content_frame,
            text="Initializing attendance system...",
            font=("Georgia", 14),
            foreground="#5f7387",
            background="#e9eef5",
        )
        self.status_label.pack(pady=(0, 20))

        # --- Loading Spinner ---
        self.spinner_canvas = tk.Canvas(content_frame, width=50, height=50, bg="#e9eef5", highlightthickness=0)
        self.spinner_canvas.pack(pady=10)
        self._animate_spinner()

        # --- Footer ---
        footer_frame = ttk.Frame(self.frame, style="App.TFrame")
        footer_frame.grid(row=3, column=0, pady=(0, 20))
        ttk.Label(
            footer_frame,
            text=f"Version {APP_VERSION} | Powered by OpenCV & Tkinter",
            font=("Georgia", 9),
            foreground="#8a9bb0",
            background="#e9eef5",
        ).pack()

        self._animate_status_text()

    def _animate_status_text(self) -> None:
        """Animates the ellipsis on the status label."""
        if not self.status_label:
            return
        self.animation_dots = (self.animation_dots + 1) % 4
        dots = "." * self.animation_dots
        self.status_label.config(text=f"Initializing attendance system{dots}")
        self.animation_after_id = self.root.after(400, self._animate_status_text)

    def _animate_face_scan(self) -> None:
        """Animates a face being scanned on the canvas."""
        canvas = self.face_scan_canvas
        if not canvas:
            return

        canvas.delete("all")

        # --- State Machine for Animation ---
        state = self.animation_state

        if state == self.AnimationState.INITIAL_DELAY:
            self.animation_progress += 0.1
            if self.animation_progress >= 1.0:
                self.animation_state = self.AnimationState.DRAWING_BRACKETS
                self.animation_progress = 0.0

        elif state == self.AnimationState.DRAWING_BRACKETS:
            self._draw_face(canvas)
            self._draw_brackets(canvas, self.animation_progress)
            self.animation_progress += 0.1
            if self.animation_progress >= 1.0:
                self.animation_state = self.AnimationState.SCANNING
                self.animation_progress = 0.0

        elif state == self.AnimationState.SCANNING:
            self._draw_face(canvas)
            self._draw_brackets(canvas, 1.0)
            self._draw_scan_line(canvas, self.animation_progress)
            self.animation_progress += 0.05
            if self.animation_progress >= 1.0:
                self.animation_state = self.AnimationState.SHOWING_CHECKMARK
                self.animation_progress = 0.0

        elif state == self.AnimationState.SHOWING_CHECKMARK:
            self._draw_checkmark(canvas, self.animation_progress)
            self.animation_progress += 0.1
            if self.animation_progress >= 1.0:
                self.animation_state = self.AnimationState.PAUSE_AT_END
                self.animation_progress = 0.0

        elif state == self.AnimationState.PAUSE_AT_END:
            self._draw_checkmark(canvas, 1.0)
            # By doing nothing here, the animation will stay in this state,
            # continuously drawing the checkmark until the loading screen is destroyed.

        elif state == self.AnimationState.RESETTING:
            self.animation_state = self.AnimationState.INITIAL_DELAY
            self.animation_progress = 0.0

        self.face_scan_after_id = self.root.after(30, self._animate_face_scan)

    def _draw_face(self, canvas: tk.Canvas) -> None:
        """Draws the static face icon."""
        canvas.create_oval(20, 20, 80, 80, outline="#5f7387", width=2)
        canvas.create_oval(35, 40, 45, 50, fill="#5f7387", outline="")
        canvas.create_oval(55, 40, 65, 50, fill="#5f7387", outline="")
        canvas.create_arc(35, 50, 65, 75, start=0, extent=-180, style=tk.ARC, outline="#5f7387", width=2)

    def _draw_brackets(self, canvas: tk.Canvas, progress: float) -> None:
        """Draws the four corner brackets, animating them inwards."""
        p = min(1.0, max(0.0, progress))
        length = 15
        offset = 30 * (1 - p)

        # Top-left
        canvas.create_line(10 + offset, 10, 10 + offset + length, 10, fill="#007bff", width=3)
        canvas.create_line(10, 10 + offset, 10, 10 + offset + length, fill="#007bff", width=3)
        # Top-right
        canvas.create_line(90 - offset, 10, 90 - offset - length, 10, fill="#007bff", width=3)
        canvas.create_line(90, 10 + offset, 90, 10 + offset + length, fill="#007bff", width=3)
        # Bottom-left
        canvas.create_line(10 + offset, 90, 10 + offset + length, 90, fill="#007bff", width=3)
        canvas.create_line(10, 90 - offset, 10, 90 - offset - length, fill="#007bff", width=3)
        # Bottom-right
        canvas.create_line(90 - offset, 90, 90 - offset - length, 90, fill="#007bff", width=3)
        canvas.create_line(90, 90 - offset, 90, 90 - offset - length, fill="#007bff", width=3)

    def _draw_scan_line(self, canvas: tk.Canvas, progress: float) -> None:
        """Draws the horizontal scan line moving downwards."""
        p = min(1.0, max(0.0, progress))
        y = 20 + (60 * p)
        canvas.create_line(20, y, 80, y, fill="#007bff", width=2)

    def _draw_checkmark(self, canvas: tk.Canvas, progress: float) -> None:
        """Draws a success circle and checkmark."""
        p = min(1.0, max(0.0, progress))
        if p < 0.1: return

        # Circle
        canvas.create_oval(30, 30, 70, 70, fill="#1f9d55", outline="")
        # Checkmark
        if p > 0.3:
            canvas.create_line(40, 50, 48, 58, 62, 42, fill="#ffffff", width=4, capstyle=tk.ROUND) # type: ignore[reportCallIssue]



    def _animate_spinner(self) -> None:
        """Animates the loading spinner on the canvas."""
        if not self.spinner_canvas:
            return

        self.spinner_canvas.delete("all")
        self.spinner_angle = (self.spinner_angle + 12) % 360

        # Draw a spinning arc
        self.spinner_canvas.create_arc(
            (4, 4, 46, 46),  # Bounding box for the circle
            start=self.spinner_angle,
            extent=150,  # The length of the arc
            style=tk.ARC,
            outline="#007bff",
            width=4,
        )

        self.spinner_after_id = self.root.after(30, self._animate_spinner)

    def destroy(self) -> None:
        """Destroy the loading screen widgets."""
        if self.animation_after_id:
            self.root.after_cancel(self.animation_after_id)
        if self.face_scan_after_id:
            self.root.after_cancel(self.face_scan_after_id)
        if self.spinner_after_id:
            self.root.after_cancel(self.spinner_after_id)
        if self.frame:
            self.frame.destroy()
            logger.info("Loading screen destroyed.")