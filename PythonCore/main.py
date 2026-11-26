import os
import sys

from aidy.assistant import Aidy
from aidy.logui import ui_state, error, UI_MODE


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        ui_state("STARTING")
        Aidy(base_dir=base_dir).run()
    except Exception as e:
        ui_state("ERROR")
        error(f"Failed to start: {e}")
        if not UI_MODE:
            input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
