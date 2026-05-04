from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import ROOT_DIR


logger = logging.getLogger(__name__)

# YuNet (OpenCV FaceDetectorYN) — small, fast face-specific detector
YUNET_ONNX_NAME = "face_detection_yunet_2023mar.onnx"
YUNET_DOWNLOAD_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    + YUNET_ONNX_NAME
)

# Post-detection limits (pixels² and aspect ratio)
_MIN_FACE_SIDE = 22
_MAX_FRAME_COVERAGE = 0.72  # reject boxes covering most of the frame (false positives)
_MAX_OUTPUT_FACES = 2
_NMS_IOU = 0.22
# Extra context around YuNet/Haar boxes so LBPH/templates see similar framing to
# older, looser Haar crops (tight boxes inflate template L2 vs enrollment photos).
_BOX_PADDING = 0.14

# YuNet thresholds — slightly higher score reduces duplicate / jitter boxes.
_YUNET_SCORE = 0.62
_YUNET_NMS = 0.45
_YUNET_TOP_K = 1500


@dataclass
class FaceBox:
    """Bounding box for a detected face with confidence score."""

    x: int
    y: int
    width: int
    height: int
    confidence: float = 0.5

    def iou(self, other: FaceBox) -> float:
        x_left = max(self.x, other.x)
        y_top = max(self.y, other.y)
        x_right = min(self.x + self.width, other.x + other.width)
        y_bottom = min(self.y + self.height, other.y + other.height)

        if x_right < x_left or y_bottom < y_top:
            return 0.0

        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        self_area = self.width * self.height
        other_area = other.width * other.height
        union_area = self_area + other_area - intersection_area
        if union_area == 0:
            return 0.0
        return intersection_area / union_area

    def expand(self, padding_percent: float = _BOX_PADDING) -> FaceBox:
        pad_x = max(1, int(self.width * padding_percent))
        pad_y = max(1, int(self.height * padding_percent))
        return FaceBox(
            x=max(0, self.x - pad_x),
            y=max(0, self.y - pad_y),
            width=self.width + 2 * pad_x,
            height=self.height + 2 * pad_y,
            confidence=self.confidence,
        )


def _greedy_largest_non_overlapping(boxes: list[FaceBox], iou_cap: float = 0.32) -> list[FaceBox]:
    """Keep spatially separated faces; if two boxes overlap a lot, keep the larger."""
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: -b.width * b.height * max(0.05, b.confidence))
    kept: list[FaceBox] = []
    for b in ordered:
        if any(b.iou(k) >= iou_cap for k in kept):
            continue
        kept.append(b)
    return kept


def _nms(boxes: list[FaceBox], iou_threshold: float = _NMS_IOU) -> list[FaceBox]:
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b.confidence, reverse=True)
    kept: list[FaceBox] = []
    while ordered:
        current = ordered.pop(0)
        kept.append(current)
        ordered = [b for b in ordered if current.iou(b) < iou_threshold]
    return kept


def _clip_box_to_frame(box: FaceBox, frame_w: int, frame_h: int) -> FaceBox | None:
    x = max(0, min(box.x, frame_w - 1))
    y = max(0, min(box.y, frame_h - 1))
    w = max(1, min(box.width, frame_w - x))
    h = max(1, min(box.height, frame_h - y))
    if w < _MIN_FACE_SIDE or h < _MIN_FACE_SIDE:
        return None
    return FaceBox(x=x, y=y, width=w, height=h, confidence=box.confidence)


def _filter_face_boxes(
    boxes: list[FaceBox],
    frame_shape: tuple[int, ...],
    *,
    phone_screen_mode: bool,
) -> list[FaceBox]:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    frame_area = float(frame_w * frame_h)
    min_area = 1600.0 if phone_screen_mode else 3200.0
    max_area = frame_area * _MAX_FRAME_COVERAGE
    min_ar = 0.48 if phone_screen_mode else 0.58
    max_ar = 1.75 if phone_screen_mode else 1.62

    out: list[FaceBox] = []
    for box in boxes:
        clipped = _clip_box_to_frame(box, frame_w, frame_h)
        if clipped is None:
            continue
        area = float(clipped.width * clipped.height)
        ar = clipped.width / float(max(1, clipped.height))
        if area < min_area or area > max_area:
            continue
        if ar < min_ar or ar > max_ar:
            continue
        expanded = clipped.expand(_BOX_PADDING)
        expanded = _clip_box_to_frame(expanded, frame_w, frame_h)
        if expanded is None:
            continue
        out.append(expanded)

    out.sort(key=lambda b: (-b.confidence, -b.width * b.height))
    out = out[:_MAX_OUTPUT_FACES]
    return _greedy_largest_non_overlapping(out, iou_cap=0.28)


def _ensure_yunet_weights(models_dir: Path) -> Path | None:
    """Return path to YuNet ONNX, downloading once if missing."""
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / YUNET_ONNX_NAME
    if target.exists() and target.stat().st_size > 10_000:
        return target
    try:
        logger.info("Downloading YuNet face model to %s", target)
        with urllib.request.urlopen(YUNET_DOWNLOAD_URL, timeout=120) as resp:
            target.write_bytes(resp.read())
        logger.info("YuNet model saved (%s bytes)", target.stat().st_size)
        return target
    except Exception as exc:
        logger.warning("Could not download YuNet model: %s", exc)
        return None


