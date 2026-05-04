from __future__ import annotations

import logging
import queue
import time
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

from .attendance import AttendanceLogger, AttendanceRecord
from .config import ATTENDANCE_DIR, AppConfig, ensure_directories
from .detector import FaceBox, FaceDetector, _BOX_PADDING
from .stats import SessionStats
from .recognition import FaceRecognizer


logger = logging.getLogger(__name__)

_FACE_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FACE_LABEL_MARGIN = 8


def _wrap_overlay_lines(
    text: str,
    max_width: int,
    font_scale: float,
    thickness: int,
) -> list[str]:
    """Split a label into lines that fit within max_width (pixels)."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        tw, _ = cv2.getTextSize(trial, _FACE_LABEL_FONT, font_scale, thickness)[0]
        if tw <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


@dataclass
class PendingRecognition:
    """Recognition pending user confirmation."""
    student_id: str
    full_name: str
    confidence: float | None
    count: int = 1


@dataclass
class LockedRecognition:
    """Face recognition locked to a student."""
    student_id: str
    full_name: str
    confidence: float | None
    missing_frames: int = 0


@dataclass
class ConfirmationCandidate:
    """Student awaiting manual confirmation."""
    student_id: str
    full_name: str
    confidence: float | None
    snapshot_frame: Any


@dataclass
class ProcessedFrame:
    frame: Any | None
    candidate: ConfirmationCandidate | None


class CameraAttendanceSession:
    """Manages face detection and attendance recording from camera input."""
    
    def __init__(self, config: AppConfig | None = None) -> None:
        """Initialize an attendance session.
        
        Args:
            config: Application configuration, or None to use defaults
        """
        self.config = config or AppConfig()
        self.detector: FaceDetector | None = None
        self.recognizer: FaceRecognizer | None = None
        self._pipeline_ready = False
        self._pipeline_error: str | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._pipeline_lock = threading.Lock()
        self.logger = AttendanceLogger()
        today_records = self.logger.records_for_day(datetime.now())
        self.present_today_ids = {record.student_id for record in today_records}
        self.last_seen_times: dict[str, datetime] = defaultdict(lambda: datetime.min)
        self.pending_recognition: PendingRecognition | None = None
        self.pending_checked_in_warning: PendingRecognition | None = None
        self.locked_recognition: LockedRecognition | None = None
        self.pending_confirmation: ConfirmationCandidate | None = None
        self.awaiting_clear_frame = False
        self.clear_frame_streak = 0
        self.clear_wait_started_at: datetime | None = None
        self.clearance_message = "Please step away so the next student can check in."
        self.capture: cv2.VideoCapture | None = None
        self.stats = SessionStats(session_start=datetime.now())
        self.running = False

        # Threading for non-blocking processing
        self._processing_thread: threading.Thread | None = None
        self._frame_queue = queue.Queue(maxsize=1)
        self._display_boxes_smooth: list[FaceBox] | None = None

        logger.debug("CameraAttendanceSession initialized")

    def _smooth_display_boxes(self, boxes: list[FaceBox]) -> list[FaceBox]:
        """EMA-stabilize box positions when count is stable to reduce UI flicker."""
        alpha = 0.42
        if not boxes:
            self._display_boxes_smooth = None
            return []

        if self._display_boxes_smooth is None or len(self._display_boxes_smooth) != len(boxes):
            self._display_boxes_smooth = list(boxes)
            return list(boxes)

        def _cx(b: FaceBox) -> float:
            return b.x + 0.5 * b.width

        prev_sorted = sorted(self._display_boxes_smooth, key=_cx)
        new_sorted = sorted(boxes, key=_cx)
        out: list[FaceBox] = []
        for pb, nb in zip(prev_sorted, new_sorted):
            x = int(round(alpha * nb.x + (1.0 - alpha) * pb.x))
            y = int(round(alpha * nb.y + (1.0 - alpha) * pb.y))
            w = max(1, int(round(alpha * nb.width + (1.0 - alpha) * pb.width)))
            h = max(1, int(round(alpha * nb.height + (1.0 - alpha) * pb.height)))
            conf = min(1.0, max(0.0, alpha * nb.confidence + (1.0 - alpha) * pb.confidence))
            out.append(FaceBox(x=x, y=y, width=w, height=h, confidence=conf))
        self._display_boxes_smooth = out
        return out

    def _draw_face_overlay_label(
        self,
        frame,
        box,
        label: str,
        color: tuple[int, int, int],
        *,
        font_scale: float = 0.6,
        thickness: int = 2,
    ) -> None:
        """Draw label above the face box, wrapping and clamping so text stays in-frame."""
        _, frame_w = frame.shape[:2]
        max_width = max(40, frame_w - 2 * _FACE_LABEL_MARGIN)
        lines = _wrap_overlay_lines(label, max_width, font_scale, thickness)
        if not lines:
            return

        gap = 4
        rev_lines = list(reversed(lines))
        metrics = [
            cv2.getTextSize(line, _FACE_LABEL_FONT, font_scale, thickness)
            for line in rev_lines
        ]

        y_cursor = max(25, box.y - 10)
        positions: list[tuple[str, int, int]] = []
        for line, ((tw, th), baseline) in zip(rev_lines, metrics):
            x = max(
                _FACE_LABEL_MARGIN,
                min(int(box.x), frame_w - tw - _FACE_LABEL_MARGIN),
            )
            positions.append((line, x, y_cursor))
            y_cursor -= th + baseline + gap

        if positions:
            top_line_idx = len(positions) - 1
            _, _, y_top_baseline = positions[top_line_idx]
            (_, th_top), bl_top = metrics[-1]
            approx_top = y_top_baseline - th_top - bl_top
            shift = _FACE_LABEL_MARGIN - approx_top
            if shift > 0:
                positions = [(ln, x, y + shift) for ln, x, y in positions]

        for line, x, y in positions:
            cv2.putText(
                frame,
                line,
                (x, y),
                _FACE_LABEL_FONT,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
            )

    def start(self) -> None:
        """Start the camera session.
        
        Raises:
            RuntimeError: If the webcam cannot be opened
        """
        if self.running:
            logger.debug("Session already running")
            return

        self.stats = SessionStats(session_start=datetime.now())
        self._display_boxes_smooth = None
        try:
            # Prefer DirectShow on Windows for faster device startup
            if hasattr(cv2, "CAP_DSHOW"):
                self.capture = cv2.VideoCapture(self.config.camera_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(self.config.camera_index)
            
            if not self.capture.isOpened():
                self.capture.release()
                self.capture = None
                raise RuntimeError("Unable to open webcam.")
            
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.capture.grab()
            self.running = True
            logger.info(f"Camera session started on device {self.config.camera_index}")
            self._processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
            self._processing_thread.start()
            logger.info("Frame processing thread started.")

            self._start_pipeline_initialization()
        except Exception as e:
            logger.error(f"Failed to start camera session: {e}")
            raise

    def stop(self) -> None:
        """Stop the camera session and clean up resources."""
        self.running = False
        if self._processing_thread is not None and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=1.0)
            self._processing_thread = None

        if self.capture is not None:
            try:
                self.capture.release()
            except Exception as e:
                logger.warning(f"Error releasing camera: {e}")
            self.capture = None
        self._display_boxes_smooth = None
        logger.info("Camera session stopped")

    def reinitialize_recognizer(self) -> None:
        """Reinitializes the FaceRecognizer instance with current config."""
        with self._pipeline_lock:
            try:
                self.recognizer = FaceRecognizer()
                self.recognizer.confidence_threshold = self.config.lbph_confidence_threshold
                self.recognizer.template_distance_threshold = self.config.template_distance_threshold
                logger.info("FaceRecognizer reinitialized successfully.")
            except Exception as e:
                logger.error(f"Failed to reinitialize FaceRecognizer: {e}")
                self._pipeline_error = f"Recognizer reinit failed: {e}"

    def get_latest_frame(self) -> ProcessedFrame | None:
        """Get the latest processed frame from the queue without blocking."""
        try:
            return self._frame_queue.get_nowait()
        except queue.Empty:
            return None

    def _processing_loop(self) -> None:
        """The main loop for the background processing thread."""
        while self.running:
            if not self.running: # Check immediately after waking up
                break

            processed_frame = self._process_one_frame()
            try:
                # Put the processed frame in the queue, overwriting if full
                self._frame_queue.put_nowait(processed_frame)
            except queue.Full:
                pass # This is expected, just means the UI is a bit behind

    def _process_one_frame(self) -> ProcessedFrame:
        if not self.running or self.capture is None:
            return ProcessedFrame(None, None)
        has_frame, frame = self.capture.read()
        if not has_frame:
            return ProcessedFrame(None, None)

        start_time = time.perf_counter()
        raw_frame = frame.copy()

        if not self._pipeline_ready:
            self._draw_startup_status(frame)
            self.stats.update_fps_metrics()
            return ProcessedFrame(frame, None)

        if self.detector is None or self.recognizer is None:
            self._draw_startup_status(frame)
            self.stats.update_fps_metrics()
            return ProcessedFrame(frame, None)

        boxes = self._smooth_display_boxes(self.detector.detect(frame))
        if not boxes:
            self.pending_recognition = None
            self.pending_checked_in_warning = None

        if self.awaiting_clear_frame:
            has_meaningful_face = self._has_meaningful_face(boxes)
            if has_meaningful_face:
                self.clear_frame_streak = 0
                self._draw_clearance_message(frame)
            else:
                self.clear_frame_streak += 1
                if self.clear_frame_streak >= 5:
                    self.awaiting_clear_frame = False
                    self.clear_frame_streak = 0
                    self.clear_wait_started_at = None
            if (
                self.awaiting_clear_frame
                and self.clear_wait_started_at is not None
                and (datetime.now() - self.clear_wait_started_at).total_seconds() >= 4.0
            ):
                # Safety release for occasional detector false positives.
                self.awaiting_clear_frame = False
                self.clear_frame_streak = 0
                self.clear_wait_started_at = None
            self.stats.update_fps_metrics()
            return ProcessedFrame(frame, None)

        if not boxes:
            self._advance_lock_without_face()

        for box in boxes:
            face_crop = frame[box.y : box.y + box.height, box.x : box.x + box.width]
            label = "Detection only"
            color = (0, 255, 255)

            if self.pending_confirmation is not None:
                candidate = self.pending_confirmation
                if candidate.confidence is not None:
                    label = (
                        f"Awaiting confirmation: {candidate.full_name} "
                        f"({candidate.student_id}) - score {candidate.confidence:.1f}"
                    )
                else:
                    label = f"Awaiting confirmation: {candidate.full_name} ({candidate.student_id})"
                color = (255, 200, 0)
            elif self.locked_recognition is not None:
                self.locked_recognition.missing_frames = 0
                if self.locked_recognition.confidence is not None:
                    label = (
                        f"{self.locked_recognition.full_name} "
                        f"({self.locked_recognition.student_id}) "
                        f"- locked score {self.locked_recognition.confidence:.1f}"
                    )
                else:
                    label = (
                        f"{self.locked_recognition.full_name} "
                        f"({self.locked_recognition.student_id}) - locked"
                    )
                color = (0, 200, 0)
            else:
                result = self.recognizer.recognize_face(face_crop)
                if result.matched and result.student_id and result.full_name:
                    if result.student_id in self.present_today_ids:
                        self.stats.record_detection(result.student_id, result.confidence or 0.0)
                        warned, streak = self._update_checked_in_warning(
                            result.student_id,
                            result.full_name,
                            result.confidence,
                        )
                        if warned:
                            label = f"{result.full_name} already checked in. Please step away."
                            color = (0, 140, 255)
                            self._begin_waiting_for_clear_frame(
                                f"{result.full_name} is already checked in. Please step away."
                            )
                            cv2.rectangle(
                                frame,
                                (box.x, box.y),
                                (box.x + box.width, box.y + box.height),
                                color,
                                2,
                            )
                            self._draw_face_overlay_label(frame, box, label, color)
                            break
                        label = (
                            f"Checking previous check-in for {result.full_name} "
                            f"({streak}/3)"
                        )
                        color = (0, 190, 255)
                    self.stats.record_detection(result.student_id, result.confidence or 0.0)
                    confirmed, streak = self._update_pending_match(
                        result.student_id,
                        result.full_name,
                        result.confidence,
                    )
                    if confirmed:
                        snapshot_frame = raw_frame
                        self.pending_confirmation = ConfirmationCandidate(
                            student_id=result.student_id,
                            full_name=result.full_name,
                            confidence=result.confidence,
                            snapshot_frame=snapshot_frame,
                        )
                        self.pending_recognition = None
                        if result.confidence is not None:
                            label = (
                                f"Awaiting confirmation: {result.full_name} "
                                f"({result.student_id}) - score {result.confidence:.1f}"
                            )
                        else:
                            label = (
                                f"Awaiting confirmation: {result.full_name} "
                                f"({result.student_id})"
                            )
                        color = (255, 200, 0)
                    else:
                        if result.confidence is not None:
                            label = (
                                f"Verifying {result.full_name} "
                                f"({streak}/{self.config.confirmation_frames}) "
                                f"- score {result.confidence:.1f}"
                            )
                        else:
                            label = (
                                f"Verifying {result.full_name} "
                                f"({streak}/{self.config.confirmation_frames})"
                            )
                        color = (0, 215, 255)
                elif result.message:
                    self.pending_recognition = None
                    self.pending_checked_in_warning = None
                    if result.confidence is not None:
                        label = f"{result.message} score {result.confidence:.1f}"
                    else:
                        label = result.message
                    color = (0, 165, 255)

            cv2.rectangle(
                frame,
                (box.x, box.y),
                (box.x + box.width, box.y + box.height),
                color,
                2,
            )
            self._draw_face_overlay_label(frame, box, label, color)

        candidate = self.pending_confirmation
        processing_time_ms = (time.perf_counter() - start_time) * 1000
        avg_confidence = self.stats.avg_confidence if self.stats.total_detections > 0 else 0.0
        self.stats.update_fps_metrics(
            avg_confidence=avg_confidence, processing_time_ms=processing_time_ms
        )
        return ProcessedFrame(frame, candidate)

    def confirm_candidate(self) -> str | None:
        if self.pending_confirmation is None:
            return None

        candidate = self.pending_confirmation
        self._lock_identity(
            candidate.student_id,
            candidate.full_name,
            candidate.confidence,
        )
        self.pending_confirmation = None
        self._begin_waiting_for_clear_frame()
        saved = self._record_if_needed(candidate.student_id, candidate.full_name)
        self.stats.record_confirmation()
        self._save_confirmation_snapshot(candidate)
        if saved:
            return f"{candidate.full_name} marked present."
        return f"{candidate.full_name} was already marked present today."

    def reject_candidate(self) -> None:
        self.pending_confirmation = None
        self.pending_recognition = None
        self.pending_checked_in_warning = None
        self.stats.record_rejection()

    def reset_today_attendance_state(self) -> None:
        self.present_today_ids.clear()
        self.last_seen_times.clear()
        self.pending_recognition = None
        self.pending_checked_in_warning = None
        self.locked_recognition = None
        self.pending_confirmation = None
        self.awaiting_clear_frame = False
        self.clear_frame_streak = 0
        self.clear_wait_started_at = None

    def _record_if_needed(self, student_id: str, full_name: str) -> bool:
        now = datetime.now()
        last_seen = self.last_seen_times[student_id]
        seconds_since_last_seen = (now - last_seen).total_seconds()
        if seconds_since_last_seen < self.config.recognition_cooldown_seconds:
            return False

        saved = self.logger.append_record(
            AttendanceRecord(student_id=student_id, full_name=full_name, recorded_at=now)
        )
        if saved:
            self.last_seen_times[student_id] = now
            self.present_today_ids.add(student_id)
        return saved

    @property
    def system_summary(self) -> str:
        if self._pipeline_error:
            return f"Recognizer startup error: {self._pipeline_error}"
        if not self._pipeline_ready:
            return "Starting camera and recognition pipeline..."

        detector_name = self.detector.backend_name if self.detector is not None else "Detector unavailable"
        recognizer_name = (
            self.recognizer.status_text if self.recognizer is not None else "Recognizer unavailable"
        )
        status = (
            f"Detector: {detector_name} | "
            f"Recognizer: {recognizer_name}"
        )
        if self.pending_confirmation is not None:
            return f"{status} | Awaiting manual confirmation"
        if self.awaiting_clear_frame:
            return f"{status} | Waiting for next student"
        if self.locked_recognition is not None:
            return f"{status} | Locked on {self.locked_recognition.full_name}"
        # Defensive check: self.stats should always be initialized, but this adds robustness.
        if self.stats:
            return f"{status} | {self.stats.get_summary()}"
        return status # Fallback if stats somehow not initialized (highly unlikely)

    def _draw_system_status(self, frame) -> None:
        cv2.putText(
            frame,
            self.system_summary,
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
        )

    def _draw_startup_status(self, frame) -> None:
        message = self.system_summary
        cv2.putText(
            frame,
            message,
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )

    def _start_pipeline_initialization(self) -> None:
        if self._pipeline_thread is not None and self._pipeline_thread.is_alive():
            return

        self._pipeline_ready = False
        self._pipeline_error = None

        def _initialize() -> None:
            try:
                detector = FaceDetector(
                    self.config.yolo_model_path,
                    phone_screen_mode=self.config.phone_screen_mode,
                )
                recognizer = FaceRecognizer()
                recognizer.confidence_threshold = self.config.lbph_confidence_threshold
                recognizer.template_distance_threshold = self.config.template_distance_threshold

                with self._pipeline_lock:
                    self.detector = detector
                    self.recognizer = recognizer
                    self._pipeline_ready = True
            except Exception as exc:
                with self._pipeline_lock:
                    self._pipeline_error = str(exc)
                    self._pipeline_ready = False

        self._pipeline_thread = threading.Thread(target=_initialize, daemon=True)
        self._pipeline_thread.start()

    def _update_pending_match(
        self,
        student_id: str,
        full_name: str,
        confidence: float | None,
    ) -> tuple[bool, int]:
        if (
            self.pending_recognition is not None
            and self.pending_recognition.student_id == student_id
        ):
            self.pending_recognition.count += 1
            self.pending_recognition.confidence = confidence
        else:
            self.pending_recognition = PendingRecognition(
                student_id=student_id,
                full_name=full_name,
                confidence=confidence,
            )

        confirmed = self.pending_recognition.count >= self.config.confirmation_frames
        return confirmed, self.pending_recognition.count

    def _lock_identity(
        self,
        student_id: str,
        full_name: str,
        confidence: float | None,
    ) -> None:
        self.locked_recognition = LockedRecognition(
            student_id=student_id,
            full_name=full_name,
            confidence=confidence,
        )
        self.pending_recognition = None
        self.pending_checked_in_warning = None

    def _advance_lock_without_face(self) -> None:
        if self.locked_recognition is None:
            return
        self.locked_recognition.missing_frames += 1
        if self.locked_recognition.missing_frames >= self.config.locked_identity_grace_frames:
            self.locked_recognition = None
            self.pending_recognition = None

    def _begin_waiting_for_clear_frame(self, message: str | None = None) -> None:
        self.locked_recognition = None
        self.pending_recognition = None
        self.pending_checked_in_warning = None
        self.awaiting_clear_frame = True
        self.clear_frame_streak = 0
        self.clear_wait_started_at = datetime.now()
        self.clearance_message = (
            message or "Please step away so the next student can check in."
        )

    def _update_checked_in_warning(
        self,
        student_id: str,
        full_name: str,
        confidence: float | None,
    ) -> tuple[bool, int]:
        if (
            self.pending_checked_in_warning is not None
            and self.pending_checked_in_warning.student_id == student_id
        ):
            self.pending_checked_in_warning.count += 1
            self.pending_checked_in_warning.confidence = confidence
        else:
            self.pending_checked_in_warning = PendingRecognition(
                student_id=student_id,
                full_name=full_name,
                confidence=confidence,
            )

        warned = self.pending_checked_in_warning.count >= 3
        self.pending_recognition = None # Clear any pending matches for other people
        return warned, self.pending_checked_in_warning.count

    def _draw_clearance_message(self, frame) -> None:
        cv2.putText(
            frame,
            self.clearance_message,
            (10, 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 220, 0),
            2,
        )

    def _has_meaningful_face(self, boxes) -> bool:
        for box in boxes:
            # Ignore tiny jitter detections so "step away" can clear reliably.
            if box.width * box.height >= 4500:
                return True
        return False

    def _save_confirmation_snapshot(self, candidate: ConfirmationCandidate) -> None:
        try:
            ensure_directories()
            day_dir = ATTENDANCE_DIR / "snapshots" / datetime.now().strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H%M%S")
            safe_id = "".join(char for char in candidate.student_id if char.isalnum() or char in {"-", "_"})
            safe_name = "".join(char for char in candidate.full_name if char.isalnum() or char in {"-", "_", " "}).strip().replace(" ", "_")
            output_path = day_dir / f"{safe_id}_{safe_name}_{timestamp}.jpg"
            cv2.imwrite(str(output_path), candidate.snapshot_frame)
        except Exception:
            # Snapshot saving is a non-blocking enhancement; attendance should still succeed.
            return
