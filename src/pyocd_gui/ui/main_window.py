"""Main application window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

        self._erase_check = QCheckBox("Erase before flash")
        self._erase_check.setChecked(True)
        self._verify_check = QCheckBox("Verify after flash")
        self._verify_check.setChecked(True)

        self._reset_check = QCheckBox("Reset after flash")
        self._reset_check.setChecked(True)

        opts_row = QHBoxLayout()
        opts_row.addWidget(self._erase_check)
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

        root.addWidget(config_box)

        # ---- Progress ----
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        root.addWidget(self._progress)

        # ---- Flash button ----
        self._flash_btn = QPushButton("Flash")
        self._flash_btn.setFixedHeight(40)
        self._flash_btn.clicked.connect(self._start_flash)
        root.addWidget(self._flash_btn)

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

        self._flash_btn.setEnabled(False)
        self._progress.setValue(0)
        self._log_viewer.append(f"Starting flash: {firmware.name}")

        self._worker = FlashWorker(
            firmware_path=firmware,
            target=target,
            probe_uid=probe.uid if probe else None,
            erase=self._erase_check.isChecked(),
            verify=self._verify_check.isChecked(),
            connect_mode=self._connect_mode.currentData(),
            reset_after=self._reset_check.isChecked(),
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.log.connect(self._log_viewer.append)
        self._worker.finished.connect(self._on_flash_finished)
        self._worker.start()

    def _on_flash_finished(self, success: bool) -> None:
        self._flash_btn.setEnabled(True)
        if success:
            self._log_viewer.append("Done.")
        else:
            self._log_viewer.append("Flash failed. Check the log above.")

    # ------------------------------------------------------------------
    def _start_detect(self) -> None:
        probe = self._probe_selector.selected_probe()
        self._detect_btn.setEnabled(False)
        self._detect_btn.setText("Detecting…")
        self._log_viewer.append("Detecting target chip …")
        self._detect_worker = DetectWorker(
            probe_uid=probe.uid if probe else None
        )
        self._detect_worker.result.connect(self._on_detect_result)
        self._detect_worker.start()

    def _on_detect_result(self, target_name: object, msg: str) -> None:
        self._detect_btn.setEnabled(True)
        self._detect_btn.setText("Detect")
        self._log_viewer.append(msg)
        if target_name:
            self._target_selector.set_target(str(target_name))
