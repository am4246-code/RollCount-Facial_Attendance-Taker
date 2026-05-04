from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

ROOT_DIR: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = ROOT_DIR / "data"
STUDENTS_DIR: Path = DATA_DIR / "students"
ATTENDANCE_DIR: Path = DATA_DIR / "attendance"
ATTENDANCE_LOG_DIR: Path = DATA_DIR / "Attendance Log" # Revert: Keep this
DAILY_LOG_DIR: Path = ATTENDANCE_LOG_DIR / "Daily" # Revert: Keep this
WEEKLY_LOG_DIR: Path = ATTENDANCE_LOG_DIR / "Weekly" # Revert: Keep this
MONTHLY_LOG_DIR: Path = ATTENDANCE_LOG_DIR / "Monthly" # Revert: Keep this
TEMP_DIR: Path = DATA_DIR / "temp"
STUDENTS_DB_PATH: Path = DATA_DIR / "students.json"
DEFAULT_ATTENDANCE_FILE: Path = ATTENDANCE_DIR / "attendance.xlsx"
DEFAULT_YOLO_MODEL_PATH: Path = ROOT_DIR / "models" / "yolo26n.pt"
SETTINGS_PATH: Path = DATA_DIR / "settings.json"

# Configuration constraints
MIN_THRESHOLD: float = 20.0
MAX_THRESHOLD: float = 180.0
DEFAULT_THRESHOLD: float = 115.0
MIN_TEMPLATE_DISTANCE: float = 1500.0
MAX_TEMPLATE_DISTANCE: float = 10000.0
DEFAULT_TEMPLATE_DISTANCE: float = 5200.0
MIN_CONFIRMATION_FRAMES: int = 1
MAX_CONFIRMATION_FRAMES: int = 10
DEFAULT_CONFIRMATION_FRAMES: int = 5


@dataclass(frozen=True)
class AppConfig:
    """Application configuration with validation and defaults."""
    yolo_model_path: Path | None = (
        DEFAULT_YOLO_MODEL_PATH if DEFAULT_YOLO_MODEL_PATH.exists() else None
    )
    camera_index: int = 0
    recognition_cooldown_seconds: int = 10
    lbph_confidence_threshold: float = DEFAULT_THRESHOLD
    template_distance_threshold: float = DEFAULT_TEMPLATE_DISTANCE
    confirmation_frames: int = DEFAULT_CONFIRMATION_FRAMES
    locked_identity_grace_frames: int = 12
    detector_backend: str = "opencv"
    phone_screen_mode: bool = True


def ensure_directories() -> None:
    """Create required directories if they don't exist."""
    for path in (DATA_DIR, STUDENTS_DIR, ATTENDANCE_DIR, TEMP_DIR, ATTENDANCE_LOG_DIR, DAILY_LOG_DIR, WEEKLY_LOG_DIR, MONTHLY_LOG_DIR): # Revert: Keep all log dirs
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {path}: {e}")
            raise


def sanitize_filename(value: str) -> str:
    """Sanitize a string for use as a filename.
    
    Args:
        value: String to sanitize
        
    Returns:
        Sanitized filename, or "student" if empty after sanitization
    """
    allowed = []
    for char in value.strip():
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char.isspace():
            allowed.append("_")
    cleaned = "".join(allowed).strip("_")
    return cleaned or "student"


def _validate_threshold(value: float) -> float:
    """Validate and clamp threshold value.
    
    Args:
        value: Threshold value to validate
        
    Returns:
        Clamped threshold value within valid range
    """
    try:
        threshold = float(value)
        return min(MAX_THRESHOLD, max(MIN_THRESHOLD, threshold))
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid threshold value {value}: {e}. Using default.")
        return DEFAULT_THRESHOLD


def _validate_template_distance(value: float) -> float:
    """Validate and clamp template distance value.

    Args:
        value: Template distance value to validate

    Returns:
        Clamped template distance value within valid range
    """
    try:
        distance = float(value)
        return min(MAX_TEMPLATE_DISTANCE, max(MIN_TEMPLATE_DISTANCE, distance))
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid template distance value {value}: {e}. Using default.")
        return DEFAULT_TEMPLATE_DISTANCE

def _validate_confirmation_frames(value: int) -> int:
    """Validate and clamp confirmation frames value.
    
    Args:
        value: Confirmation frames value to validate
        
    Returns:
        Clamped confirmation frames value within valid range
    """
    try:
        frames = int(value)
        return min(MAX_CONFIRMATION_FRAMES, max(MIN_CONFIRMATION_FRAMES, frames))
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid confirmation frames value {value}: {e}. Using default.")
        return DEFAULT_CONFIRMATION_FRAMES


def load_app_config() -> AppConfig:
    """Load application configuration from settings file.
    
    Returns:
        AppConfig instance with loaded values, or defaults if file doesn't exist
    """
    ensure_directories()
    config = AppConfig()
    
    if not SETTINGS_PATH.exists():
        logger.info(f"Settings file not found at {SETTINGS_PATH}, using defaults")
        return config

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load settings from {SETTINGS_PATH}: {e}. Using defaults.")
        return config

    threshold = _validate_threshold(raw.get("lbph_confidence_threshold", DEFAULT_THRESHOLD))
    template_distance = _validate_template_distance(raw.get("template_distance_threshold", DEFAULT_TEMPLATE_DISTANCE))
    frames = _validate_confirmation_frames(raw.get("confirmation_frames", DEFAULT_CONFIRMATION_FRAMES))
    phone_screen_mode = bool(raw.get("phone_screen_mode", config.phone_screen_mode))

    return AppConfig(
        yolo_model_path=config.yolo_model_path,
        camera_index=config.camera_index,
        recognition_cooldown_seconds=config.recognition_cooldown_seconds,
        lbph_confidence_threshold=threshold,
        template_distance_threshold=template_distance,
        confirmation_frames=frames,
        detector_backend=config.detector_backend,
        phone_screen_mode=phone_screen_mode,
    )


def save_app_config(config: AppConfig) -> None:
    """Save application configuration to settings file.
    
    Args:
        config: AppConfig instance to save
        
    Raises:
        OSError: If settings file cannot be written
    """
    ensure_directories()
    data: dict[str, Any] = {
        "lbph_confidence_threshold": config.lbph_confidence_threshold,
        "template_distance_threshold": config.template_distance_threshold,
        "confirmation_frames": config.confirmation_frames,
        "phone_screen_mode": config.phone_screen_mode,
    }
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Configuration saved to {SETTINGS_PATH}")
    except OSError as e:
        logger.error(f"Failed to save configuration to {SETTINGS_PATH}: {e}")
        raise
