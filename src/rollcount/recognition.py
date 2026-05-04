from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import STUDENTS_DIR
from .registry import Student, StudentRegistry


logger = logging.getLogger(__name__)

# Recognition thresholds and parameters
DEFAULT_CONFIDENCE_THRESHOLD = 70.0
DEFAULT_TEMPLATE_DISTANCE_THRESHOLD = 5200.0
DEFAULT_TEMPLATE_MARGIN_THRESHOLD = 1.4
# When LBPH already agrees with the closest template (same identity) and LBPH score is
# within the user threshold, allow template L2 up to this multiple of the strict cutoff.
# Tighter detector crops (e.g. YuNet) inflate raw L2 vs enrollment photos without meaning
# a different person.
_TEMPLATE_DISTANCE_SOFT_FACTOR = 2.2


@dataclass
class RecognitionResult:
    """Result of face recognition matching."""
    matched: bool
    student_id: str | None = None
    full_name: str | None = None
    confidence: float | None = None
    message: str = ""


@dataclass
class TemplateComparison:
    """Comparison metrics between face and templates."""
    best_label: int | None
    best_distance: float | None
    second_best_distance: float | None
    margin: float | None


class FaceRecognizer:
    """Recognizes student faces using LBPH algorithm and template matching."""
    
    def __init__(self, students_dir: Path = STUDENTS_DIR) -> None:
        """Initialize the face recognizer.
        
        Args:
            students_dir: Path to directory containing student images
        """
        self.students_dir = students_dir
        self.registry = StudentRegistry()
        self.label_to_student: dict[int, Student] = {}
        self.face_templates: dict[int, list[np.ndarray]] = {}
        self.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
        self.template_distance_threshold = DEFAULT_TEMPLATE_DISTANCE_THRESHOLD
        self.template_margin_threshold = DEFAULT_TEMPLATE_MARGIN_THRESHOLD
        
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        )
        if self._cascade.empty():
            logger.warning("Failed to load Haar Cascade classifier for face detection")
        
        self.recognizer = self._build_recognizer()

    def _build_recognizer(self) -> cv2.face.LBPHFaceRecognizer | None:  # type: ignore[attr-defined]
        """Build and train the LBPH recognizer from student images.
        
        Returns:
            Trained LBPH recognizer, or None if training fails or no students registered
        """
        try:
            recognizer = cv2.face.LBPHFaceRecognizer_create()  # type: ignore[attr-defined]
        except Exception as e:
            logger.error(f"Failed to create LBPH recognizer: {e}")
            return None

        faces: list[np.ndarray] = []
        labels: list[int] = []
        student_count = 0
        image_count = 0
        next_label = 0

        for student in self.registry.list_students():
            templates_for_student: list[np.ndarray] = []
            for image_path in student.image_paths:
                processed_face = self._load_training_face(Path(image_path))
                if processed_face is None:
                    logger.warning(f"Failed to process training image: {image_path}")
                    continue
                faces.append(processed_face)
                labels.append(next_label)
                templates_for_student.append(processed_face)
                image_count += 1

            if not templates_for_student:
                logger.warning(f"No valid images for student {student.student_id}: {student.full_name}")
                continue

            self.label_to_student[next_label] = student
            self.face_templates[next_label] = templates_for_student
            student_count += 1
            next_label += 1

        if not faces:
            logger.warning("No training faces available for LBPH recognizer")
            return None

        try:
            recognizer.train(faces, np.array(labels))
            logger.info(f"LBPH recognizer trained: {student_count} students, {image_count} images")
            return recognizer
        except Exception as e:
            logger.error(f"Failed to train LBPH recognizer: {e}")
            return None

    def recognize_face(self, face_image: np.ndarray) -> RecognitionResult:
        """Recognize a student from a face image.
        
        Args:
            face_image: Image containing a face to recognize
            
        Returns:
            RecognitionResult with student info if matched, error message otherwise
        """
        if self.recognizer is None:
            return RecognitionResult(
                matched=False,
                message="LBPH recognizer is unavailable or has no training data.",
            )

        if not self.label_to_student:
            return RecognitionResult(
                matched=False, message="No students are registered for recognition."
            )

        variant_pairs = self._prepare_face_variants(face_image)
        if not variant_pairs:
            return RecognitionResult(matched=False, message="Face crop could not be processed.")

        best_rejection: RecognitionResult | None = None
        for lbph_face, template_face in variant_pairs:
            result = self._recognize_processed_face(lbph_face, template_face)
            if result.matched:
                return result
            if best_rejection is None:
                best_rejection = result
            elif (
                result.confidence is not None
                and (best_rejection.confidence is None or result.confidence < best_rejection.confidence)
            ):
                best_rejection = result

        return best_rejection or RecognitionResult(
            matched=False,
            message="Could not confidently match this face.",
        )

    def _recognize_processed_face(
        self,
        lbph_face: np.ndarray,
        template_face: np.ndarray,
    ) -> RecognitionResult:
        """Recognize a single face variant pair.
        
        Enrollment templates are built from canonical preprocessing (equalize + blur).
        Augmented inputs (CLAHE/sharpen) help LBPH but must not be L2-compared to those
        templates or distances explode and every match fails.
        
        Args:
            lbph_face: Grayscale 200x200 tensor passed to LBPH predict
            template_face: Same-size tensor aligned with stored templates for L2 checks
            
        Returns:
            RecognitionResult for this variant
        """
        try:
            label, confidence = self.recognizer.predict(lbph_face)
        except Exception as exc:
            logger.debug(f"Recognition prediction failed: {exc}")
            return RecognitionResult(matched=False, message=f"Recognition error: {exc}")

        matched_student = self.label_to_student.get(label)
        if matched_student is None:
            logger.warning(f"Recognizer returned unknown label: {label}")
            return RecognitionResult(
                matched=False,
                message="Recognizer returned an unknown label.",
            )

        template_comparison = self._compare_to_templates(template_face)
        if template_comparison.best_label is None:
            return RecognitionResult(
                matched=False,
                confidence=float(confidence),
                message="No reliable template comparison was available.",
            )

        # If the LBPH label and template label differ, check if it's a close call.
        # This can happen if two students look very similar. We can be more lenient
        # if the confidence and template distances are still very good.
        if (
            template_comparison.best_label != label
            and template_comparison.margin is not None
            and template_comparison.margin < (self.template_margin_threshold * 2.0)
        ):
            # Only reject if the match isn't close. If margin is small, it's ambiguous.
            if template_comparison.margin < self.template_margin_threshold:
                logger.debug(f"Ambiguous match: LBPH label {label} != template label {template_comparison.best_label}")
                return RecognitionResult(
                    matched=False,
                    message="Ambiguous match. Please hold still and try again.",
                )

        if template_comparison.best_distance is not None:
            td = float(template_comparison.best_distance)
            strict = float(self.template_distance_threshold)
            soft_limit = strict * _TEMPLATE_DISTANCE_SOFT_FACTOR
            lbph_acceptable = float(confidence) <= self.confidence_threshold
            template_identity = template_comparison.best_label == label
            if td > strict:
                if (
                    template_identity
                    and lbph_acceptable
                    and td <= soft_limit
                ):
                    logger.debug(
                        "Template distance soft pass: dist=%.1f strict=%.1f soft_cap=%.1f lbph=%.1f",
                        td,
                        strict,
                        soft_limit,
                        float(confidence),
                    )
                else:
                    logger.debug(f"Template distance too high: {td:.1f}")
                    return RecognitionResult(
                        matched=False,
                        confidence=float(confidence),
                        message="Verifying...",
                    )

        if (
            template_comparison.margin is not None
            and template_comparison.margin < self.template_margin_threshold
        ):
            logger.debug(f"Template margin too low: {template_comparison.margin:.1f}")
            return RecognitionResult(
                matched=False,
                confidence=float(confidence),
                message=(
                    "Match is too close to another student. "
                    f"Margin {template_comparison.margin:.1f}."
                ),
            )

        if confidence > self.confidence_threshold:
            logger.debug(f"Confidence too high: {confidence:.1f}")
            return RecognitionResult(
                matched=False,
                confidence=float(confidence),
                message=f"Match rejected (confidence {confidence:.1f}).",
            )

        logger.debug(f"Match found: {matched_student.student_id} ({confidence:.1f})")
        return RecognitionResult(
            matched=True,
            student_id=matched_student.student_id,
            full_name=matched_student.full_name,
            confidence=float(confidence),
            message="Match found.",
        )

    def _load_training_face(self, image_path: Path) -> np.ndarray | None:
        """Load and prepare a training face image.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Prepared face image or None if processing fails
        """
        try:
            image = cv2.imread(str(image_path))
            if image is None:
                logger.warning(f"Failed to read image: {image_path}")
                return None
            # Match webcam path: attendance uses detector face crops; training must
            # build templates from detected faces, not a shrink of the full frame.
            prepared = self._prepare_face(image, detect_face=True)
            if prepared is not None:
                return prepared
            logger.debug(
                "No frontal face detected in training image %s; using full-frame fallback",
                image_path,
            )
            return self._prepare_face(image, detect_face=False)
        except Exception as e:
            logger.error(f"Error loading training face {image_path}: {e}")
            return None

    def _trim_face_margin(self, region: np.ndarray) -> np.ndarray | None:
        """Apply the same edge trim used for live detector crops."""
        height, width = region.shape[:2]
        if height < 20 or width < 20:
            return None
        pad_y = max(int(height * 0.08), 1)
        pad_x = max(int(width * 0.08), 1)
        top = min(pad_y, height // 4)
        bottom = max(height - pad_y, top + 1)
        left = min(pad_x, width // 4)
        right = max(width - pad_x, left + 1)
        cropped = region[top:bottom, left:right]
        return cropped if cropped.size > 0 else None

    def _prepare_face(self, image: np.ndarray, detect_face: bool) -> np.ndarray | None:
        """Prepare a face for recognition processing.
        
        Args:
            image: Input image (BGR format)
            detect_face: Whether to detect face region or use full image
            
        Returns:
            Normalized face image or None if processing fails
        """
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)

            if detect_face:
                h, w = gray.shape[:2]
                short_side = min(h, w)
                # Webcam/detector crops are often smaller than enrollment photos; slightly
                # looser Haar settings improve inner-face alignment vs templates.
                if short_side < 160:
                    scale_factor = 1.06
                    min_neighbors = 3
                    min_size = (18, 18)
                else:
                    scale_factor = 1.1
                    min_neighbors = 5
                    min_size = (20, 20)
                detected = self._cascade.detectMultiScale(
                    gray,
                    scaleFactor=scale_factor,
                    minNeighbors=min_neighbors,
                    minSize=min_size,
                )
                if len(detected) == 0:
                    return None
                x, y, width, height = max(detected, key=lambda box: box[2] * box[3])
                roi = gray[y : y + height, x : x + width]
            else:
                roi = gray

            face_region = self._trim_face_margin(roi)
            if face_region is None:
                return None

            normalized_face = cv2.resize(face_region, (200, 200))
            return cv2.GaussianBlur(normalized_face, (3, 3), 0)
        except Exception as e:
            logger.error(f"Error preparing face: {e}")
            return None

    def _prepare_face_variants(self, image: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """Create LBPH/template variant pairs for robust recognition.
        
        Each pair is ``(lbph_input, template_input)``. Templates on disk use canonical
        preprocessing only; ``template_input`` is always that canonical tensor while
        ``lbph_input`` may include CLAHE/sharpen augmentations for harder lighting.
        
        Enrollment templates use Haar alignment inside each photo (see
        `_load_training_face`). Live frames use detector crops that may be full-body
        (YOLO/person) or loose boxes; we mirror training by preferring Haar inside
        the crop when possible, and keeping the full crop as a fallback when it
        differs enough to help LBPH/template matching.
        
        Args:
            image: Input image (BGR format), typically a detector face/person crop
            
        Returns:
            List of (lbph_face, template_face) pairs, same 200x200 shape
        """
        aligned = self._prepare_face(image, detect_face=True)
        full_roi = self._prepare_face(image, detect_face=False)

        bases: list[np.ndarray] = []
        if aligned is not None:
            bases.append(aligned)
        if full_roi is not None:
            if aligned is None:
                bases.append(full_roi)
            else:
                diff = float(
                    cv2.norm(
                        full_roi.astype(np.float32),
                        aligned.astype(np.float32),
                        cv2.NORM_L2,
                    )
                )
                # Include whole-crop encoding when inner-face crop is very different
                # (common when the detector box is not tight on the face).
                if diff > 300.0:
                    bases.append(full_roi)

        if not bases:
            return []

        pairs: list[tuple[np.ndarray, np.ndarray]] = []
        for base in bases:
            pairs.extend(self._derivative_variant_pairs(base))
        return pairs

    def _derivative_variant_pairs(self, base: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """Canonical ``base`` for templates; optional augmentations only for LBPH."""
        out: list[tuple[np.ndarray, np.ndarray]] = [(base, base)]
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(base)
            aug = cv2.GaussianBlur(enhanced, (3, 3), 0)
            out.append((aug, base))

            sharpen_kernel = np.array(
                [[0, -1, 0], [-1, 5, -1], [0, -1, 0]],
                dtype=np.float32,
            )
            sharp = cv2.filter2D(base, -1, sharpen_kernel)
            out.append((sharp, base))
        except Exception as e:
            logger.error(f"Error creating face variants: {e}")
        return out

    def _compare_to_templates(self, processed_face: np.ndarray) -> TemplateComparison:
        """Compare a face against all template faces.
        
        Args:
            processed_face: Preprocessed face image
            
        Returns:
            TemplateComparison with distance metrics
        """
        distances: list[tuple[int, float]] = []
        
        for label, templates in self.face_templates.items():
            try:
                best_distance_for_student = min(
                    cv2.norm(processed_face, template, cv2.NORM_L2)
                    for template in templates
                )
                distances.append((label, best_distance_for_student))
            except Exception as e:
                logger.debug(f"Error comparing to student templates: {e}")
                continue

        if not distances:
            return TemplateComparison(None, None, None, None)

        distances.sort(key=lambda item: item[1])
        best_label, best_distance = distances[0]
        second_best_distance = distances[1][1] if len(distances) > 1 else None
        margin = (
            second_best_distance - best_distance
            if second_best_distance is not None
            else None
        )
        return TemplateComparison(
            best_label=best_label,
            best_distance=best_distance,
            second_best_distance=second_best_distance,
            margin=margin,
        )

    @property
    def status_text(self) -> str:
        """Get human-readable status of the recognizer.
        
        Returns:
            Status string describing recognizer state
        """
        if self.recognizer is None:
            return "LBPH unavailable"
        return (
            f"LBPH ready ({len(self.label_to_student)} students, "
            f"threshold {self.confidence_threshold:.1f})"
        )
