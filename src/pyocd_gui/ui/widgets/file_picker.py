"""Firmware file picker widget."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QPushButton,
    QStyle, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from pyocd_gui.core.firmware import FirmwareImage, parse_address, validate_images
from pyocd_gui.utils import config


class FilePicker(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Firmware", "BIN Address"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(110)
        self._table.setMaximumHeight(170)
        browse_btn = QPushButton("Add Images")
        browse_btn.clicked.connect(self._browse)
        remove_btn = QToolButton()
        remove_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        remove_btn.setToolTip("Remove selected images")
        remove_btn.clicked.connect(self._remove_selected)
        toolbar = QHBoxLayout()
        toolbar.addWidget(browse_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)
        layout.addLayout(toolbar)

        last = config.get("last_firmware_path", "")
        if last:
            self._add_paths([last])

    def _browse(self) -> None:
        first = self.firmware_path()
        start_dir = str(first.parent) if first else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Firmware File",
            start_dir,
            "Firmware files (*.bin *.hex *.elf);;All files (*)",
        )
        if paths:
            self._add_paths(paths)
            config.set("last_firmware_path", paths[0])

    def _add_paths(self, paths: list[str]) -> None:
        for path in paths:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item = QTableWidgetItem(path)
            item.setToolTip(path)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, item)
            address = QTableWidgetItem("")
            if Path(path).suffix.lower() == ".bin":
                address.setToolTip("BIN base address, e.g. 0x08004000. Blank = target boot address.")
            else:
                address.setText("Embedded")
                address.setFlags(address.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, address)

    def _remove_selected(self) -> None:
        rows = {index.row() for index in self._table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._table.removeRow(row)

    def firmware_images(self) -> list[FirmwareImage]:
        images = []
        for row in range(self._table.rowCount()):
            path = Path(self._table.item(row, 0).text())
            text = self._table.item(row, 1).text().strip()
            address = parse_address(text) if path.suffix.lower() == ".bin" and text else None
            images.append(FirmwareImage(path, address))
        validate_images(tuple(images))
        return images

    def firmware_path(self) -> Path | None:
        return Path(self._table.item(0, 0).text()) if self._table.rowCount() else None
