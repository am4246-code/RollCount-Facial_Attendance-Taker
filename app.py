"""RollCount application entry point."""

import logging
import sys
from pathlib import Path

# Add the 'src' directory to the Python path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import tkinter as tk
from loading import LoadingScreen
from rollcount.ui import RollCountApp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    """Initializes and runs the RollCount application."""
    try:
        root = tk.Tk()
        root.title("RollCount")
        root.geometry("1440x940")
        root.configure(bg="#e9eef5")

        app: RollCountApp | None = None

        def load_main_app():
            """Build the main app and destroy the loading screen."""
            nonlocal app
            loading_screen.destroy()
            app = RollCountApp(root)
            app._select_tab(0)

        # Show loading screen, then schedule the main app to load.
        loading_screen = LoadingScreen(root, on_load_complete=load_main_app)
        root.after(2500, load_main_app)  # A small delay to ensure the loading screen renders
        root.mainloop()
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
