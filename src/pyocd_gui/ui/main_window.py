"""Main application window."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pyocd_gui.core.detect_worker import DetectWorker
from pyocd_gui.core.dump_worker import DumpWorker
from pyocd_gui.core.erase_worker import EraseWorker
from pyocd_gui.core.flash_worker import FlashWorker
from pyocd_gui.ui.widgets.file_picker import FilePicker
from pyocd_gui.ui.widgets.log_viewer import LogViewer
from pyocd_gui.ui.widgets.probe_selector import ProbeSelector
from pyocd_gui.ui.widgets.target_selector import TargetSelector


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pyOCD GUI")
        self.setMinimumWidth(640)
        self._worker: FlashWorker | None = None
        self._dump_worker: DumpWorker | None = None
        self._erase_worker: EraseWorker | None = None
        self._detect_worker: DetectWorker | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)

        # ---- Configuration group ----
        config_box = QGroupBox("Flash Configuration")
        form = QFormLayout(config_box)

        self._probe_selector = ProbeSelector()
        form.addRow(QLabel("Debug Probe:"), self._probe_selector)

        self._target_selector = TargetSelector()
        self._detect_btn = QPushButton("Detect")
        self._detect_btn.setToolTip("Auto-detect connected target chip")
        self._detect_btn.clicked.connect(self._start_detect)
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.addWidget(self._target_selector, 1)
        target_row.addWidget(self._detect_btn)
        form.addRow(QLabel("Target:"), target_row)

        self._file_picker = FilePicker()
        form.addRow(QLabel("Firmware:"), self._file_picker)

        self._verify_check = QCheckBox("Verify after flash")
        self._verify_check.setChecked(True)

        self._reset_check = QCheckBox("Reset after flash")
        self._reset_check.setChecked(True)

        self._erase_mode = QComboBox()
        self._erase_mode.setToolTip(
            "Chip: erase the entire flash before programming.\n"
            "Sector: erase only the sectors being written.\n"
            "Smart: pages whose contents already match are neither erased nor\n"
            "programmed, but pages that changed ARE still erased (sector erase).\n"
            "This is NOT a no-erase mode — pyOCD's public API has no true\n"
            "'skip erase before programming' option."
        )
        for label, mode in [
            ("Chip erase", "chip"),
            ("Sector erase", "sector"),
            ("Smart (skip unchanged pages)", "smart"),
        ]:
            self._erase_mode.addItem(label, userData=mode)

        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("Erase:"))
        opts_row.addWidget(self._erase_mode)
        opts_row.addWidget(self._verify_check)
        opts_row.addWidget(self._reset_check)
        opts_row.addStretch()
        form.addRow(opts_row)

        self._connect_mode = QComboBox()
        for mode, tip in [
            ("under-reset", "推荐：复位期间连接，兼容性最佳"),
            ("halt",        "连接后立即暂停"),
            ("pre-reset",   "连接前先复位"),
            ("attach",      "非侵入式附加，不暂停"),
        ]:
            self._connect_mode.addItem(mode, userData=mode)
            self._connect_mode.setItemData(
                self._connect_mode.count() - 1, tip, role=Qt.ItemDataRole.ToolTipRole
            )
        form.addRow(QLabel("Connect Mode:"), self._connect_mode)

        self._clock_combo = QComboBox()
        self._clock_combo.setEditable(True)
        for khz in [100, 240, 480, 960, 1000, 1800, 3600, 7200, 12000]:
            self._clock_combo.addItem(f"{khz} kHz", userData=khz)
        self._clock_combo.setCurrentIndex(self._clock_combo.findData(1000))
        self._clock_combo.setToolTip(
            "SWD/JTAG clock frequency.\n"
            "Lower this (e.g. 100–480 kHz) if the connection is unstable.\n"
            "Custom values can be typed in (plain number = kHz); an invalid\n"
            "entry falls back to the pyOCD default of 1000 kHz."
        )
        form.addRow(QLabel("SWD Clock:"), self._clock_combo)

        root.addWidget(config_box)

        # ---- Progress ----
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        root.addWidget(self._progress)

        # ---- Action buttons ----
        btn_row = QHBoxLayout()
        self._flash_btn = QPushButton("Flash")
        self._flash_btn.setFixedHeight(40)
        self._flash_btn.clicked.connect(self._start_flash)
        self._dump_btn = QPushButton("Read Flash")
        self._dump_btn.setFixedHeight(40)
        self._dump_btn.setToolTip("Read the entire flash contents and save it to a file")
        self._dump_btn.clicked.connect(self._start_dump)
        self._erase_flash_btn = QPushButton("Erase Flash")
        self._erase_flash_btn.setFixedHeight(40)
        self._erase_flash_btn.setToolTip("Erase the entire flash (mass erase)")
        self._erase_flash_btn.clicked.connect(self._start_erase)
        btn_row.addWidget(self._flash_btn, 1)
        btn_row.addWidget(self._dump_btn, 1)
        btn_row.addWidget(self._erase_flash_btn, 1)
        root.addLayout(btn_row)

        # ---- Log viewer ----
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self._log_viewer = LogViewer()
        log_layout.addWidget(self._log_viewer)
        root.addWidget(log_box, 1)

    # ------------------------------------------------------------------
    def _start_flash(self) -> None:
        firmware = self._file_picker.firmware_path()
        if firmware is None or not firmware.exists():
            QMessageBox.warning(self, "Invalid File", "Please select a valid firmware file.")
            return

        probe = self._probe_selector.selected_probe()
        target = self._target_selector.current_target()

        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(
                self,
                "No Target",
                "Please select a target type or run Detect first.\n\n"
                "The generic 'cortex_m' target cannot program flash.",
            )
            return

        self._set_busy(True)
        self._progress.setValue(0)
        self._log_viewer.append(f"Starting flash: {firmware.name}")

        self._worker = FlashWorker(
            firmware_path=firmware,
            target=target,
            probe_uid=probe.uid if probe else None,
            erase_mode=self._erase_mode.currentData(),
            verify=self._verify_check.isChecked(),
            connect_mode=self._connect_mode.currentData(),
            reset_after=self._reset_check.isChecked(),
            clock_khz=self._selected_clock_khz(),
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.log.connect(self._log_viewer.append)
        self._worker.finished.connect(self._on_flash_finished)
        self._worker.start()

    def _on_flash_finished(self, success: bool) -> None:
        self._set_busy(False)
        if success:
            self._log_viewer.append("Done.")
        else:
            self._log_viewer.append("Flash failed. Check the log above.")

    # ------------------------------------------------------------------
    def _start_dump(self) -> None:
        target = self._target_selector.current_target()
        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(
                self,
                "No Target",
                "Please select a target type or run Detect first.\n\n"
                "The generic 'cortex_m' target has no memory map, "
                "so the flash region is unknown.",
            )
            return

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save flash dump",
            f"{target}.bin",
            "Binary (*.bin);;Intel HEX (*.hex)",
        )
        if not path_str:
            return

        probe = self._probe_selector.selected_probe()
        self._set_busy(True)
        self._progress.setValue(0)
        self._log_viewer.append(f"Reading flash of '{target}' …")

        self._dump_worker = DumpWorker(
            out_path=Path(path_str),
            target=target,
            probe_uid=probe.uid if probe else None,
            connect_mode=self._connect_mode.currentData(),
            clock_khz=self._selected_clock_khz(),
            reset_after=self._reset_check.isChecked(),
        )
        self._dump_worker.progress.connect(self._progress.setValue)
        self._dump_worker.log.connect(self._log_viewer.append)
        self._dump_worker.finished.connect(self._on_dump_finished)
        self._dump_worker.start()

    def _on_dump_finished(self, success: bool) -> None:
        self._dump_btn.setEnabled(True)
        self._set_busy(False)
        self._log_viewer.append(
            "Flash dump complete." if success else "Flash dump failed."
        )

    # ------------------------------------------------------------------
    def _selected_clock_khz(self) -> int | None:
        """SWD clock in kHz from the combo box (predefined or typed), None = pyOCD default.

        Parsing is text-based: for an editable combo, ``currentData()`` reflects
        the selected *item*, not the edited text, so both would disagree after
        the user types a custom value.
        """
        text = self._clock_combo.currentText().strip().lower().removesuffix("khz").strip()
        try:
            return int(text)
        except ValueError:
            return None

    def _set_busy(self, busy: bool) -> None:
        """Enable/disable all action buttons — only one probe operation at a time."""
        for btn in (self._flash_btn, self._dump_btn, self._erase_flash_btn):
            btn.setEnabled(not busy)

    def _start_erase(self) -> None:
        target = self._target_selector.current_target()
        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(
                self,
                "No Target",
                "Please select a target type or run Detect first.\n\n"
                "The generic 'cortex_m' target cannot erase flash.",
            )
            return

        ret = QMessageBox.question(
            self,
            "Erase Flash",
            "Erase the ENTIRE flash of the target?\n\n"
            "All data on the flash will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        probe = self._probe_selector.selected_probe()
        self._set_busy(True)
        self._progress.setValue(0)
        self._log_viewer.append(f"Mass-erasing flash of '{target}' …")

        self._erase_worker = EraseWorker(
            target=target,
            probe_uid=probe.uid if probe else None,
            connect_mode=self._connect_mode.currentData(),
            clock_khz=self._selected_clock_khz(),
            reset_after=self._reset_check.isChecked(),
        )
        self._erase_worker.log.connect(self._log_viewer.append)
        self._erase_worker.finished.connect(self._on_erase_finished)
        self._erase_worker.start()

    def _on_erase_finished(self, success: bool) -> None:
        self._set_busy(False)
        self._progress.setValue(100 if success else 0)
        self._log_viewer.append(
            "Flash erased." if success else "Erase failed. Check the log above."
        )

    # ------------------------------------------------------------------
    def _start_detect(self) -> None:
        probe = self._probe_selector.selected_probe()
        self._detect_btn.setEnabled(False)
        self._detect_btn.setText("Detecting…")
        self._log_viewer.append("Detecting target chip …")
        self._detect_worker = DetectWorker(
            probe_uid=probe.uid if probe else None,
            clock_khz=self._selected_clock_khz(),
        )
        self._detect_worker.result.connect(self._on_detect_result)
        self._detect_worker.start()

    def _on_detect_result(self, target_name: object, msg: str) -> None:
        self._detect_btn.setEnabled(True)
        self._detect_btn.setText("Detect")
        self._log_viewer.append(msg)
        if target_name:
            self._target_selector.set_target(str(target_name))
