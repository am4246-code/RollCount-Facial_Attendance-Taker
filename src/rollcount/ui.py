from __future__ import annotations

import logging
import tkinter as tk
import calendar
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Literal

import cv2
from PIL import Image, ImageTk
import numpy as np

from .attendance import AttendanceLogger
from .camera import CameraAttendanceSession, _FACE_LABEL_FONT
from .config import (
    AppConfig,
    DEFAULT_ATTENDANCE_FILE,
    MAX_TEMPLATE_DISTANCE,
    MAX_THRESHOLD,
    MIN_TEMPLATE_DISTANCE,
    MIN_THRESHOLD,
    ROOT_DIR,
    TEMP_DIR,
    ensure_directories,
    load_app_config,
    save_app_config,
)
from .detector import FaceDetector
from .registry import StudentRegistry
from .stats import SessionStats, DuplicateDetectionFilter


logger = logging.getLogger(__name__)

# Unicode symbols for better UI
ICON_CHECKMARK = "✔"  # Better checkmark
ICON_REJECT = "✘"     # Better X symbol
ICON_STATS = "📊"     # Stats
ICON_TIMER = "⏱"      # Timer
ICON_ENROLL = "🧑‍🎓"
ICON_DETECT = "📸"
ICON_DAILY = "📋"
ICON_WEEKLY = "📅"
ICON_MONTHLY = "📆"
ICON_SETTINGS = "🛠️"


