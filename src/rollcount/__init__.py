"""RollCount package - Classroom attendance system with face detection and recognition."""

import logging
import sys

# Configure package-level logging
logger = logging.getLogger(__name__)

# Set up logging if not already configured
if not logging.getLogger().handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

__all__ = ["logger"]
