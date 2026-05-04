"""RollCount application entry point."""

import logging
import tkinter as tk
from rollcount.ui import RollCountApp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    """Initializes and runs the RollCount application."""
    logger.info("Starting RollCount application")
    try:
        root = tk.Tk()
        RollCountApp(root)
        root.mainloop()
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