class RollCountApp:
    """Main application class for the RollCount UI."""

    def __init__(self, root: tk.Tk) -> None:
        """Initialize the application."""
        self.root = root
        self.root.title("RollCount (Face Attendance Taker)")
        self.root.geometry("1440x940")
        self.root.minsize(1320, 860)

        self.registry = StudentRegistry()
        self.attendance_logger = AttendanceLogger()
        self.selected_image_path: Path | None = None
        self.selected_image_paths: list[Path] = []
        self.captured_image_paths: list[Path] = []
        self.app_config = load_app_config()
        self.session_config_summary = self._build_config_summary(self.app_config)
        self.active_session: CameraAttendanceSession | None = None
        self.video_after_id: str | None = None
        self.video_photo = None
        self.badge_photo = None
        self.app_icon = None
        self.present_badge_after_id: str | None = None
        self.present_badge_message: str | None = None
        self.notebook: ttk.Notebook | None = None
        self.datetime_label: ttk.Label | None = None
        self.attendance_tab: ttk.Frame | None = None
        self.capture_window: tk.Toplevel | None = None
        self.capture_device = None
        self.capture_after_id: str | None = None
        self.capture_last_frame = None
        self.capture_photo = None
        self.capture_preview_label: tk.Label | None = None
        self.capture_status_label: ttk.Label | None = None
        self.capture_detector: FaceDetector | None = None
        self.is_auto_capturing = False
        self.last_auto_capture_time: datetime | None = None
        self.auto_capture_hold_steady_frames = 0
        self.use_pictures_button: ttk.Button | None = None
        self.daily_report_table: ttk.Treeview | None = None
        self.weekly_report_table: ttk.Treeview | None = None
        self.monthly_report_canvas: tk.Canvas | None = None
        self.current_retake_student_id: str | None = None
        self.is_capturing_for_retake: bool = False
        self.capture_count_label: ttk.Label | None = None
        self.gallery_image_labels: list[tk.Label] = []
        self.capture_complete_overlay: tk.Frame | None = None
        self.gallery_photos: list[ImageTk.PhotoImage] = []
        self.capture_complete_subtitle: tk.Label | None = None
        self.unenroll_overlay: tk.Frame | None = None
        self.unenroll_after_id: str | None = None

        # Session statistics and filters
        self.session_stats: SessionStats | None = None
        self.duplicate_filter = DuplicateDetectionFilter(cooldown_seconds=2.0)
        self.stats_label: ttk.Label | None = None

        # Bind keyboard shortcuts
        self.root.bind('<Return>', self._on_return_key)
        self.root.bind('<Escape>', self._on_escape_key)
        self.root.bind('<space>', self._on_space_key)

        # Defer main UI build to allow a loading screen to show first.
        # The main app entry point will call build_main_ui().

    def build_main_ui(self) -> None:
        """Builds the main application interface after the loading screen."""
        self._load_app_icon()
        if self.app_icon:
            self.root.iconphoto(True, self.app_icon)  # type: ignore[arg-type]
        self._configure_styles()
        self._build_layout()
        self.refresh_student_table()
        self.refresh_attendance_log()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._update_datetime()
        logger.info("RollCount application initialized")

    def _load_app_icon(self) -> None:
        """Load and prepare the application icon."""
        icon_path = ROOT_DIR / "icon.png"
        if icon_path.exists():
            try:
                image = Image.open(icon_path)
                image.thumbnail((48, 48))
                self.app_icon = ImageTk.PhotoImage(image)
            except Exception as e:
                logger.warning(f"Could not load app icon: {e}")

    def _configure_styles(self) -> None:
        self.colors = {
            "bg": "#e9eef5",
            "panel": "#ffffff",
            "panel_alt": "#e7f3ff", # Light blue
            "ink": "#12324a",
            "muted": "#5f7387",
            "accent": "#007bff", # Main blue accent
            "accent_soft": "#cce5ff", # Lighter blue
            "border": "#c8d7e6",
            "success": "#1f9d55",
            "warning": "#d9822b",
            "danger": "#e53935",
            "nav": "#0069d9", # Darker blue for nav
            "nav_active": "#0056b3", # Active nav state
        }
        self.root.configure(bg=self.colors["bg"])
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure(
            "App.TFrame",
            background=self.colors["bg"],
        )
        style.configure(
            "Card.TFrame",
            background=self.colors["panel"],
            relief="flat",
        )
        style.configure(
            "Card.TLabelframe",
            background=self.colors["panel"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self.colors["panel"],
            foreground=self.colors["ink"],
            font=("Georgia", 11, "bold"),
        )
        style.configure(
            "Hero.TLabel",
            background=self.colors["accent"],
            foreground="#ffffff",
            font=("Georgia", 24, "bold"),
        )
        style.configure(
            "Hero.TFrame",
            background=self.colors["accent"],
            relief="solid",
            borderwidth=1,
            bordercolor=self.colors["accent"],
        )
        style.configure(
            "Subhero.TLabel",
            background=self.colors["bg"],
            foreground=self.colors["muted"],
            font=("Georgia", 11),
        )
        style.configure(
            "DateTime.TLabel",
            background=self.colors["accent"],
            foreground="#ffffff",
            font=("Georgia", 14),
        )
        style.configure(
            "Banner.TFrame",
            background=self.colors["panel"],
            relief="flat",
        )
        style.configure(
            "BannerTitle.TLabel",
            background=self.colors["panel"],
            foreground="#ffffff",
            font=("Georgia", 28, "bold"),
        )
        style.configure(
            "BannerBadge.TLabel",
            background=self.colors["danger"],
            foreground="#ffffff",
            font=("Georgia", 11, "bold"),
            padding=(14, 6),
        )
        style.configure(
            "Section.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["ink"],
            font=("Georgia", 11, "bold"),
        )
        style.configure(
            "Modern.TButton",
            background=self.colors["accent"],
            foreground="#ffffff",
            padding=(14, 10),
            borderwidth=0,
            focusthickness=0,
            font=("Georgia", 10, "bold"),
        )
        style.map(
            "Modern.TButton",
            background=[("active", self.colors["nav_active"]), ("pressed", "#004494")],
            foreground=[("disabled", "#f8fbff")],
        )
        style.configure(
            "Secondary.TButton",
            background=self.colors["panel_alt"],
            foreground=self.colors["ink"],
            padding=(14, 10),
            borderwidth=0,
            focusthickness=0,
            font=("Georgia", 10, "bold"),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#dde7f2"), ("pressed", "#d1dce8")],
        )
        style.configure(
            "Confirm.TButton",
            background=self.colors["success"],
            foreground="#ffffff",
            padding=(18, 12),
            borderwidth=0,
            focusthickness=0,
            font=("Georgia", 14, "bold"),
        )
        style.map(
            "Confirm.TButton",
            background=[("active", "#16834a"), ("pressed", "#12693b")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure(
            "Reject.TButton",
            background="#fbe3e4",
            foreground="#9f2f34",
            padding=(18, 12),
            borderwidth=0,
            focusthickness=0,
            font=("Georgia", 14, "bold"),
        )
        style.map(
            "Reject.TButton",
            background=[("active", "#f6d4d6"), ("pressed", "#efc1c5")],
            foreground=[("active", "#8b2328"), ("pressed", "#7b1d22")],
        )
        style.configure(
            "Danger.TButton",
            background=self.colors["danger"],
            foreground="#ffffff",
            padding=(12, 8),
            borderwidth=0,
            font=("Georgia", 10, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#cf2b28"), ("pressed", "#b62421")],
        )
        style.configure(
            "Modern.TEntry",
            fieldbackground="#ffffff",
            bordercolor=self.colors["border"],
            lightcolor=self.colors["border"],
            darkcolor=self.colors["border"],
            padding=8,
        )
        style.configure(
            "Modern.TNotebook",
            background=self.colors["bg"],
            borderwidth=0,
        )
        style.configure(
            "Modern.TNotebook.Tab",
            padding=(18, 10),
            background=self.colors["panel_alt"],
            foreground=self.colors["muted"],
            font=("Georgia", 10, "bold"),
        )
        style.map(
            "Modern.TNotebook.Tab",
            background=[("selected", self.colors["panel"])],
            foreground=[("selected", self.colors["ink"])],
        )
        style.configure(
            "Modern.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground=self.colors["ink"],
            bordercolor=self.colors["border"],
            rowheight=28,
            font=("Georgia", 10),
        )
        style.configure(
            "Modern.Treeview.Heading",
            background=self.colors["panel_alt"],
            foreground=self.colors["ink"],
            font=("Georgia", 10, "bold"),
            relief="flat",
        )
        style.map(
            "Modern.Treeview",
            background=[("selected", self.colors["accent_soft"])],
            foreground=[("selected", self.colors["ink"])],
        )
        style.configure(
            "Nav.TFrame",
            background=self.colors["nav"],
        )
        style.configure(
            "Nav.TButton",
            background=self.colors["nav"],
            foreground="#ffffff",
            font=("Georgia", 12),
            borderwidth=0,
            padding=(14, 12),
        )
        style.map(
            "Nav.TButton",
            background=[("active", self.colors["nav_active"]), ("pressed", self.colors["nav_active"])],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure(
            "Sidebar.TFrame",
            background=self.colors["panel"],
        )
        style.configure(
            "Sidebar.TButton",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
            font=("Georgia", 11, "bold"),
            padding=(18, 12),
            anchor="w",
            borderwidth=0,
        )
        style.map("Sidebar.TButton", background=[("selected", self.colors["panel_alt"])], foreground=[("selected", self.colors["ink"])])

    def _build_layout(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame", padding=14)
        shell.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(shell, style="Hero.TFrame", padding=(18, 14))
        header.pack(fill="x", pady=(0, 12))
        header.columnconfigure(0, weight=1) # Left spacer
        header.columnconfigure(2, weight=1) # Right spacer
        # Columns 1 (title), 3 (live), and 4 (datetime) will have natural width

        if self.app_icon:
            icon_label = ttk.Label(
                header,
                image=self.app_icon,
                style="Hero.TFrame", # Use same background as header
            )
            icon_label.grid(row=0, column=0, sticky="w", padx=(0, 20))

        ttk.Label(
            header,
            text="🧑‍🏫 RollCount (Face Attendence Taker)",
            style="Hero.TLabel",
        ).grid(row=0, column=1, sticky="") # Center in its column
        self.live_indicator = ttk.Label(
            header,
            text="● LIVE",
            style="BannerBadge.TLabel",
        )
        # The indicator will be placed later when a session starts
        self.live_indicator.grid(row=0, column=3, sticky="e", padx=(12, 0))
        self.live_indicator.grid_remove() # Hide it initially

        self.datetime_label = ttk.Label(
            header,
            text="",
            style="DateTime.TLabel",
        )
        self.datetime_label.grid(row=0, column=4, sticky="e", padx=(20, 0))

        main_content = ttk.Frame(shell, style="App.TFrame")
        main_content.pack(fill="both", expand=True)
        main_content.columnconfigure(1, weight=1)
        main_content.rowconfigure(0, weight=1)

        # --- Vertical Sidebar ---
        sidebar = ttk.Frame(main_content, style="Sidebar.TFrame", width=240)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        sidebar.pack_propagate(False)

        self.sidebar_buttons: list[ttk.Button] = []
        
        def create_sidebar_button(parent, text, icon, tab_index):
            btn = ttk.Button(
                parent,
                text=f"{icon}{text}",
                style="Sidebar.TButton",
                command=lambda: self._select_tab(tab_index),
            )
            btn.pack(fill="x", pady=1, padx=8)
            self.sidebar_buttons.append(btn)

        create_sidebar_button(sidebar, "Enrollment", ICON_ENROLL, 0)
        create_sidebar_button(sidebar, "Take Attendance", ICON_DETECT, 1)
        create_sidebar_button(sidebar, "Daily Log", ICON_DAILY, 2)
        create_sidebar_button(sidebar, "Weekly Log", ICON_WEEKLY, 3)
        create_sidebar_button(sidebar, "Monthly Log", ICON_MONTHLY, 4)
        create_sidebar_button(sidebar, "Settings", ICON_SETTINGS, 5)

        # --- Notebook for content ---
        notebook = ttk.Notebook(main_content, style="Modern.TNotebook")
        self.notebook = notebook

        # To hide the tabs, we can override the layout.
        # This is a more robust way to ensure tabs are not visible.
        style = ttk.Style()
        style.layout("Modern.TNotebook.Tab", [])

        notebook.grid(row=0, column=1, sticky="nsew")

        register_tab = ttk.Frame(notebook, padding=18, style="Card.TFrame")
        attendance_tab = ttk.Frame(notebook, padding=16, style="Card.TFrame")
        self.attendance_tab = attendance_tab
        daily_report_tab = ttk.Frame(notebook, padding=16, style="Card.TFrame")
        weekly_report_tab = ttk.Frame(notebook, padding=16, style="Card.TFrame")
        monthly_report_tab = ttk.Frame(notebook, padding=16, style="Card.TFrame")
        settings_tab = ttk.Frame(notebook, padding=20, style="Card.TFrame")

        # Add a dummy frame to hide the actual tabs
        dummy_frame = ttk.Frame(notebook)
        notebook.add(dummy_frame, text="")

        notebook.add(register_tab, text="Enrollment")
        notebook.add(attendance_tab, text="Take Attendance")
        notebook.add(daily_report_tab, text="Daily Log")
        notebook.add(weekly_report_tab, text="Weekly Log")
        notebook.add(monthly_report_tab, text="Monthly Log")
        notebook.add(settings_tab, text="Settings")
        notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self._build_register_tab(register_tab)
        self._build_attendance_tab(attendance_tab)
        self._build_daily_report_tab(daily_report_tab)
        self._build_weekly_report_tab(weekly_report_tab)
        self._build_monthly_report_tab(monthly_report_tab)
        self._build_settings_tab(settings_tab)
        
        notebook.hide(0) # Hide the dummy frame's tab
        self._select_tab(0) # Select the first tab by default

    def _update_datetime(self) -> None:
        """Update the date and time label in the header."""
        if self.datetime_label:
            now = datetime.now()
            # Format: e.g., "Tuesday, April 29, 2025 | 02:30:15 PM"
            datetime_str = now.strftime("%A, %B %d, %Y | %I:%M:%S %p")
            self.datetime_label.config(text=datetime_str)
        self.root.after(1000, self._update_datetime)

    def _build_register_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(2, weight=1)

        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(0, weight=1)

        ttk.Label(
            header_frame,
            text="🗂️Enrollment🗂️",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="")

        form_card = ttk.LabelFrame(parent, text="Student Registration", style="Card.TLabelframe", padding=18)
        form_card.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        form_card.columnconfigure(0, weight=1)
        table_card = ttk.LabelFrame(parent, text="Registered Students", style="Card.TLabelframe", padding=18)
        table_card.grid(row=1, column=1, sticky="nsew", rowspan=2)
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        ttk.Label(form_card, text="Student ID", style="Section.TLabel", anchor="center").grid(row=0, column=0, sticky="ew")
        self.student_id_var = tk.StringVar()
        ttk.Entry(form_card, textvariable=self.student_id_var, width=36, style="Modern.TEntry").grid(
            row=1, column=0, sticky="ew", pady=(6, 12), padx=16
        )

        ttk.Label(form_card, text="Full Name", style="Section.TLabel", anchor="center").grid(row=2, column=0, sticky="ew")
        self.full_name_var = tk.StringVar()
        ttk.Entry(form_card, textvariable=self.full_name_var, width=36, style="Modern.TEntry").grid(
            row=3, column=0, sticky="ew", pady=(6, 12), padx=16
        )

        ttk.Button(
            form_card,
            text="Take Live Pictures",
            command=self.open_live_capture,
            style="Modern.TButton",
        ).grid(row=4, column=0, pady=(0, 0))
        self.selected_image_label = ttk.Label(
            form_card,
            text="No live pictures captured",
            style="Subhero.TLabel",
            wraplength=280,
            justify="center",
            anchor="center",
        )
        self.selected_image_label.grid(row=5, column=0, sticky="ew", pady=(10, 14))

        ttk.Button(
            form_card,
            text="Register New Student",
            command=self.register_student,
            style="Modern.TButton",
        ).grid(row=6, column=0)

        ttk.Button(
            form_card,
            text="Delete Selected Student",
            command=self.delete_selected_student,
            style="Secondary.TButton",
        ).grid(row=7, column=0, pady=(10, 0))

        ttk.Button(
            form_card,
            text="Retake Photos for Selected Student",
            command=self.retake_photos_for_selected_student,
            style="Secondary.TButton",
        ).grid(row=8, column=0, pady=(10, 0))

        columns = ("student_id", "full_name", "image_count")
        self.student_table = ttk.Treeview(
            table_card,
            columns=columns,
            show="headings",
            height=16,
            style="Modern.Treeview",
        )
        self.student_table.heading("student_id", text="Student ID")
        self.student_table.heading("full_name", text="Full Name")
        self.student_table.heading("image_count", text="Images")
        self.student_table.column("student_id", width=100)
        self.student_table.column("full_name", width=180)
        self.student_table.column("image_count", width=100)
        self.student_table.grid(row=0, column=0, sticky="nsew")
        self.student_table.bind("<<TreeviewSelect>>", self.on_student_select)

        # --- Image Gallery for Selected Student ---
        gallery_card = ttk.LabelFrame(table_card, text="Reference Images", style="Card.TLabelframe", padding=10)
        gallery_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        gallery_card.columnconfigure((0, 1, 2), weight=1)

        self.gallery_image_labels = []
        for i in range(3):
            image_shell = tk.Frame(
                gallery_card,
                bg=self.colors["panel_alt"],
                bd=0,
                highlightbackground=self.colors["border"],
                highlightthickness=1,
                width=160,
                height=120,
            )
            image_shell.grid(row=0, column=i, sticky="nsew", padx=5)
            image_shell.grid_propagate(False)
            image_shell.grid_columnconfigure(0, weight=1)
            image_shell.grid_rowconfigure(0, weight=1)

            label = tk.Label(image_shell, bg=self.colors["panel_alt"], text="...", fg=self.colors["muted"])
            label.grid(sticky="nsew")
            self.gallery_image_labels.append(label)

        self._clear_student_gallery() # Initialize with a clear state

        # --- Unenrollment Success Overlay ---
        self.unenroll_overlay = tk.Frame(table_card, bg="#ffffff")
        self.unenroll_overlay.grid_columnconfigure(0, weight=1)
        
        tk.Label(
            self.unenroll_overlay,
            text="🗑️",
            bg="#ffffff",
            fg=self.colors["danger"],
            font=("Georgia", 64),
        ).grid(row=0, column=0, pady=(80, 10))
        
        tk.Label(
            self.unenroll_overlay,
            text="Student Unenrolled",
            bg="#ffffff",
            fg=self.colors["danger"],
            font=("Georgia", 36, "bold"),
        ).grid(row=1, column=0, pady=(0, 10))
        
        self.unenroll_subtitle_label = tk.Label(self.unenroll_overlay, text="", bg="#ffffff", fg=self.colors["muted"], font=("Georgia", 12))
        self.unenroll_subtitle_label.grid(row=2, column=0)

    def _build_attendance_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=5)
        parent.columnconfigure(1, weight=4)
        parent.rowconfigure(2, weight=1)
        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(0, weight=1)

        ttk.Label(
            header_frame,
            text="👋 Take Attendance 👋 ",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="")

        video_panel = ttk.Frame(parent, style="Card.TFrame")
        video_panel.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        video_panel.columnconfigure(0, weight=1)
        video_panel.rowconfigure(1, weight=1)

        controls = ttk.Frame(video_panel, style="Card.TFrame")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        ttk.Button(
            controls,
            text="📸 Start LiveStream",
            command=self.start_attendance,
            style="Modern.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(
            controls,
            text="📷 End LiveStream",
            command=self.stop_attendance,
            style="Secondary.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Button(
            controls,
            text="Reload enrolled faces",
            command=self.reload_enrolled_faces,
            style="Secondary.TButton",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.video_label = tk.Label(
            video_panel,
            text="Webcam preview will appear here.",
            anchor="center",
            relief="solid",
            background="#eaf1f7",
            foreground=self.colors["muted"],
            font=("Georgia", 11),
        )
        self.video_label.grid(row=1, column=0, sticky="nsew")

        right_panel = ttk.LabelFrame(
            parent,
            text="Verification",
            style="Card.TLabelframe",
            labelanchor="n",
            padding=12,
        )
        right_panel.grid(row=2, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(0, weight=1)
        badge_shell = tk.Frame(
            right_panel,
            bg=self.colors["accent"],
            bd=0,
            highlightbackground=self.colors["accent"],
            highlightthickness=1,
            padx=14,
            pady=14,
        )
        badge_shell.grid(row=0, column=0, sticky="nsew")

        self.system_status_label = ttk.Label(
            right_panel,
            text=f"System status: {self.session_config_summary}",
            wraplength=450,
            justify="center",
            style="Subhero.TLabel",
        )
        # Place it at the bottom of the right panel, centered
        self.system_status_label.grid(row=1, column=0, sticky="s", pady=(10, 0))

        badge_shell.grid_columnconfigure(0, weight=1)

        self.badge_card = tk.Frame(
            badge_shell,
            bg="#ffffff",
            bd=0,
            highlightbackground="#ffffff",
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        self.badge_card.grid(row=0, column=0, sticky="nsew")
        self.badge_card.grid_columnconfigure(0, weight=1)

        self.badge_image_shell = tk.Frame(
            self.badge_card,
            bg=self.colors["panel_alt"],
            bd=0,
            highlightbackground=self.colors["accent"],
            highlightthickness=4,
            width=248,
            height=180,
        )
        self.badge_image_shell.grid(row=0, column=0, sticky="n", pady=(4, 0))
        self.badge_image_shell.grid_propagate(False)
        self.badge_image_shell.grid_columnconfigure(0, weight=1)
        self.badge_image_shell.grid_rowconfigure(0, weight=1)

        self.badge_image_label = tk.Label(
            self.badge_image_shell,
            text="No verification pending",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            anchor="center",
            justify="center",
        )
        self.badge_image_label.grid(row=0, column=0, sticky="nsew")

        self.badge_name_label = tk.Label(
            self.badge_card,
            text="No student",
            bg="#ffffff",
            fg=self.colors["ink"],
            anchor="center",
            font=("Georgia", 22, "bold"),
        )
        self.badge_name_label.grid(row=1, column=0, sticky="ew", pady=(10, 10))

        self.badge_id_label = tk.Label(
            self.badge_card,
            text="ID: --",
            bg=self.colors["accent_soft"],
            fg=self.colors["accent"],
            anchor="center",
            font=("Georgia", 10, "bold"),
            padx=18,
            pady=6,
        )
        self.badge_id_label.grid(row=2, column=0, sticky="n", pady=(0, 8))

        self.badge_major_label = tk.Label(
            self.badge_card,
            text="Status: Waiting",
            bg=self.colors["accent_soft"],
            fg=self.colors["accent"],
            anchor="center",
            font=("Georgia", 10, "bold"),
            padx=22,
            pady=6,
        )
        self.badge_major_label.grid(row=3, column=0, sticky="n")

        self.badge_status_label = tk.Label(
            self.badge_card,
            text="",
            bg="#ffffff",
            fg=self.colors["muted"],
            anchor="center",
            justify="center",
            wraplength=300,
        )
        self.badge_status_label.grid(row=4, column=0, sticky="ew", pady=(10, 12))

        badge_actions = ttk.Frame(self.badge_card)
        badge_actions.grid(row=5, column=0, sticky="ew")
        badge_actions.columnconfigure(0, weight=1) # Make the first column expand
        badge_actions.columnconfigure(1, weight=1) # Make the second column expand
        ttk.Button(
            badge_actions,
            text=ICON_CHECKMARK,
            command=self.confirm_badge,
            style="Confirm.TButton",
        ).grid(row=0, column=0, sticky="nsew", padx=(0, 3)) # Use grid, expand, and add padding to the right
        ttk.Button(
            badge_actions,
            text=ICON_REJECT,
            command=self.reject_badge,
            style="Reject.TButton",
        ).grid(row=0, column=1, sticky="nsew", padx=(3, 0)) # Use grid, expand, and add padding to the left

        self.badge_present_overlay = tk.Frame(
            self.badge_card,
            bg="#ffffff",
            padx=10,
            pady=10,
        )
        self.badge_present_overlay.grid_columnconfigure(0, weight=1)
        self.badge_present_icon = tk.Label(
            self.badge_present_overlay,
            text="🙋",
            bg="#ffffff",
            fg=self.colors["accent"],
            font=("Georgia", 54),
        )
        self.badge_present_icon.grid(row=0, column=0, pady=(36, 10))
        self.badge_present_label = tk.Label(
            self.badge_present_overlay,
            text="Present!",
            bg="#ffffff",
            fg=self.colors["accent"],
            font=("Georgia", 30, "bold"),
        )
        self.badge_present_label.grid(row=1, column=0, pady=(0, 6))
        self.badge_present_subtitle = tk.Label(
            self.badge_present_overlay,
            text="Attendance marked successfully.",
            bg="#ffffff",
            fg=self.colors["muted"],
            font=("Georgia", 11),
        )
        self.badge_present_subtitle.grid(row=2, column=0)

    def _build_daily_report_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(1, weight=1) # Give weight to the spacer column

        ttk.Label(
            header_frame,
            text="📋 Daily Attendance Log 📋 ",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(12, 0))

        ttk.Button(
            header_frame,
            text="Export for Canvas (CSV)",
            command=self.export_daily_canvas,
            style="Modern.TButton",
        ).grid(row=0, column=2, sticky="e", padx=(0, 12))

        columns = ("index", "name", "check_in_time", "present", "absent")
        self.daily_report_table = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            style="Modern.Treeview",
        )
        self.daily_report_table.heading("index", text="#")
        self.daily_report_table.heading("name", text="Name")
        self.daily_report_table.heading("check_in_time", text="Check-In Time")
        self.daily_report_table.heading("present", text="Present")
        self.daily_report_table.heading("absent", text="Absent")

        self.daily_report_table.column("index", width=50, anchor="center")
        self.daily_report_table.column("name", width=300)
        self.daily_report_table.column("check_in_time", width=150, anchor="center")
        self.daily_report_table.column("present", width=100, anchor="center")
        self.daily_report_table.column("absent", width=100, anchor="center")

        self.daily_report_table.grid(row=1, column=0, sticky="nsew")

    def export_daily_canvas(self) -> None:
        try:
            path = self.attendance_logger.export_daily_summary(datetime.now(), self.registry.list_students())
            messagebox.showinfo("Export Success", f"Daily Canvas report saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Could not export daily Canvas report: {e}")

    def _populate_daily_report(self) -> None:
        if not hasattr(self, "daily_report_table"):
            return
        table = self.daily_report_table
        assert table is not None, "daily_report_table should not be None"
        for item in table.get_children():
            table.delete(item)

        daily_records = self.attendance_logger.records_for_day(datetime.now())
        present_records_map = {record.student_id: record for record in daily_records}
        day_has_started = bool(daily_records)
        for index, student in enumerate(self.registry.list_students(), start=1):
            record = present_records_map.get(student.student_id)
            check_in_time = record.recorded_at.strftime("%I:%M %p") if record else ""
            present_mark = "✓" if record else ""
            absent_mark = "" if record or not day_has_started else "X"
            table.insert(
                "",
                "end",
                values=(index, student.full_name, check_in_time, present_mark, absent_mark),
            )

    def _build_weekly_report_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(1, weight=1) # Give weight to the spacer column

        ttk.Label(
            header_frame,
            text="📅 Weekly Attendance Log 📅 ",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(12, 0))

        ttk.Button(
            header_frame,
            text="Export for Canvas (CSV)",
            command=self.export_weekly_canvas,
            style="Modern.TButton",
        ).grid(row=0, column=2, sticky="e", padx=(0, 12))

        columns = ("index", "name", "mon", "tue", "wed", "thu", "fri")
        self.weekly_report_table = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            style="Modern.Treeview",
        )
        headings = {
            "index": "#",
            "name": "Student Name",
            "mon": "Mon",
            "tue": "Tue",
            "wed": "Wed",
            "thu": "Thu",
            "fri": "Fri",
        }
        for column, heading in headings.items():
            self.weekly_report_table.heading(column, text=heading)
            self.weekly_report_table.column(
                column,
                width=260 if column == "name" else 130,
                anchor="w" if column == "name" else "center",
            )
        self.weekly_report_table.column("index", width=50, anchor="center")
        self.weekly_report_table.tag_configure("summary", font=("Georgia", 10, "bold"))
        self.weekly_report_table.grid(row=1, column=0, sticky="nsew")

    def export_weekly_canvas(self) -> None:
        try:
            path = self.attendance_logger.export_weekly_summary(datetime.now(), self.registry.list_students())
            messagebox.showinfo("Export Success", f"Weekly Canvas report saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Could not export weekly Canvas report: {e}")

    def _populate_weekly_report(self) -> None:
        if not hasattr(self, "weekly_report_table"):
            return
        table = self.weekly_report_table
        assert table is not None, "weekly_report_table should not be None"
        for item in table.get_children():
            table.delete(item)

        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        week_days = [start_of_week + timedelta(days=offset) for offset in range(5)]
        day_columns = ("mon", "tue", "wed", "thu", "fri")
        for column, day in zip(day_columns, week_days):
            table.heading(column, text=day.strftime("%a %b %d"))

        presence = self.attendance_logger._get_presence_for_range(
            datetime.combine(week_days[0], datetime.min.time()),
            datetime.combine(week_days[-1], datetime.max.time()),
        )

        daily_present_counts = [0] * len(week_days)
        students = self.registry.list_students()
        for index, student in enumerate(students, start=1):
            marks: list[str] = []
            for day_index, day in enumerate(week_days):
                day_is_in_past_or_present = day <= today
                date_key = day.strftime("%Y-%m-%d")
                day_has_started = bool(presence.get(date_key)) or day < today
                is_present = student.student_id in presence.get(date_key, set())
                mark = "✓" if is_present else "X" if day_is_in_past_or_present and day_has_started else ""
                marks.append(mark)
                if is_present:
                    daily_present_counts[day_index] += 1
            table.insert(
                "",
                "end",
                values=(
                    index,
                    student.full_name,
                    *marks,
                ),
            )

        total_students = len(students)
        daily_absent_counts = [
            str(total_students - daily_present_counts[i])
            if (day <= today and (bool(presence.get(day.strftime("%Y-%m-%d"))) or day < today))
            else ""
            for i, day in enumerate(week_days)
        ]
        daily_present_counts_display = [str(count) if count > 0 else "" for count in daily_present_counts]
        table.insert("", "end", values=("", "", "", "", "", "", ""))
        table.insert(
            "",
            "end",
            values=("", "Total Present", *daily_present_counts_display),
            tags=("summary",),
        )
        table.insert(
            "",
            "end",
            values=("", "Total Absent", *daily_absent_counts),
            tags=("summary",),
        )

    def _build_monthly_report_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(1, weight=1) # Give weight to the spacer column

        ttk.Label(
            header_frame,
            text="🗓️ Monthly Attendance Log 🗓️",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(12, 0))

        ttk.Button(
            header_frame,
            text="Export for Canvas (CSV)",
            command=self.export_monthly_canvas,
            style="Modern.TButton",
        ).grid(row=0, column=2, sticky="e", padx=(0, 12))

        grid_shell = ttk.Frame(parent, style="Card.TFrame")
        grid_shell.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        grid_shell.columnconfigure(0, weight=1)
        grid_shell.rowconfigure(0, weight=1)

        self.monthly_report_canvas = tk.Canvas(
            grid_shell,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        self.monthly_report_canvas.grid(row=0, column=0, sticky="nsew")

        x_scroll = ttk.Scrollbar(
            grid_shell,
            orient="horizontal",
            command=self.monthly_report_canvas.xview,
        )
        x_scroll.grid(row=1, column=0, sticky="ew")
        y_scroll = ttk.Scrollbar(
            grid_shell,
            orient="vertical",
            command=self.monthly_report_canvas.yview,
        )
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.monthly_report_canvas.configure(
            xscrollcommand=x_scroll.set,
            yscrollcommand=y_scroll.set,
        )

    def export_monthly_canvas(self) -> None:
        try: # type: ignore[attr-defined]
            path = self.attendance_logger.export_monthly_summary(datetime.now(), self.registry.list_students())
            messagebox.showinfo("Export Success", f"Monthly Canvas report saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Could not export monthly Canvas report: {e}")

    def _populate_monthly_report(self) -> None:
        if not hasattr(self, "monthly_report_canvas"):
            return
        canvas = self.monthly_report_canvas
        assert canvas is not None, "monthly_report_canvas should not be None"
        canvas.delete("all")
        
        today = datetime.now()
        presence = self.attendance_logger._get_monthly_presence(today.year, today.month)
        _, days_in_month = calendar.monthrange(today.year, today.month)
        month_days = [datetime(today.year, today.month, day) for day in range(1, days_in_month + 1)]
        students = self.registry.list_students()
        present_counts = [0] * len(month_days)
        absent_counts = [0] * len(month_days)
        
        row_height = 24
        index_width = 54
        name_width = 180
        day_width = 90
        header_height = row_height
        total_width = index_width + name_width + (day_width * len(month_days))
        total_height = row_height * (1 + len(students) + 1 + 2) # Header, students, blank, 2 summary rows
        
        weekend_fill = "#ffc7ce"
        weekend_ink = "#9c0006"
        grid_ink = "#d9d9d9"
        text_ink = "#000000"
        
        # --- Optimization: Draw grid lines and backgrounds first ---
        # Draw weekend backgrounds
        for day_index, day in enumerate(month_days):
            is_weekend = day.weekday() >= 5
            if is_weekend:
                x = index_width + name_width + (day_index * day_width)
                canvas.create_rectangle(x, 0, x + day_width, total_height, fill=weekend_fill, outline="")
        
        # Draw horizontal grid lines
        for i in range(2 + len(students) + 3): # Header, students, blank, 2 summary
            y = i * row_height
            canvas.create_line(0, y, total_width, y, fill=grid_ink)
        
        # Draw vertical grid lines
        canvas.create_line(0, 0, 0, total_height, fill=grid_ink)
        canvas.create_line(index_width, 0, index_width, total_height, fill=grid_ink)
        canvas.create_line(index_width + name_width, 0, index_width + name_width, total_height, fill=grid_ink)
        for i in range(len(month_days) + 1):
            x = index_width + name_width + (i * day_width)
            canvas.create_line(x, 0, x, total_height, fill=grid_ink)

        # --- Helper for placing text ---
        def place_text(
            x: float,
            y: float,
            text: str,
            bold: bool = False,
            anchor: Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"] = "center",
            ink: str = text_ink,
        ) -> None:
            font = ("Georgia", 10, "bold") if bold else ("Georgia", 10)
            canvas.create_text(x, y, text=text, fill=ink, anchor=anchor, font=font)

        # --- Populate Header ---
        place_text(index_width / 2, header_height / 2, "#", bold=True)
        place_text(index_width + 6, header_height / 2, "Student Name", bold=True, anchor="w")
        for day_index, day in enumerate(month_days):
            x = index_width + name_width + (day_index * day_width) + (day_width / 2)
            is_weekend = day.weekday() >= 5
            place_text(x, header_height / 2, day.strftime("%b %d"), bold=not is_weekend, ink=weekend_ink if is_weekend else text_ink)

        # --- Populate Student Data ---
        for row_index, student in enumerate(students, start=1):
            y = row_index * row_height
            text_y = y + row_height / 2
            
            place_text(index_width / 2, text_y, str(row_index))
            place_text(index_width + 6, text_y, student.full_name, anchor="w")
            
            for day_index, day in enumerate(month_days):
                x = index_width + name_width + (day_index * day_width)
                is_weekend = day.weekday() >= 5
                day_is_in_past_or_present = day.date() <= today.date()
                date_key = day.strftime("%Y-%m-%d")
                day_has_started = bool(presence.get(date_key)) or day.date() < today.date()
                is_present = student.student_id in presence.get(date_key, set())
                
                mark = ""
                if not is_weekend and day_is_in_past_or_present:
                    mark = "✓" if is_present else "X" if day_has_started else ""
                    if is_present:
                        present_counts[day_index] += 1
                    else:
                        absent_counts[day_index] += 1
                
                if mark:
                    place_text(x + day_width / 2, text_y, mark, bold=True, ink=weekend_ink if is_weekend else text_ink)

        # --- Populate Summary Rows ---
        present_row_y = (len(students) + 2) * row_height
        absent_row_y = present_row_y + row_height
        present_text_y = present_row_y + row_height / 2
        absent_text_y = absent_row_y + row_height / 2
        
        place_text(index_width + 6, present_text_y, "Total Present", anchor="w", bold=True)
        place_text(index_width + 6, absent_text_y, "Total Absent", anchor="w", bold=True)
        
        for day_index, day in enumerate(month_days):
            x = index_width + name_width + (day_index * day_width)
            is_weekend = day.weekday() >= 5
            day_is_in_past_or_present = day.date() <= today.date()
            date_key = day.strftime("%Y-%m-%d")
            day_has_started = bool(presence.get(date_key)) or day.date() < today.date()
            
            if not is_weekend and day_is_in_past_or_present and day_has_started:
                present_value = str(present_counts[day_index])
                absent_value = str(absent_counts[day_index])
                text_x = x + day_width / 2
                ink = weekend_ink if is_weekend else text_ink
                
                place_text(text_x, present_text_y, present_value, bold=True, ink=ink)
                place_text(text_x, absent_text_y, absent_value, bold=True, ink=ink)

        # --- Final Configuration ---
        canvas.configure(scrollregion=(0, 0, total_width, total_height))


    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        header_frame = ttk.Frame(parent, style="Hero.TFrame", padding=(0, 8))
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        header_frame.columnconfigure(0, weight=1)

        ttk.Label(
            header_frame,
            text="⚙️ Settings ⚙️",
            style="Hero.TLabel",
        ).grid(row=0, column=0, sticky="")

        tuning_card = ttk.LabelFrame(
            parent,
            text="Recognition Controls",
            style="Card.TLabelframe",
            padding=18,
        )
        tuning_card.grid(row=1, column=0, sticky="ew", padx=20)

        threshold_frame = ttk.Frame(tuning_card, style="Card.TFrame")
        threshold_frame.pack(anchor="w", pady=(0, 18), fill="x")

        ttk.Label(threshold_frame, text="Recognition threshold", style="Section.TLabel").pack(anchor="w")
        self.threshold_var = tk.DoubleVar(value=self.app_config.lbph_confidence_threshold)
        entry_row = ttk.Frame(threshold_frame, style="Card.TFrame")
        entry_row.pack(anchor="w", fill="x", pady=(8, 0))

        self.threshold_entry_var = tk.StringVar(value=f"{self.threshold_var.get():.1f}")
        self.threshold_entry = ttk.Entry(
            entry_row,
            textvariable=self.threshold_entry_var,
            width=10,
            style="Modern.TEntry",
        )
        self.threshold_entry.pack(side="left", padx=(0, 12))
        self.threshold_entry.bind("<Return>", self.on_threshold_entry_commit)
        self.threshold_entry.bind("<FocusOut>", self.on_threshold_entry_commit)

        self.threshold_value_label = ttk.Label(
            entry_row,
            text=f"Current: {self.threshold_var.get():.1f}",
            style="Subhero.TLabel",
        )
        self.threshold_value_label.pack(side="left")

        threshold_scale = ttk.Scale(
            threshold_frame,
            from_=20.0,
            to=180.0,
            orient="horizontal",
            variable=self.threshold_var,
            command=self.on_threshold_change,
        )
        threshold_scale.pack(anchor="w", fill="x", pady=(10, 0))
        threshold_scale.bind("<ButtonRelease-1>", self.on_threshold_entry_commit)

        # --- Template Distance Threshold ---
        template_distance_frame = ttk.Frame(tuning_card, style="Card.TFrame")
        template_distance_frame.pack(anchor="w", pady=(18, 0), fill="x")

        ttk.Label(template_distance_frame, text="Template Distance Threshold (Lower is stricter)", style="Section.TLabel").pack(anchor="w")
        self.template_distance_var = tk.DoubleVar(value=self.app_config.template_distance_threshold)
        template_entry_row = ttk.Frame(template_distance_frame, style="Card.TFrame")
        template_entry_row.pack(anchor="w", fill="x", pady=(8, 0))

        self.template_distance_entry_var = tk.StringVar(value=f"{self.template_distance_var.get():.1f}")
        self.template_distance_entry = ttk.Entry(
            template_entry_row,
            textvariable=self.template_distance_entry_var,
            width=10,
            style="Modern.TEntry",
        )
        self.template_distance_entry.pack(side="left", padx=(0, 12))
        self.template_distance_entry.bind("<Return>", self.on_template_distance_entry_commit)
        self.template_distance_entry.bind("<FocusOut>", self.on_template_distance_entry_commit)

        self.template_distance_value_label = ttk.Label(
            template_entry_row,
            text=f"Current: {self.template_distance_var.get():.1f}",
            style="Subhero.TLabel",
        )
        self.template_distance_value_label.pack(side="left")

        template_distance_scale = ttk.Scale(
            template_distance_frame,
            from_=MIN_TEMPLATE_DISTANCE,
            to=MAX_TEMPLATE_DISTANCE,
            orient="horizontal",
            variable=self.template_distance_var,
            command=self.on_template_distance_change,
        )
        template_distance_scale.pack(anchor="w", fill="x", pady=(10, 0))
        template_distance_scale.bind("<ButtonRelease-1>", self.on_template_distance_entry_commit)

        confirmation_frame = ttk.Frame(tuning_card, style="Card.TFrame")
        confirmation_frame.pack(anchor="w", fill="x")

        ttk.Label(confirmation_frame, text="Confirmation frames", style="Section.TLabel").pack(anchor="w")
        self.confirmation_frames_var = tk.StringVar(
            value=str(self.app_config.confirmation_frames)
        )
        confirmation_row = ttk.Frame(confirmation_frame, style="Card.TFrame")
        confirmation_row.pack(anchor="w", fill="x", pady=(8, 0))
        self.confirmation_frames_entry = ttk.Entry(
            confirmation_row,
            textvariable=self.confirmation_frames_var,
            width=10,
            style="Modern.TEntry",
        )
        self.confirmation_frames_entry.pack(side="left", padx=(0, 12))
        self.confirmation_frames_entry.bind("<Return>", self.on_confirmation_frames_commit)
        self.confirmation_frames_entry.bind("<FocusOut>", self.on_confirmation_frames_commit)
        self.confirmation_frames_label = ttk.Label(
            confirmation_row,
            text=f"Current: {self.app_config.confirmation_frames}",
            style="Subhero.TLabel",
        )
        self.confirmation_frames_label.pack(side="left")

        data_card = ttk.LabelFrame(
            parent,
            text="Data Management",
            style="Card.TLabelframe",
            padding=18,
        )
        data_card.grid(row=2, column=0, sticky="ew", padx=20, pady=(20, 0))
        data_card.columnconfigure(0, weight=1)

        ttk.Button(
            data_card,
            text="Clear Today's Attendance",
            command=self.reset_today_attendance,
            style="Danger.TButton",
        ).pack()

    def open_live_capture(self, for_retake: bool = False, student_id: str | None = None, full_name: str | None = None) -> None:
        if self.active_session is not None:
            messagebox.showerror(
                "Live Pictures",
                "LiveStream is using the webcam, so registration cannot open a second "
                "camera session.\n\n"
                "Use End LiveStream, capture registration photos, then Start LiveStream "
                "again — recognition reloads from disk when the stream starts.\n\n"
                "If LiveStream is already on and you only changed photos on disk, use "
                "\"Reload enrolled faces\" instead.",
            )
            return
        if self.capture_window is not None:
            self.capture_window.lift()
            return

        self._clear_captured_temp_images()
        self.selected_image_paths = []
        self.captured_image_paths = []
        self.use_pictures_button: ttk.Button | None = None # Initialize here or in __init__
        self.is_capturing_for_retake = for_retake
        self.is_auto_capturing = False
        self.current_retake_student_id = student_id
        self.selected_image_label.config(text="No live pictures captured")

        capture_index = self.app_config.camera_index
        api_preference = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        self.capture_device = cv2.VideoCapture(capture_index, api_preference)
        if self.capture_device is None or not self.capture_device.isOpened():
            self.capture_device = None
            messagebox.showerror("Live Pictures", "Unable to open webcam.")
            return
        
        try:
            self.capture_detector = FaceDetector(phone_screen_mode=self.app_config.phone_screen_mode)
        except Exception as e:
            logger.error(f"Failed to initialize detector for capture window: {e}")
            self.capture_detector = None

        self.capture_device.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.capture_device.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture_device.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        window = tk.Toplevel(self.root)
        window.geometry("760x620")
        window.minsize(720, 580)
        window.configure(bg=self.colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close_live_capture)
        self.capture_window = window
        
        if self.is_capturing_for_retake and self.current_retake_student_id:
            student_name = full_name or self.registry.get_student_name(self.current_retake_student_id) or "student"
            window.title(f"Retake Photos for {student_name}")
        else:
            window.title("Take Live Pictures")

        shell = ttk.Frame(window, style="App.TFrame", padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        title_text = f"Capture new photos for {full_name}" if for_retake else "Capture 2 or 3 reference pictures"
        ttk.Label(shell, text=title_text, style="Hero.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.capture_preview_label = tk.Label(
            shell,
            text="Starting webcam preview...",
            anchor="center",
            relief="solid",
            background="#eaf1f7",
            foreground=self.colors["muted"],
            font=("Georgia", 11),
        )
        self.capture_preview_label.grid(row=1, column=0, sticky="nsew")

        controls = ttk.Frame(shell, style="App.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        controls.columnconfigure(3, weight=1)

        self.start_scan_button = ttk.Button(
            controls,
            text="Start Face Scan",
            command=self.start_auto_capture,
            style="Modern.TButton",
        ).grid(row=0, column=0, padx=(0, 8))

        self.use_pictures_button = ttk.Button(controls, text="Use Pictures", command=self.use_live_pictures, style="Modern.TButton")
        self.use_pictures_button.grid(row=0, column=1, padx=(0, 8))
        self.use_pictures_button.state(["disabled"])

        ttk.Button(
            controls,
            text="Cancel",
            command=self.close_live_capture,
            style="Secondary.TButton",
        ).grid(row=0, column=4)

        self.capture_count_label = ttk.Label(
            shell,
            text="Captured: 0 / 3",
            style="Subhero.TLabel",
        )
        self.capture_count_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.capture_status_label = ttk.Label(shell, text="Click 'Start Face Scan' to begin.", style="Subhero.TLabel")
        self.capture_status_label.grid(row=4, column=0, sticky="w", pady=(4, 0))

        # --- Completion Overlay ---
        self.capture_complete_overlay = tk.Frame(shell, bg="#ffffff")
        self.capture_complete_overlay.grid_columnconfigure(0, weight=1)
        
        tk.Label(
            self.capture_complete_overlay,
            text="✅",
            bg="#ffffff",
            fg=self.colors["success"],
            font=("Georgia", 64),
        ).grid(row=0, column=0, pady=(80, 10))
        
        tk.Label(
            self.capture_complete_overlay,
            text="Capture Complete",
            bg="#ffffff",
            fg=self.colors["success"],
            font=("Georgia", 36, "bold"),
        ).grid(row=1, column=0, pady=(0, 10))
        
        self.capture_complete_subtitle = tk.Label(self.capture_complete_overlay, text="Registering student...", bg="#ffffff", fg=self.colors["muted"], font=("Georgia", 12))
        self.capture_complete_subtitle.grid(row=2, column=0)

        self._schedule_live_capture_update()

    def _update_use_pictures_button_text(self) -> None:
        if self.use_pictures_button:
            text = "Update Pictures" if self.is_capturing_for_retake else "Use Pictures"
            self.use_pictures_button.config(text=text)

    def start_auto_capture(self) -> None:
        """Begin the automatic face scanning process."""
        if self.is_auto_capturing:
            return
        self.is_auto_capturing = True
        if self.start_scan_button: # type: ignore[attr-defined]
            self.start_scan_button.state(["disabled"])
        if self.use_pictures_button: # type: ignore[attr-defined]
            self.use_pictures_button.state(["disabled"])
        self._clear_captured_temp_images() # Start fresh
        self._update_live_capture_count()
        if self.capture_status_label:
            self.capture_status_label.config(text="Scanning... Please center your face in the preview.")

    def _schedule_live_capture_update(self) -> None:
        if self.capture_window is None or self.capture_device is None:
            return

        has_frame, frame = self.capture_device.read()
        if has_frame:
            if self.is_auto_capturing and self.capture_detector and len(self.captured_image_paths) < 3:
                boxes = self.capture_detector.detect(frame)
                if boxes:
                    box = boxes[0] # Get the best detection
                    cv2.rectangle(frame, (box.x, box.y), (box.x + box.width, box.y + box.height), (0, 255, 0), 2)
                    cv2.putText(frame, "Face Detected", (box.x, box.y - 10), _FACE_LABEL_FONT, 0.6, (0, 255, 0), 2)
                    self.auto_capture_hold_steady_frames += 1
                    
                    if self.auto_capture_hold_steady_frames > 10: # Require face to be stable for ~1/3 second
                        if self.capture_status_label:
                            self.capture_status_label.config(text=f"Hold still... Capturing image {len(self.captured_image_paths) + 1} of 3.")
                        
                        now = datetime.now() # type: ignore[attr-defined]
                        if self.last_auto_capture_time is None or (now - self.last_auto_capture_time).total_seconds() > 0.75: # type: ignore[attr-defined]
                            self.capture_live_picture(frame)
                            self.last_auto_capture_time = now # type: ignore[attr-defined]
                            if len(self.captured_image_paths) >= 3:
                                self.is_auto_capturing = False
                                self._show_capture_complete_and_finish()
                else:
                    self.auto_capture_hold_steady_frames = 0
                    if self.capture_status_label:
                        self.capture_status_label.config(text="Scanning... Please center your face in the preview.")

            self.capture_last_frame = frame.copy()
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb_frame)
            image.thumbnail((700, 430))
            self.capture_photo = ImageTk.PhotoImage(image=image)
            if self.capture_preview_label is not None:
                self.capture_preview_label.config(image=self.capture_photo, text="")

        self.capture_after_id = self.root.after(60, self._schedule_live_capture_update)

    def capture_live_picture(self, frame_to_save: np.ndarray | None = None) -> None:
        """Saves the provided frame as a reference image."""
        if frame_to_save is None:
            messagebox.showerror("Live Pictures", "No webcam frame is ready yet.")
            return
        if len(self.captured_image_paths) >= 3:
            return

        ensure_directories()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = TEMP_DIR / f"live_reference_{timestamp}.jpg"
        if not cv2.imwrite(str(output_path), frame_to_save):
            messagebox.showerror("Live Pictures", "Could not save the captured picture.")
            return

        self.captured_image_paths.append(output_path)
        self._update_live_capture_count()

    def remove_last_live_picture(self) -> None:
        if not self.captured_image_paths:
            return
        last_path = self.captured_image_paths.pop()
        if last_path.exists():
            last_path.unlink()
        self._update_live_capture_count()

    def use_live_pictures(self, show_success_popup: bool = True) -> None: # type: ignore[attr-defined]
        try:
            if self.is_capturing_for_retake and self.current_retake_student_id:
                # Retake photos for an existing student
                self.registry.replace_student_images(
                    student_id=self.current_retake_student_id,
                    image_sources=self.captured_image_paths,
                )
                if show_success_popup: # type: ignore[attr-defined]
                    messagebox.showinfo(
                        "Success",
                        f"Images updated for {self.current_retake_student_id}.\n\n"
                        "Start LiveStream when you are ready — enrollment is reloaded from disk "
                        "each time the stream starts. If LiveStream is already on, click "
                        "\"Reload enrolled faces\".",
                    )
                self.rebuild_recognizer_pipeline()
            else:
                # Register new student
                self.registry.register_student(
                    student_id=self.student_id_var.get(),
                    full_name=self.full_name_var.get(),
                    image_sources=self.captured_image_paths,
                )
                if show_success_popup: # type: ignore[attr-defined]
                    messagebox.showinfo(
                        "Success",
                        "Student registered successfully.\n\n"
                        "Start LiveStream when you are ready — enrollment is reloaded from disk "
                        "each time the stream starts. If LiveStream is already on, click "
                        "\"Reload enrolled faces\".",
                    )
                self.rebuild_recognizer_pipeline()

            self.student_id_var.set("")
            self.full_name_var.set("")
            self.selected_image_paths = []
            self.selected_image_label.config(text="No live pictures captured")
            self.refresh_student_table()
            self.close_live_capture(clear_captures=True)
        except (ValueError, FileNotFoundError) as exc:
            messagebox.showerror("Operation Error", str(exc))
        except Exception as exc:
            logger.error(f"Unexpected error during image processing: {exc}", exc_info=True)
            messagebox.showerror("Operation Error", "An unexpected error occurred.")

    def _show_capture_complete_and_finish(self) -> None: # type: ignore[attr-defined]
        """Displays a completion overlay and automatically finalizes the registration."""
        if self.capture_complete_overlay:
            subtitle = "Updating images..." if self.is_capturing_for_retake else "Registering student..."
            if self.capture_complete_subtitle:
                self.capture_complete_subtitle.config(text=subtitle)
            self.capture_complete_overlay.place(relx=0, rely=0, relwidth=1, relheight=1) # type: ignore[attr-defined]

        # The user sees the completion screen for 2.5 seconds before the final action.
        # We pass `show_success_popup=False` to `use_live_pictures` because the overlay
        # already serves as the success indicator.
        self.root.after(2500, lambda: self.use_live_pictures(show_success_popup=False)) # type: ignore[attr-defined]


    def _update_live_capture_count(self) -> None:
        if self.capture_count_label is not None:
            self.capture_count_label.config(
                text=f"Captured: {len(self.captured_image_paths)} / 3"
            )

    def close_live_capture(self, clear_captures: bool = True) -> None:
        if self.capture_after_id is not None:
            self.root.after_cancel(self.capture_after_id)
            self.capture_after_id = None
        if self.capture_device is not None:
            self.capture_device.release()
            self.capture_device = None
        if self.capture_window is not None:
            self.capture_window.destroy()
            self.capture_window = None
        # Clear widget references before updating button (widgets are destroyed with window)
        self.capture_last_frame = None
        self.capture_photo = None
        self.capture_preview_label = None
        self.capture_count_label = None
        self.use_pictures_button = None
        self.capture_status_label = None
        self.capture_detector = None
        self.capture_complete_overlay = None
        self.capture_complete_subtitle = None

        self.is_auto_capturing = False
        self.last_auto_capture_time = None
        self.auto_capture_hold_steady_frames = 0
        self._update_use_pictures_button_text() # Reset button text
        self.is_capturing_for_retake = False
        self.current_retake_student_id = None
        if clear_captures:
            self._clear_captured_temp_images()

    def _clear_captured_temp_images(self) -> None:
        for image_path in self.captured_image_paths:
            try:
                if image_path.exists():
                    image_path.unlink()
            except Exception:
                pass
        self.captured_image_paths = []

    def register_student(self) -> None:
        """Validates fields and initiates the live photo capture for a new student."""
        student_id = self.student_id_var.get().strip()
        full_name = self.full_name_var.get().strip()
        if not student_id or not full_name:
            messagebox.showerror("Registration Error", "Student ID and Full Name are required to register a new student.")
            return
        
        # This now only opens the capture window if fields are valid.
        # The use_live_pictures method will handle the actual registration.
        self.open_live_capture(for_retake=False, student_id=student_id, full_name=full_name)

    def on_student_select(self, _event=None) -> None:
        """Handle student selection in the Treeview to display their images."""
        selection = self.student_table.selection()
        if not selection:
            self._clear_student_gallery()
            return

        item_id = selection[0]
        values = self.student_table.item(item_id, "values")
        if not values:
            self._clear_student_gallery()
            return

        student_id = str(values[0])
        student = self.registry.get_student_by_id(student_id)
        if not student:
            self._clear_student_gallery()
            return

        self.gallery_photos = []
        for i, label in enumerate(self.gallery_image_labels):
            if i < len(student.image_paths):
                try:
                    image_path = Path(student.image_paths[i])
                    image = Image.open(image_path)
                    image.thumbnail((158, 118))
                    photo = ImageTk.PhotoImage(image)
                    self.gallery_photos.append(photo)
                    label.config(image=photo, text="")
                except Exception as e:
                    logger.warning(f"Could not load gallery image {student.image_paths[i]}: {e}")
                    label.config(image="", text="Error")
            else:
                label.config(image="", text="") # Clear unused labels

    def _clear_student_gallery(self) -> None:
        """Clear the student image gallery and reset to placeholder text."""
        for label in self.gallery_image_labels:
            label.config(image="", text="Select a student")

    def refresh_student_table(self) -> None:
        for item in self.student_table.get_children():
            self.student_table.delete(item)

        for student in self.registry.list_students():
            self.student_table.insert(
                "",
                "end",
                values=(student.student_id, student.full_name, len(student.image_paths)),
            )
        self._clear_student_gallery()

    def delete_selected_student(self) -> None:
        selection = self.student_table.selection()
        if not selection:
            messagebox.showerror("Delete Student", "Please select a student first.")
            return

        item_id = selection[0]
        values = self.student_table.item(item_id, "values")
        if not values:
            messagebox.showerror("Delete Student", "Unable to read the selected student.")
            return

        student_id = str(values[0])
        full_name = str(values[1])
        confirmed = messagebox.askyesno(
            "Delete Student",
            f"Delete {full_name} ({student_id}) from the registry?",
        )
        if not confirmed:
            return

        deleted = self.registry.delete_student(student_id)
        if not deleted:
            messagebox.showerror("Delete Student", "Student could not be deleted.")
            return

        self.refresh_student_table()
        self.refresh_attendance_log()
        self._flash_unenroll_success(full_name)

    def retake_photos_for_selected_student(self) -> None:
        selection = self.student_table.selection()
        if not selection:
            messagebox.showerror("Retake Photos", "Please select a student first.")
            return

        item_id = selection[0]
        values = self.student_table.item(item_id, "values")
        if not values:
            messagebox.showerror("Retake Photos", "Unable to read the selected student.")
            return

        student_id = str(values[0])
        full_name = str(values[1])

        if self.active_session is not None:
            messagebox.showerror(
                "Retake Photos",
                "Please stop attendance before retaking photos for a student.",
            )
            return

        self.open_live_capture(for_retake=True, student_id=student_id, full_name=full_name)

    def start_attendance(self) -> None:
        try:
            if self.capture_window is not None:
                messagebox.showerror(
                    "Attendance Error",
                    "Please close live picture capture before starting attendance.",
                )
                return
            if self.active_session is not None:
                return
            session_config = replace(
                self.app_config,
                lbph_confidence_threshold=float(self.threshold_var.get()),
                template_distance_threshold=min(
                    MAX_TEMPLATE_DISTANCE,
                    max(MIN_TEMPLATE_DISTANCE, float(self.template_distance_var.get())),
                ),
                confirmation_frames=self._get_confirmation_frames_value(),
            )
            self.active_session = CameraAttendanceSession(session_config)
            self.active_session.start()
            self.system_status_label.config(
                text=f"System status: {self.active_session.system_summary}"
            )
            self.live_indicator.grid() # Show the live indicator
            self._schedule_video_update()
        except Exception as exc:
            self.active_session = None
            messagebox.showerror("Attendance Error", str(exc))

    def stop_attendance(self) -> None:
        if self.video_after_id is not None:
            self.root.after_cancel(self.video_after_id)
            self.video_after_id = None
        if self.active_session is not None:
            self.active_session.stop()
            self.active_session = None
        self.live_indicator.grid_remove()
        self.video_label.config(text="Webcam preview will appear here.", image="")
        self.video_photo = None
        self._reset_badge()
        self.badge_status_label.config(text="Waiting for a confirmed match.")
        self.system_status_label.config(text=f"System status: {self.session_config_summary}")
        self.refresh_attendance_log()

    def reset_today_attendance(self) -> None:
        confirmed = messagebox.askyesno(
            "Reset Attendance",
            "Clear today's attendance log and start over?",
        )
        if not confirmed:
            return

        self.attendance_logger.clear_records_for_day(datetime.now())
        if self.active_session is not None:
            self.active_session.reset_today_attendance_state()
            self.system_status_label.config(
                text=f"System status: {self.active_session.system_summary} | Attendance reset"
            )
        else:
            self.system_status_label.config(
                text=f"System status: {self.session_config_summary} | Attendance reset"
            )
        self._reset_badge()
        self.refresh_attendance_log()
        messagebox.showinfo("Reset Attendance", "Today's attendance log was cleared.")

    def _schedule_video_update(self) -> None:
        if self.active_session is None:
            return

        processed = self.active_session.get_latest_frame()
        if processed:
            self.system_status_label.config(text=f"System status: {self.active_session.system_summary}")
            if processed.frame is not None:
                self._update_video_panel(processed.frame)
            if processed.candidate is not None:
                self._show_confirmation_badge(processed.candidate)
        self.video_after_id = self.root.after(33, self._schedule_video_update)

    def _build_config_summary(self, config: AppConfig) -> str:
        if config.yolo_model_path is not None and config.yolo_model_path.exists():
            detector_name = f"YOLO ({config.yolo_model_path.name})"
        else:
            detector_name = "OpenCV Haar Cascade"

        if config.phone_screen_mode:
            detector_name = f"{detector_name} [Phone Mode]"

        recognizer_name = (
            f"LBPH (conf {config.lbph_confidence_threshold:.1f}, "
            f"dist {config.template_distance_threshold:.0f}, "
            f"confirmation {config.confirmation_frames})"
        )

        return f"Detector: {detector_name} | Recognizer: {recognizer_name}"

    def on_threshold_change(self, _value: str) -> None:
        threshold = float(self.threshold_var.get())
        self.threshold_entry_var.set(f"{threshold:.1f}")
        self.threshold_value_label.config(text=f"Current: {threshold:.1f}")

    def on_threshold_entry_commit(self, _event) -> None:
        try:
            threshold = float(self.threshold_entry_var.get())
        except ValueError:
            threshold = float(self.threshold_var.get())

        threshold = min(MAX_THRESHOLD, max(MIN_THRESHOLD, threshold))
        self.threshold_var.set(threshold)
        self.threshold_entry_var.set(f"{threshold:.1f}")
        self._update_threshold_display(threshold)

    def _update_threshold_display(self, threshold: float) -> None:
        self.threshold_value_label.config(text=f"Current: {threshold:.1f}")
        summary_config = replace(self.app_config, lbph_confidence_threshold=threshold)
        self.app_config = summary_config
        self.session_config_summary = self._build_config_summary(self.app_config)
        save_app_config(self.app_config)
        self._sync_live_recognition_thresholds()
        if self.active_session is None:
            self.system_status_label.config(
                text=f"System status: {self.session_config_summary}"
            )

    def on_template_distance_change(self, _value: str) -> None:
        distance = float(self.template_distance_var.get())
        self.template_distance_entry_var.set(f"{distance:.1f}")
        self.template_distance_value_label.config(text=f"Current: {distance:.1f}")

    def on_template_distance_entry_commit(self, _event) -> None:
        try:
            distance = float(self.template_distance_entry_var.get())
        except ValueError:
            distance = float(self.template_distance_var.get())

        distance = min(MAX_TEMPLATE_DISTANCE, max(MIN_TEMPLATE_DISTANCE, distance))
        self.template_distance_var.set(distance)
        self.template_distance_entry_var.set(f"{distance:.1f}")
        self.template_distance_value_label.config(text=f"Current: {distance:.1f}")
        summary_config = replace(self.app_config, template_distance_threshold=distance)
        self.app_config = summary_config
        self.session_config_summary = self._build_config_summary(self.app_config)
        save_app_config(self.app_config)
        self._sync_live_recognition_thresholds()
        if self.active_session is None:
            self.system_status_label.config(
                text=f"System status: {self.session_config_summary}"
            )

    def on_confirmation_frames_commit(self, _event) -> None:
        frames = self._get_confirmation_frames_value()
        self.confirmation_frames_var.set(str(frames))
        self.confirmation_frames_label.config(text=f"Current: {frames}")
        summary_config = replace(self.app_config, confirmation_frames=frames)
        self.app_config = summary_config
        self.session_config_summary = self._build_config_summary(self.app_config)
        save_app_config(self.app_config)
        if self.active_session is None:
            self.system_status_label.config(
                text=f"System status: {self.session_config_summary}"
            )

    def on_phone_screen_mode_toggle(self) -> None:
        summary_config = replace(
            self.app_config,
            # This method is not currently used, and phone_screen_mode_var is not defined.
            # If phone screen mode needs to be configurable, a UI element and corresponding
            # StringVar/BooleanVar would need to be added to the settings tab.
            # For now, we'll keep the existing config value.
            phone_screen_mode=self.app_config.phone_screen_mode,
        )
        self.app_config = summary_config
        self.session_config_summary = self._build_config_summary(self.app_config)
        save_app_config(self.app_config)
        if self.active_session is None:
            self.system_status_label.config(
                text=f"System status: {self.session_config_summary}"
            )

    
    def rebuild_recognizer_pipeline(self) -> None:
        """Triggers the active session's recognizer to rebuild with updated student data."""
        if self.active_session:
            self.active_session.reinitialize_recognizer()
            logger.info("Requested active session to reinitialize recognizer.")
        else:
            # Capture runs only while LiveStream is off; new enrollments load on next start.
            logger.info("No active session; recognizer will load new enrollments when LiveStream starts.")

    def reload_enrolled_faces(self) -> None:
        """Retrain LBPH from disk without restarting the camera (LiveStream must be on)."""
        if self.active_session is None:
            messagebox.showinfo(
                "Reload enrolled faces",
                "LiveStream is off. Start LiveStream to take attendance — saved student "
                "photos are loaded automatically when the stream starts.",
            )
            return
        self.rebuild_recognizer_pipeline()
        self.system_status_label.config(
            text=f"System status: {self.active_session.system_summary}"
        )

    def _sync_live_recognition_thresholds(self) -> None:
        """Apply LBPH/template tuning to a running recognizer (no full retrain)."""
        session = self.active_session
        if session is None or session.recognizer is None:
            return
        conf = min(MAX_THRESHOLD, max(MIN_THRESHOLD, float(self.threshold_var.get())))
        dist = min(
            MAX_TEMPLATE_DISTANCE,
            max(MIN_TEMPLATE_DISTANCE, float(self.template_distance_var.get())),
        )
        session.recognizer.confidence_threshold = conf
        session.recognizer.template_distance_threshold = dist
        if getattr(self, "system_status_label", None) is not None:
            self.system_status_label.config(text=f"System status: {session.system_summary}")

    def _get_confirmation_frames_value(self) -> int:
        try:
            frames = int(self.confirmation_frames_var.get())
        except ValueError:
            frames = self.app_config.confirmation_frames
        return min(10, max(1, frames))

    def _update_video_panel(self, frame) -> None:
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb_frame).resize((520, 320))
        self.video_photo = ImageTk.PhotoImage(image=image)
        self.video_label.config(image=self.video_photo, text="")

    def _show_confirmation_badge(self, candidate) -> None:
        snapshot = cv2.cvtColor(candidate.snapshot_frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(snapshot)
        image.thumbnail((252, 182))
        self.badge_photo = ImageTk.PhotoImage(image=image)
        self.badge_image_label.config(image=self.badge_photo, text="")
        self.badge_name_label.config(text=f"{candidate.full_name}")
        self.badge_id_label.config(text=f"ID: {candidate.student_id}")
        self.badge_major_label.config(text="Status: Ready to confirm")
        self.badge_status_label.config(
            text="Check Mark confirms this student. X rejects the match."
        )

    def confirm_badge(self) -> None:
        if self.active_session is None:
            return
        message = self.active_session.confirm_candidate()
        self._show_present_feedback(message)
        self.system_status_label.config(text=f"System status: {self.active_session.system_summary}")
        self.refresh_attendance_log()

    def reject_badge(self) -> None:
        if self.active_session is None:
            return
        self.active_session.reject_candidate()
        self._reset_badge()
        self.badge_status_label.config(
            text="Match rejected. Detection is still running for the current person."
        )
        self.system_status_label.config(text=f"System status: {self.active_session.system_summary}")

    def _reset_badge(self) -> None:
        if self.present_badge_after_id is not None:
            self.root.after_cancel(self.present_badge_after_id)
            self.present_badge_after_id = None
        self.badge_present_overlay.place_forget()
        self.badge_photo = None
        self.badge_image_label.config(image="", text="No verification pending")
        self.badge_name_label.config(text="No student")
        self.badge_id_label.config(text="ID: --")
        self.badge_major_label.config(text="Status: Waiting")
        if self.present_badge_message:
            self.badge_status_label.config(text=self.present_badge_message)
            self.present_badge_message = None
        else:
            self.badge_status_label.config(text="Waiting for a confirmed match.")

    def _show_present_feedback(self, message: str | None) -> None:
        if self.present_badge_after_id is not None:
            self.root.after_cancel(self.present_badge_after_id)
            self.present_badge_after_id = None
        self.present_badge_message = message
        self.badge_present_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.present_badge_after_id = self.root.after(2200, self._reset_badge)

    def _flash_unenroll_success(self, full_name: str) -> None:
        """Flashes a temporary overlay to confirm student deletion."""
        if self.unenroll_after_id:
            self.root.after_cancel(self.unenroll_after_id)
        if self.unenroll_overlay and self.unenroll_subtitle_label:
            self.unenroll_subtitle_label.config(text=f"{full_name} has been removed from the registry.")
            self.unenroll_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.unenroll_after_id = self.root.after(2200, lambda: self.unenroll_overlay.place_forget())

    def on_tab_changed(self, _event) -> None:
        selected_tab_index = self.notebook.index(self.notebook.select())
        # The dummy frame is at index 0, so our content tabs start at 1
        content_index = selected_tab_index - 1

        for i, btn in enumerate(self.sidebar_buttons):
            if i == content_index:
                btn.state(["selected"])
            else:
                btn.state(["!selected"])
        
        if content_index == 1: # Take Attendance
            if self.video_photo is not None:
                self.video_label.config(image=self.video_photo, text="")
            elif self.active_session is None:
                self.video_label.config(text="Webcam preview will appear here.", image="")
        elif content_index == 2: # Daily Report
            self._populate_daily_report()
        elif content_index == 3: # Weekly Report
            self._populate_weekly_report()
        elif content_index == 4: # Monthly Report
            self._populate_monthly_report()

    def _is_attendance_tab_selected(self) -> bool:
        if self.notebook is None or self.attendance_tab is None:
            return False
        return self.notebook.index(self.notebook.select()) == 2 # Enroll, Attend

    def _select_tab(self, index: int) -> None:
        self.notebook.select(index + 1) # Add 1 to account for the hidden dummy tab
    def on_close(self) -> None:
        self.close_live_capture()
        self.stop_attendance()
        self.root.destroy()

    # This method now only updates the Overview tab
    def refresh_attendance_log(self) -> None:
        # This method is called when data changes. We can trigger report updates here if needed,
        # but for now, the on_tab_changed event handles populating the visible report.
        pass

    def _on_return_key(self, _event=None) -> None:
        """Handle Enter key press to confirm a badge."""
        # Only confirm if there is an active session and a pending candidate
        if self.active_session and self.active_session.pending_confirmation:
            logger.debug("Enter key pressed, confirming badge.")
            self.confirm_badge()

    def _on_escape_key(self, _event=None) -> None:
        """Handle Escape key press to reject a badge or close popups."""
        if self.capture_window:
            self.close_live_capture()
        elif self.active_session and self.active_session.pending_confirmation:
            logger.debug("Escape key pressed, rejecting badge.")
            self.reject_badge()

    def _on_space_key(self, _event=None) -> None:
        """Handle Spacebar press to toggle the attendance session."""
        # Avoid triggering when typing in an entry field
        if isinstance(self.root.focus_get(), (ttk.Entry, tk.Entry)):
            return
        if self.active_session and self.active_session.running:
            self.stop_attendance()
        else:
            self.start_attendance()
