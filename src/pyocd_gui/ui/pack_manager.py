"""Manage shared CMSIS-DFP packs through the pyOCD CLI."""

import codecs
import re
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFont, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class PackManagerDialog(QDialog):
    packs_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DFP Pack Manager")
        self.resize(820, 540)
        self._operation = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._ansi_pending = ""
        self._ansi_format = QTextCharFormat()
        self._ansi_dim = False
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(environment)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        layout = QVBoxLayout(self)
        query_row = QHBoxLayout()
        query_row.addWidget(QLabel("Chip:"))
        self._query = QLineEdit()
        self._query.setPlaceholderText("STM32G0B1RETx")
        self._query.setClearButtonEnabled(True)
        self._query.returnPressed.connect(self._find)
        query_row.addWidget(self._query, 1)
        self._find_btn = QPushButton("Find DFP")
        self._find_btn.clicked.connect(self._find)
        query_row.addWidget(self._find_btn)
        self._install_btn = QPushButton("Install")
        self._install_btn.clicked.connect(self._install)
        query_row.addWidget(self._install_btn)
        layout.addLayout(query_row)

        toolbar = QHBoxLayout()
        self._installed_btn = QPushButton("Installed Packs")
        self._installed_btn.clicked.connect(lambda: self._run_command("show"))
        toolbar.addWidget(self._installed_btn)
        self._update_btn = QPushButton("Update Index")
        self._update_btn.clicked.connect(lambda: self._run_command("update"))
        toolbar.addWidget(self._update_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._output.setMaximumBlockCount(20000)
        layout.addWidget(self._output, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)
        self._status = QLabel("Ready")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        footer = QHBoxLayout()
        footer.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        footer.addWidget(self._close_btn)
        layout.addLayout(footer)
        self._controls = (
            self._query, self._find_btn, self._install_btn,
            self._installed_btn, self._update_btn, self._close_btn,
        )
        self._run_command("show")

    def _find(self) -> None:
        self._run_command("find")

    def _install(self) -> None:
        pattern = self._query.text().strip()
        if not pattern or self._operation:
            return
        answer = QMessageBox.question(
            self,
            "Install DFP",
            f"Download and install all packs matching chip pattern '{pattern}'?\n\n"
            "A broad pattern may download multiple large packs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_command("install")

    def _run_command(self, operation: str) -> None:
        if self._operation:
            return
        flags = {"show": "-s", "find": "-f", "install": "-i", "update": "-u"}
        arguments = ["-u", "-m", "pyocd", "pack", "--color", "always", flags[operation]]
        if operation in ("find", "install"):
            pattern = self._query.text().strip()
            if not pattern:
                self._status.setText("Enter a chip part number first.")
                self._query.setFocus()
                return
            arguments.append(pattern)
        self._operation = operation
        self._decoder.reset()
        self._ansi_pending = ""
        self._ansi_format = QTextCharFormat()
        self._ansi_dim = False
        self._output.clear()
        self._append_output("pyocd " + " ".join(arguments[3:]) + "\n\n")
        self._status.setText(f"Running: {operation}")
        self._set_busy(True)
        self._process.setProgram(sys.executable)
        self._process.setArguments(arguments)
        self._process.start()

    def _set_busy(self, busy: bool) -> None:
        for control in self._controls:
            control.setEnabled(not busy)
        self._progress.setRange(0, 0 if busy else 1)
        if not busy:
            self._progress.setValue(0)

    def _read_output(self) -> None:
        data = bytes(self._process.readAllStandardOutput())
        self._append_output(self._decoder.decode(data))

    def _append_output(self, text: str) -> None:
        text = self._ansi_pending + text
        self._ansi_pending = ""
        incomplete = re.search(r"\x1b(?:\[[0-?]*[ -/]*)?$", text)
        if incomplete:
            self._ansi_pending = text[incomplete.start():]
            text = text[:incomplete.start()]
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for part in re.split(r"(\x1b\[[0-?]*[ -/]*[@-~])", text):
            if part.startswith("\x1b["):
                if part.endswith("m"):
                    self._apply_sgr(part[2:-1])
                continue
            char_format = QTextCharFormat(self._ansi_format)
            if self._ansi_dim:
                color = (
                    char_format.foreground().color()
                    if char_format.hasProperty(QTextCharFormat.Property.ForegroundBrush)
                    else self._output.palette().text().color()
                )
                color.setAlphaF(0.55)
                char_format.setForeground(color)
            cursor.insertText(part, char_format)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _apply_sgr(self, parameters: str) -> None:
        colors = (
            "#000000", "#aa0000", "#00aa00", "#aa5500",
            "#0000aa", "#aa00aa", "#00aaaa", "#aaaaaa",
            "#555555", "#ff5555", "#55ff55", "#ffff55",
            "#5555ff", "#ff55ff", "#55ffff", "#ffffff",
        )
        for parameter in parameters.split(";"):
            if parameter and not parameter.isdigit():
                return
            code = int(parameter or "0")
            if code == 0:
                self._ansi_format = QTextCharFormat()
                self._ansi_dim = False
            elif code == 1:
                self._ansi_format.setFontWeight(QFont.Weight.Bold)
            elif code == 2:
                self._ansi_dim = True
            elif code == 22:
                self._ansi_format.setFontWeight(QFont.Weight.Normal)
                self._ansi_dim = False
            elif code == 39:
                self._ansi_format.clearForeground()
            elif code == 49:
                self._ansi_format.clearBackground()
            elif 30 <= code <= 37 or 90 <= code <= 97:
                index = code - 30 if code < 90 else code - 90 + 8
                self._ansi_format.setForeground(QColor(colors[index]))
            elif 40 <= code <= 47 or 100 <= code <= 107:
                index = code - 40 if code < 100 else code - 100 + 8
                self._ansi_format.setBackground(QColor(colors[index]))

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._append_output(self._decoder.decode(b"", final=True))
        operation = self._operation
        self._operation = ""
        self._set_busy(False)
        success = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        if success:
            self._status.setText(f"Completed: {operation}")
        else:
            self._status.setText(f"Failed: {operation} (exit {exit_code}). Check output above.")
        if operation == "install":
            self.packs_changed.emit()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self._append_output(f"\n{self._process.errorString()}\n")
        if error == QProcess.ProcessError.FailedToStart:
            self._operation = ""
            self._set_busy(False)
            self._status.setText("Could not start pyOCD. Check the Python environment.")

    def done(self, result: int) -> None:
        if not self._operation:
            super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._operation:
            event.ignore()
        else:
            super().closeEvent(event)