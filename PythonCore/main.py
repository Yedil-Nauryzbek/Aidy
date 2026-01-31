import os

from aidy.assistant import Aidy
from aidy.logui import ui_state, error


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        Aidy(base_dir=base_dir).run()
    except Exception as e:
        ui_state("ERROR")
        error(f"Failed to start: {e}")
        ui_state("IDLE")
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
