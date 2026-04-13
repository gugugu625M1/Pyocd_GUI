"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from pyocd_gui.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("pyOCD GUI")
    app.setApplicationVersion("0.1.0")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
