"""Firmware file picker widget."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from pyocd_gui.utils import config


class FilePicker(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Select firmware file (.bin / .hex / .elf)")
        self._path_edit.setReadOnly(True)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._path_edit, 1)
        layout.addWidget(browse_btn)

        # Restore last used path
        last = config.get("last_firmware_path", "")
        if last:
            self._path_edit.setText(last)

    def _browse(self) -> None:
        start_dir = str(Path(self._path_edit.text()).parent) if self._path_edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Firmware File",
            start_dir,
            "Firmware files (*.bin *.hex *.elf);;All files (*)",
        )
        if path:
            self._path_edit.setText(path)
            config.set("last_firmware_path", path)

    def firmware_path(self) -> Path | None:
        text = self._path_edit.text().strip()
        return Path(text) if text else None
