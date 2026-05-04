"""Session statistics and metrics tracking for RollCount."""

from __future__ import annotations
from .recognition import RecognitionResult

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

import numpy as np


logger = logging.getLogger(__name__)


@dataclass
class FrameMetrics:
    """Metrics for a single frame."""
    timestamp: datetime
    faces_detected: int
    avg_confidence: float = 0.0
    processing_time_ms: float = 0.0


@dataclass
class SessionStats:
    """Accumulated statistics for a session."""
    session_start: datetime
    frames_processed: int = 0
    total_detections: int = 0
    total_confirmations: int = 0
    total_rejections: int = 0
    _detection_filter: DuplicateDetectionFilter = field(default_factory=lambda: DuplicateDetectionFilter(cooldown_seconds=2.0))
    avg_confidence: float = 0.0
    frame_metrics: deque[FrameMetrics] = field(default_factory=lambda: deque(maxlen=300))  # Last 300 frames
    
    @property
    def session_duration(self) -> timedelta:
        """Get session duration."""
        return datetime.now() - self.session_start
    
    @property
    def session_duration_str(self) -> str:
        """Get formatted session duration string."""
        duration = self.session_duration
        minutes, seconds = divmod(int(duration.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    @property
    def fps(self) -> float:
        """Calculate frames per second."""
        if not self.frame_metrics or len(self.frame_metrics) < 2:
            return 0.0
        
        time_span = self.frame_metrics[-1].timestamp - self.frame_metrics[0].timestamp
        if time_span.total_seconds() == 0:
            return 0.0
        
        return len(self.frame_metrics) / time_span.total_seconds()
    
    def update_fps_metrics(self, avg_confidence: float = 0.0, processing_time_ms: float = 0.0) -> None:
        """Update FPS and confidence metrics.
        
        Args:
            avg_confidence: Average face detection confidence
            processing_time_ms: Time to process frame in milliseconds
        """
        self.frames_processed += 1
        metric = FrameMetrics(
            timestamp=datetime.now(),
            faces_detected=0,
            avg_confidence=avg_confidence,
            processing_time_ms=processing_time_ms,
        )
        self.frame_metrics.append(metric)
    
    def record_detection(self, student_id: str, confidence: float = 0.5) -> None:
        """Record a face detection.
        
        Args:
            student_id: The ID of the detected student.
            confidence: Detection confidence score
        """
        if self._detection_filter.is_duplicate(student_id):
            return
        self.total_detections += 1

        # Update running average confidence
        if self.total_detections == 1:
            self.avg_confidence = confidence
        else:
            self.avg_confidence = (
                (self.avg_confidence * (self.total_detections - 1) + confidence) 
                / self.total_detections
            )
        
        if self.frame_metrics:
            self.frame_metrics[-1].faces_detected += 1
    
    def record_confirmation(self) -> None:
        """Record a confirmed match."""
        self.total_confirmations += 1
    
    def record_rejection(self) -> None:
        """Record a rejected match."""
        self.total_rejections += 1
    
    def get_summary(self) -> str:
        """Get formatted summary of session statistics.
        
        Returns:
            Summary string
        """
        return (
            f"Session: {self.session_duration_str} | "
            f"Frames: {self.frames_processed} | "
            f"FPS: {self.fps:.1f} | "
            f"Detections: {self.total_detections} | "
            f"Confirmed: {self.total_confirmations} | "
            f"Avg Conf: {self.avg_confidence:.2f}"
        )


class DuplicateDetectionFilter:
    """Filter duplicate face detections within a time window."""
    
    def __init__(self, cooldown_seconds: float = 3.0) -> None:
        """Initialize filter.
        
        Args:
            cooldown_seconds: Minimum time between same face detections
        """
        self.cooldown_seconds = cooldown_seconds
        self.last_seen: dict[str, datetime] = {}
    
    def is_duplicate(self, student_id: str) -> bool:
        """Check if student was recently detected.
        
        Args:
            student_id: Student identifier
            
        Returns:
            True if this is a duplicate recent detection, False otherwise
        """
        now = datetime.now()
        if student_id not in self.last_seen:
            self.last_seen[student_id] = now
            return False
        
        time_since_last = (now - self.last_seen[student_id]).total_seconds()
        if time_since_last < self.cooldown_seconds:
            return True
        
        self.last_seen[student_id] = now
        return False
    
    def reset(self) -> None:
        """Clear all tracking data."""
        self.last_seen.clear()


class FaceQualityAssessment:
    """Assess quality of detected face for recognition."""
    
    @staticmethod
    def assess(box_width: int, box_height: int, confidence: float) -> dict[str, bool | float]:
        """Assess face quality based on multiple factors.
        
        Args:
            box_width: Width of face bounding box
            box_height: Height of face bounding box
            confidence: Detection confidence score (0-1)
            
        Returns:
            Dictionary with quality assessment results
        """
        min_size = 50
        ideal_size = 200
        max_size = 400
        
        size = (box_width + box_height) / 2
        
        return {
            "size_adequate": min_size <= size <= max_size,
            "size_ideal": ideal_size * 0.7 <= size <= ideal_size * 1.3,
            "confidence_high": confidence > 0.7,
            "confidence_medium": confidence > 0.5,
            "aspect_ratio_ok": 0.5 < (box_width / max(box_height, 1)) < 2.0,
            "quality_score": min(1.0, (confidence * 0.5) + ((size / ideal_size) * 0.5)),
        }
    
    @staticmethod
    def is_good_quality(assessment: dict) -> bool:
        """Determine if assessment indicates good quality.
        
        Args:
            assessment: Quality assessment dictionary
            
        Returns:
            True if quality is acceptable
        """
        return (
            assessment.get("size_adequate", False) and
            assessment.get("confidence_medium", False) and
            assessment.get("aspect_ratio_ok", False)
        )
