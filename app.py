"""RollCount application entry point."""

import logging
import sys
from pathlib import Path

# Add the 'src' directory to the Python path
# This allows the interpreter to find the 'rollcount' package correctly
SRC_PATH = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_PATH)) # Ensure this line is present
import tkinter as tk
from rollcount.ui import RollCountApp


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Starting RollCount application")
    try:
        root = tk.Tk()
        RollCountApp(root)
        root.mainloop()
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        raise
