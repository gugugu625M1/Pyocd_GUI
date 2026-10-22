"""Main application window."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyocd_gui.core.detect_worker import DetectWorker
from pyocd_gui.core.device_worker import DeviceWorker
from pyocd_gui.core.dump_worker import DumpWorker
from pyocd_gui.core.erase_worker import EraseWorker
from pyocd_gui.core.flash_worker import FlashWorker
from pyocd_gui.core.firmware import parse_address
from pyocd_gui.ui.compare_dialog import CompareDialog
from pyocd_gui.ui.pack_manager import PackManagerDialog
from pyocd_gui.ui.widgets.file_picker import FilePicker
from pyocd_gui.ui.widgets.log_viewer import LogViewer
from pyocd_gui.ui.widgets.probe_selector import ProbeSelector
from pyocd_gui.ui.widgets.target_selector import TargetSelector


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pyOCD GUI")
        self.setMinimumWidth(800)
        self._busy = False
        self._worker: FlashWorker | None = None
        self._dump_worker: DumpWorker | None = None
        self._erase_worker: EraseWorker | None = None
        self._detect_worker: DetectWorker | None = None
        self._device_worker: DeviceWorker | None = None

        self._build_ui()
        self._build_info_panel()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)

        # ---- Configuration group ----
        config_box = QGroupBox("Flash Configuration")
        self._config_box = config_box
        form = QFormLayout(config_box)

        self._probe_selector = ProbeSelector()
        form.addRow(QLabel("Debug Probe:"), self._probe_selector)

        self._target_selector = TargetSelector()
        self._detect_btn = QPushButton("Detect")
        self._detect_btn.setToolTip("Auto-detect connected target chip")
        self._detect_btn.clicked.connect(self._start_detect)
        self._packs_btn = QPushButton("DFP Packs")
        self._packs_btn.clicked.connect(self._open_pack_manager)
        self._info_btn = QPushButton("Chip Info")
        self._info_btn.clicked.connect(lambda: self._start_device_action("info"))
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.addWidget(self._target_selector, 1)
        target_row.addWidget(self._detect_btn)
        target_row.addWidget(self._packs_btn)
        target_row.addWidget(self._info_btn)
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
        self._erase_mode.setCurrentIndex(self._erase_mode.findData("smart"))

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

        self._erase_scope = QComboBox()
        for label, mode in [("Mass erase", "mass"), ("Chip erase", "chip"), ("Address range", "range")]:
            self._erase_scope.addItem(label, mode)
        self._erase_start = QLineEdit()
        self._erase_start.setPlaceholderText("0x08000000")
        self._erase_end = QLineEdit()
        self._erase_end.setPlaceholderText("0x08001000")
        self._erase_start.setEnabled(False)
        self._erase_end.setEnabled(False)
        self._erase_scope.currentIndexChanged.connect(self._update_erase_fields)
        erase_row = QHBoxLayout()
        erase_row.addWidget(self._erase_scope)
        erase_row.addWidget(QLabel("Start:"))
        erase_row.addWidget(self._erase_start, 1)
        erase_row.addWidget(QLabel("End (exclusive):"))
        erase_row.addWidget(self._erase_end, 1)
        form.addRow(QLabel("Standalone Erase:"), erase_row)

        self._reset_type = QComboBox()
        for label, mode in [("Target default", "default"), ("System", "system"),
                            ("Hardware pin", "hardware"), ("Emulated", "emulated")]:
            self._reset_type.addItem(label, mode)
        form.addRow(QLabel("Standalone Reset:"), self._reset_type)

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
        self._compare_btn = QPushButton("Compare Flash")
        self._compare_btn.setFixedHeight(40)
        self._compare_btn.clicked.connect(self._open_compare)
        self._erase_flash_btn = QPushButton("Erase Flash")
        self._erase_flash_btn.setFixedHeight(40)
        self._erase_flash_btn.setToolTip("Erase Flash using the selected standalone erase mode")
        self._erase_flash_btn.clicked.connect(self._start_erase)
        self._reset_btn = QPushButton("Reset Target")
        self._reset_btn.setFixedHeight(40)
        self._reset_btn.clicked.connect(lambda: self._start_device_action("reset"))
        btn_row.addWidget(self._flash_btn, 1)
        btn_row.addWidget(self._dump_btn, 1)
        btn_row.addWidget(self._compare_btn, 1)
        btn_row.addWidget(self._erase_flash_btn, 1)
        btn_row.addWidget(self._reset_btn, 1)
        root.addLayout(btn_row)

        # ---- Log viewer ----
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self._log_viewer = LogViewer()
        log_layout.addWidget(self._log_viewer)
        root.addWidget(log_box, 1)

    # ------------------------------------------------------------------
    def _start_flash(self) -> None:
        if self._busy:
            return
        try:
            images = self._file_picker.firmware_images()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Invalid Firmware", str(error))
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
        self._log_viewer.append(f"Starting flash: {len(images)} image(s)")

        self._worker = FlashWorker(
            firmware_path=None,
            images=images,
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
        if self._busy:
            return
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
        self._busy = busy
        self._config_box.setEnabled(not busy)
        for btn in (self._flash_btn, self._dump_btn, self._compare_btn, self._erase_flash_btn, self._reset_btn):
            btn.setEnabled(not busy)

    def _open_compare(self) -> None:
        if self._busy:
            return
        try:
            images = self._file_picker.firmware_images()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Invalid Firmware", str(error))
            return
        target = self._target_selector.current_target()
        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(self, "No Target", "Select a concrete target or run Detect first.")
            return
        probe = self._probe_selector.selected_probe()
        dialog = CompareDialog(
            images, target, probe_uid=probe.uid if probe else None,
            clock_khz=self._selected_clock_khz(), parent=self,
        )
        self._set_busy(True)
        try:
            dialog.exec()
        finally:
            self._set_busy(False)
            dialog.deleteLater()

    def _update_erase_fields(self) -> None:
        enabled = self._erase_scope.currentData() == "range"
        self._erase_start.setEnabled(enabled)
        self._erase_end.setEnabled(enabled)

    def _open_pack_manager(self) -> None:
        dialog = PackManagerDialog(self)
        dialog.packs_changed.connect(self._refresh_pack_targets)
        dialog.exec()

    def _refresh_pack_targets(self) -> None:
        try:
            self._target_selector.refresh_targets()
        except Exception as error:
            self._log_viewer.append(f"Could not refresh pack targets: {error}")

    def _start_erase(self) -> None:
        if self._busy:
            return
        target = self._target_selector.current_target()
        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(
                self,
                "No Target",
                "Please select a target type or run Detect first.\n\n"
                "The generic 'cortex_m' target cannot erase flash.",
            )
            return

        mode = self._erase_scope.currentData()
        start = end = None
        description = "the ENTIRE flash"
        if mode == "range":
            try:
                start = parse_address(self._erase_start.text())
                end = parse_address(self._erase_end.text())
                if end <= start:
                    raise ValueError("End must be greater than start (exclusive end).")
            except ValueError as error:
                QMessageBox.warning(self, "Invalid Erase Range", str(error))
                return
            description = f"Flash range [0x{start:08X}, 0x{end:08X})"
        ret = QMessageBox.question(
            self,
            "Erase Flash",
            f"Erase {description} of '{target}' ({mode})?\n\n"
            "Data in the erased area will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        probe = self._probe_selector.selected_probe()
        self._set_busy(True)
        self._progress.setValue(0)
        self._log_viewer.append(f"Erasing {description} of '{target}' ...")

        self._erase_worker = EraseWorker(
            target=target,
            mode=mode,
            start_address=start,
            end_address=end,
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
        if self._busy:
            return
        probe = self._probe_selector.selected_probe()
        self._set_busy(True)
        self._detect_btn.setText("Detecting…")
        self._log_viewer.append("Detecting target chip …")
        self._detect_worker = DetectWorker(
            probe_uid=probe.uid if probe else None,
            clock_khz=self._selected_clock_khz(),
        )
        self._detect_worker.result.connect(self._on_detect_result)
        self._detect_worker.start()

    def _on_detect_result(self, target_name: object, msg: str) -> None:
        self._set_busy(False)
        self._detect_btn.setText("Detect")
        self._log_viewer.append(msg)
        if target_name:
            self._target_selector.set_target(str(target_name))

    def _build_info_panel(self) -> None:
        self._info_dock = QDockWidget("Chip Information", self)
        self._info_dock.setObjectName("chip_information")
        self._info_dock.setMinimumWidth(380)
        content = QWidget()
        layout = QVBoxLayout(content)
        self._info_summary = QLabel("Not connected")
        self._info_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._info_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._info_summary.setWordWrap(True)
        layout.addWidget(self._info_summary)
        self._region_table = QTableWidget(0, 5)
        self._region_table.setHorizontalHeaderLabels(["Region", "Type", "Start", "End", "Bytes"])
        self._region_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._region_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._region_table)
        self._info_dock.setWidget(content)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._info_dock)
        self._info_dock.hide()

    def _start_device_action(self, action: str) -> None:
        if self._busy:
            return
        target = self._target_selector.current_target()
        if not target or target.lower() == "cortex_m":
            QMessageBox.warning(self, "No Target", "Select a concrete target or run Detect first.")
            return
        probe = self._probe_selector.selected_probe()
        if action == "info":
            self._info_summary.setText(f"Connecting to {target} ...")
            self._region_table.setRowCount(0)
            self._info_dock.show()
        self._set_busy(True)
        self._progress.setValue(0)
        self._device_worker = DeviceWorker(
            target, action, probe_uid=probe.uid if probe else None,
            clock_khz=self._selected_clock_khz(), reset_type=self._reset_type.currentData(),
        )
        self._device_worker.log.connect(self._log_viewer.append)
        self._device_worker.result.connect(self._show_device_info)
        self._device_worker.finished.connect(self._on_device_finished)
        self._device_worker.start()

    def _show_device_info(self, info: dict) -> None:
        flash_bytes = sum(region["length"] for region in info["regions"] if region["type"] == "FLASH")
        ram_bytes = sum(region["length"] for region in info["regions"] if region["type"] == "RAM")
        self._info_summary.setText(
            f"Target: {info['target']}\nPart: {info['part']}\nVendor: {info['vendor']}\n"
            f"Core: {info['core']}\nBoard: {info['board']}\nProbe UID: {info['probe_uid']}\n"
            f"Mapped Flash: {flash_bytes:,} bytes\nMapped RAM: {ram_bytes:,} bytes"
        )
        self._region_table.setRowCount(len(info["regions"]))
        for row, region in enumerate(info["regions"]):
            values = [region["name"], region["type"], f"0x{region['start']:08X}",
                      f"0x{region['end']:08X}", f"{region['length']:,}"]
            for column, value in enumerate(values):
                self._region_table.setItem(row, column, QTableWidgetItem(value))
        self._info_dock.show()

    def _on_device_finished(self, success: bool) -> None:
        self._set_busy(False)
        self._progress.setValue(100 if success else 0)
        if not success:
            self._log_viewer.append("Device operation failed. Check the log above.")
            if self._device_worker is not None and self._device_worker._action == "info":
                self._info_summary.setText("Could not read chip information. Check the log.")

    def closeEvent(self, event: QCloseEvent) -> None:
        workers = (self._worker, self._dump_worker, self._erase_worker,
                   self._detect_worker, self._device_worker)
        if any(worker is not None and worker.isRunning() for worker in workers):
            QMessageBox.warning(self, "Operation in Progress", "Wait for the device operation to finish.")
            event.ignore()
        else:
            super().closeEvent(event)
