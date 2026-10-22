"""File-versus-Flash comparison dialog."""

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCloseEvent, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton, QStyle,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from pyocd_gui.core.compare_worker import CompareWorker
from pyocd_gui.core.firmware import FirmwareImage


class CompareDialog(QDialog):
    def __init__(
        self, images: list[FirmwareImage], target: str, probe_uid: str | None = None,
        clock_khz: int | None = None, parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare File with Flash")
        self.resize(820, 580)
        self._images = list(images)
        self._target = target
        self._probe_uid = probe_uid
        self._clock_khz = clock_khz
        self._worker: CompareWorker | None = None
        self._report: dict | None = None
        self._busy = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for title, text in (("Target:", target), ("Probe UID:", probe_uid or "Auto-select")):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(title, label)
        layout.addLayout(form)

        self._sources = QTableWidget(len(images), 3)
        self._sources.setHorizontalHeaderLabels(["File", "Flash Address", "File Bytes"])
        self._sources.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sources.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._sources.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._sources.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._sources.setMaximumHeight(130)
        for row, image in enumerate(images):
            address = (
                f"0x{image.base_address:08X}" if image.base_address is not None
                else "Target boot address" if image.path.suffix.lower() == ".bin" else "Embedded"
            )
            for column, value in enumerate((image.path.name, address, f"{image.path.stat().st_size:,}")):
                item = QTableWidgetItem(value)
                item.setToolTip(str(image.path))
                self._sources.setItem(row, column, item)
        layout.addWidget(self._sources)

        self._freeze_check = QCheckBox("Freeze STM32F1 watchdogs during comparison")
        self._freeze_check.setEnabled(target.lower().startswith("stm32f1"))
        self._freeze_check.setToolTip("Temporarily freeze IWDG/WWDG while halted; restore DBGMCU_CR afterwards.")
        layout.addWidget(self._freeze_check)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)
        self._status = QLabel("Ready")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        status_font = self._status.font()
        status_font.setBold(True)
        self._status.setFont(status_font)
        layout.addWidget(self._status)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._summary)

        self._differences = QTableWidget(0, 4)
        self._differences.setHorizontalHeaderLabels(["Address", "Image", "File Byte", "Flash Byte"])
        self._differences.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._differences.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._differences.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._differences.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._differences.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._differences, 1)
        self._detail_note = QLabel("")
        self._detail_note.setWordWrap(True)
        layout.addWidget(self._detail_note)

        footer = QHBoxLayout()
        self._export_btn = QPushButton("Export Differences")
        self._export_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        footer.addWidget(self._export_btn)
        footer.addStretch()
        self._start_btn = QPushButton("Compare")
        self._start_btn.clicked.connect(self._start)
        footer.addWidget(self._start_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        footer.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        footer.addWidget(self._close_btn)
        layout.addLayout(footer)

    def _start(self) -> None:
        if self._busy or (self._worker is not None and self._worker.isRunning()):
            return
        self._report = None
        self._differences.setRowCount(0)
        self._summary.clear()
        self._detail_note.clear()
        self._progress.setValue(0)
        self._status.setText("Comparing...")
        self._set_busy(True)
        self._worker = CompareWorker(
            self._images, self._target, probe_uid=self._probe_uid, clock_khz=self._clock_khz,
            freeze_stm32f1_watchdogs=self._freeze_check.isChecked(),
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.result.connect(self._show_result)
        self._worker.error.connect(self._show_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._start_btn.setEnabled(not busy)
        self._close_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        self._freeze_check.setEnabled(not busy and self._target.lower().startswith("stm32f1"))
        self._export_btn.setEnabled(not busy and self._report is not None and bool(self._report["differences"]))

    def _cancel(self) -> None:
        if self._worker is not None and self._busy:
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status.setText("Cancelling...")

    def _show_result(self, report: dict) -> None:
        self._report = report
        self._status.setText("MATCH - File and Flash are identical" if report["matched"]
                             else "DIFFERENT - File and Flash do not match")
        first = f"0x{report['first_mismatch']:08X}" if report["first_mismatch"] is not None else "None"
        self._summary.setText(
            f"Compared: {report['compared_bytes']:,} bytes   "
            f"Different: {report['mismatch_count']:,} bytes   First difference: {first}"
        )
        self._differences.setRowCount(len(report["differences"]))
        for row, difference in enumerate(report["differences"]):
            values = (f"0x{difference['address']:08X}", difference["image"],
                      f"{difference['file_byte']:02X}", f"{difference['flash_byte']:02X}")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column in (2, 3):
                    item.setBackground(QColor("#ffe4e6"))
                    item.setForeground(QColor("#9f1239"))
                self._differences.setItem(row, column, item)
        self._detail_note.setText(
            f"Displayed differences: {len(report['differences']):,} / {report['mismatch_count']:,}"
        )

    def _show_error(self, message: str) -> None:
        self._status.setText(message)
        self._summary.setText("Comparison incomplete. No valid result.")

    def _on_finished(self, success: bool) -> None:
        self._set_busy(False)
        if success:
            self._progress.setValue(100)

    def _export(self) -> None:
        if self._busy or self._report is None or not self._report["differences"]:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Displayed Differences", "flash-differences.csv", "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Address", "Image", "File Byte", "Flash Byte"])
                for difference in self._report["differences"]:
                    writer.writerow([
                        f"0x{difference['address']:08X}", difference["image"],
                        f"{difference['file_byte']:02X}", f"{difference['flash_byte']:02X}",
                    ])
        except OSError as error:
            QMessageBox.warning(self, "Export Failed", str(error))

    def done(self, result: int) -> None:
        if not self._busy and not (self._worker is not None and self._worker.isRunning()):
            super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy or (self._worker is not None and self._worker.isRunning()):
            event.ignore()
        else:
            super().closeEvent(event)