class FaceDetector:
    """Face detection: YuNet when available, otherwise unified multi-scale Haar."""

    def __init__(self, yolo_model_path: Path | None = None, phone_screen_mode: bool = False) -> None:
        # Kept for API compatibility; generic YOLO models are not used for face boxes.
        self.yolo_model_path = yolo_model_path
        self.phone_screen_mode = phone_screen_mode
        self._yunet: Any = None
        self._yunet_backend = False

        self._cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(self._cascade_path)
        if self._cascade.empty():
            logger.error("Failed to load Haar cascade at %s", self._cascade_path)

        self._init_yunet()

        if yolo_model_path:
            logger.debug(
                "YOLO path %s is ignored for face detection; use a dedicated face model internally.",
                yolo_model_path,
            )

    def _init_yunet(self) -> None:
        if not hasattr(cv2, "FaceDetectorYN"):
            logger.info("FaceDetectorYN not available in this OpenCV build; using Haar only.")
            return
        model_path = _ensure_yunet_weights(ROOT_DIR / "models")
        if model_path is None:
            return
        try:
            # Input size updated every frame via setInputSize
            self._yunet = cv2.FaceDetectorYN.create(
                str(model_path),
                "",
                (320, 240),
                _YUNET_SCORE,
                _YUNET_NMS,
                _YUNET_TOP_K,
            )
            self._yunet_backend = True
            logger.info("YuNet face detector ready (%s)", model_path.name)
        except Exception as exc:
            self._yunet = None
            logger.warning("YuNet init failed, falling back to Haar: %s", exc)

    @property
    def backend_name(self) -> str:
        suffix = " + phone assist" if self.phone_screen_mode else ""
        if self._yunet_backend and self._yunet is not None:
            return f"YuNet ({YUNET_ONNX_NAME}){suffix}"
        return f"Haar multiscale{suffix}"

    def detect(self, frame: np.ndarray) -> list[FaceBox]:
        if frame.ndim < 2:
            return []
        h, w = frame.shape[:2]
        candidates: list[FaceBox] = []

        yunet_boxes: list[FaceBox] = []
        if self._yunet_backend and self._yunet is not None:
            yunet_boxes = self._detect_yunet(frame)

        # YuNet alone is usually enough; stacking Haar on top causes overlapping
        # boxes that flicker frame-to-frame. Fall back to Haar only when YuNet empty.
        if yunet_boxes:
            candidates.extend(yunet_boxes)
        else:
            candidates.extend(self._detect_haar_multiscale(frame))

        if self.phone_screen_mode and not yunet_boxes:
            candidates.extend(self._detect_phone_assist(frame))
        elif self.phone_screen_mode and yunet_boxes:
            # Light assist only when YuNet is weak (single low-score box)
            if len(yunet_boxes) == 1 and yunet_boxes[0].confidence < 0.68:
                candidates.extend(self._detect_phone_assist(frame))

        merged = _nms(candidates, _NMS_IOU)
        return _filter_face_boxes(merged, frame.shape, phone_screen_mode=self.phone_screen_mode)

    def _detect_yunet(self, frame: np.ndarray) -> list[FaceBox]:
        assert self._yunet is not None
        h, w = frame.shape[:2]
        try:
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
        except Exception as exc:
            logger.debug("YuNet detect failed: %s", exc)
            return []

        if faces is None or len(faces) == 0:
            return []

        out: list[FaceBox] = []
        for row in np.asarray(faces, dtype=np.float32):
            if row.shape[0] < 15:
                continue
            x, y, fw, fh = int(row[0]), int(row[1]), int(row[2]), int(row[3])
            conf = float(row[14])
            if fw < 1 or fh < 1:
                continue
            out.append(FaceBox(x=x, y=y, width=fw, height=fh, confidence=min(1.0, max(0.0, conf))))
        return out

    def _detect_haar_multiscale(self, frame: np.ndarray) -> list[FaceBox]:
        if self._cascade.empty():
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

        raw: list[tuple[int, int, int, int]] = []
        for scale in (1.04, 1.08, 1.12):
            det = self._cascade.detectMultiScale(
                gray,
                scaleFactor=scale,
                minNeighbors=3,
                minSize=(_MIN_FACE_SIDE, _MIN_FACE_SIDE),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )
            raw.extend([(int(x), int(y), int(w), int(h)) for x, y, w, h in det])

        if not raw:
            return []

        boxes = [FaceBox(x=x, y=y, width=w, height=h, confidence=0.52) for x, y, w, h in raw]
        return _nms(boxes, 0.28)

    def _detect_phone_assist(self, frame: np.ndarray) -> list[FaceBox]:
        """Second pass tuned for faces on phone/tablet screens (moiré, soft edges)."""
        if self._cascade.empty():
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
        sharp = cv2.addWeighted(gray, 1.55, blur, -0.55, 0)
        up = cv2.resize(sharp, None, fx=1.35, fy=1.35, interpolation=cv2.INTER_CUBIC)
        up = np.clip(up, 0, 255).astype(np.uint8)

        det = self._cascade.detectMultiScale(
            up,
            scaleFactor=1.05,
            minNeighbors=2,
            minSize=(28, 28),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        inv = 1.35
        out: list[FaceBox] = []
        for x, y, w, h in det:
            out.append(
                FaceBox(
                    x=int(x / inv),
                    y=int(y / inv),
                    width=int(w / inv),
                    height=int(h / inv),
                    confidence=0.48,
                )
            )
        return _nms(out, 0.28)
