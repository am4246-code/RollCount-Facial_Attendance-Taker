from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .config import STUDENTS_DB_PATH, STUDENTS_DIR, ensure_directories, sanitize_filename


logger = logging.getLogger(__name__)


@dataclass
class Student:
    """Student record with ID, name, and associated images."""
    student_id: str
    full_name: str
    image_paths: list[str]


class StudentRegistry:
    """Manages student records and registration."""
    
    def __init__(self, db_path: Path = STUDENTS_DB_PATH) -> None:
        """Initialize the student registry.
        
        Args:
            db_path: Path to the students database JSON file
        """
        self.db_path = db_path
        ensure_directories()
        if not self.db_path.exists():
            self._save([])
            logger.info(f"Created new student database at {self.db_path}")

    def _load(self) -> list[Student]:
        """Load all students from the database.
        
        Returns:
            List of Student objects from the database
            
        Raises:
            OSError: If the database file cannot be read
            json.JSONDecodeError: If the database file is corrupted
        """
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                raw: list[dict[str, Any]] = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Error loading student database: {e}")
            raise

        students: list[Student] = []
        for item in raw:
            try:
                # Handle legacy single image_path field
                image_paths = item.get("image_paths")
                if image_paths is None and "image_path" in item:
                    image_paths = [item["image_path"]]
                students.append(
                    Student(
                        student_id=item["student_id"],
                        full_name=item["full_name"],
                        image_paths=list(image_paths or []),
                    )
                )
            except (KeyError, TypeError) as e:
                logger.warning(f"Skipping malformed student record: {e}")
                continue
        return students

    def _save(self, students: list[Student]) -> None:
        """Save all students to the database.
        
        Args:
            students: List of Student objects to save
            
        Raises:
            OSError: If the database file cannot be written
        """
        try:
            data: list[dict[str, Any]] = [asdict(student) for student in students]
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(students)} students to database")
        except OSError as e:
            logger.error(f"Error saving student database: {e}")
            raise

    def list_students(self) -> list[Student]:
        """Get all registered students, sorted by name.
        
        Returns:
            List of Student objects sorted alphabetically by full name
        """
        try:
            return sorted(self._load(), key=lambda student: student.full_name.lower())
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to list students: {e}")
            return []

    def get_student_name(self, student_id: str) -> str | None:
        """Retrieve the full name of a student by their ID.
        
        Args:
            student_id: The ID of the student to look up.
            
        Returns:
            The full name of the student, or None if not found.
        """
        for student in self.list_students():
            if student.student_id == student_id:
                return student.full_name

    def get_student_by_id(self, student_id: str) -> Student | None:
        """Retrieve a complete student object by their ID.

        Args:
            student_id: The ID of the student to look up.

        Returns:
            The Student object, or None if not found.
        """
        for student in self.list_students():
            if student.student_id == student_id:
                return student

    def register_student(
        self,
        student_id: str,
        full_name: str,
        image_sources: list[Path],
    ) -> Student:
        """Register a new student with reference images.
        
        Args:
            student_id: Unique student identifier
            full_name: Student's full name
            image_sources: List of 2-3 reference image paths
            
        Returns:
            Newly registered Student object
            
        Raises:
            ValueError: If validation fails (empty fields, invalid image count, duplicate ID)
            FileNotFoundError: If any image file doesn't exist
        """
        student_id = student_id.strip()
        full_name = full_name.strip()
        
        if not student_id or not full_name:
            raise ValueError("Student ID and full name are required.")
        if not 2 <= len(image_sources) <= 3:
            raise ValueError("Please choose 2 or 3 reference images.")
        
        for image_source in image_sources:
            if not image_source.exists():
                raise FileNotFoundError(f"Image file not found: {image_source}")

        students = self.list_students()
        if any(student.student_id == student_id for student in students):
            raise ValueError(f"Student ID '{student_id}' is already registered.")

        safe_name = sanitize_filename(full_name)
        safe_id = sanitize_filename(student_id)
        target_paths: list[str] = []
        
        for index, image_source in enumerate(image_sources, start=1):
            extension = image_source.suffix.lower() or ".jpg"
            target_path = STUDENTS_DIR / f"{safe_id}_{safe_name}_{index}{extension}"
            try:
                shutil.copy2(image_source, target_path)
                target_paths.append(str(target_path))
            except OSError as e:
                logger.error(f"Failed to copy image {image_source}: {e}")
                raise

        student = Student(
            student_id=student_id,
            full_name=full_name,
            image_paths=target_paths,
        )
        students.append(student)
        self._save(students)
        logger.info(f"Registered student {student_id}: {full_name}")
        return student

    def delete_student(self, student_id: str) -> bool:
        """Delete a student and their associated images.
        
        Args:
            student_id: ID of student to delete
            
        Returns:
            True if student was deleted, False if not found
        """
        students = self.list_students()
        remaining: list[Student] = []
        deleted_student: Student | None = None

        for student in students:
            if student.student_id == student_id:
                deleted_student = student
            else:
                remaining.append(student)

        if deleted_student is None:
            logger.warning(f"Student {student_id} not found for deletion")
            return False

        for image_path in deleted_student.image_paths:
            path = Path(image_path)
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning(f"Failed to delete image {path}: {e}")

        self._save(remaining)
        logger.info(f"Deleted student {student_id}: {deleted_student.full_name}")
        return True

    def replace_student_images(self, student_id: str, image_sources: list[Path]) -> bool:
        """Replace a student's reference images.
        
        Args:
            student_id: ID of student to update
            image_sources: List of 2-3 new reference image paths
            
        Returns:
            True if images were replaced, False if student not found
            
        Raises:
            ValueError: If image count is invalid or student not found
            FileNotFoundError: If any image file doesn't exist
        """
        if not 2 <= len(image_sources) <= 3:
            raise ValueError("Please choose 2 or 3 reference images.")
        
        for image_source in image_sources:
            if not image_source.exists():
                raise FileNotFoundError(f"Image file not found: {image_source}")

        students = self.list_students()
        student_found = False

        for student in students:
            if student.student_id != student_id:
                continue

            safe_name = sanitize_filename(student.full_name)
            safe_id = sanitize_filename(student.student_id)

            # Delete old images
            for image_path in student.image_paths:
                path = Path(image_path)
                if path.exists():
                    try:
                        path.unlink()
                    except OSError as e:
                        logger.warning(f"Failed to delete old image {path}: {e}")

            # Copy new images
            new_paths: list[str] = []
            for index, image_source in enumerate(image_sources, start=1):
                extension = image_source.suffix.lower() or ".jpg"
                target_path = STUDENTS_DIR / f"{safe_id}_{safe_name}_{index}{extension}"
                try:
                    shutil.copy2(image_source, target_path)
                    new_paths.append(str(target_path))
                except OSError as e:
                    logger.error(f"Failed to copy image {image_source}: {e}")
                    raise

            student.image_paths = new_paths
            student_found = True
            break

        if student_found:
            self._save(students)
            logger.info(f"Replaced images for student {student_id}")
        else:
            logger.warning(f"Student {student_id} not found for image replacement")
            raise ValueError(f"Student ID '{student_id}' not found.")
        
        return student_found
