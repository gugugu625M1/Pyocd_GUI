"""Scrolling log viewer widget."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogViewer(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(2000)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._text.clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text)
        layout.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def append(self, message: str) -> None:
        self._text.appendPlainText(message)
        self._text.verticalScrollBar().setValue(
            self._text.verticalScrollBar().maximum()
        )
