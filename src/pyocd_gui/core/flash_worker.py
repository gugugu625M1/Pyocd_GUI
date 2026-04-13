"""Background worker thread for flash operations."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.flash.file_programmer import FileProgrammer


class FlashWorker(QThread):
    """Run pyOCD flash programming in a background thread.

    Signals
    -------
    progress(int)   0–100 percentage
    log(str)        text line to show in the log viewer
    finished(bool)  True on success, False on failure
    """

    progress = Signal(int)
    log = Signal(str)
    finished = Signal(bool)

    def __init__(
        self,
        firmware_path: Path,
        target: str,
        probe_uid: Optional[str] = None,
        erase: bool = True,
        verify: bool = True,
        connect_mode: str = "under-reset",
        reset_after: bool = True,
    ) -> None:
        super().__init__()
        self._firmware_path = firmware_path
        self._target = target
        self._probe_uid = probe_uid
        self._erase = erase
        self._verify = verify
        self._connect_mode = connect_mode
        self._reset_after = reset_after

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            self.log.emit(f"Connecting to target '{self._target}' (mode: {self._connect_mode}) …")
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid,
                target_override=self._target,
                auto_unlock=True,
                connect_mode=self._connect_mode,
                options={"smart_flash": False},
            ) as session:
                self.log.emit("Connected. Halting target …")
                session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)
                self.log.emit("Target halted. Starting flash operation …")

                programmer = FileProgrammer(
                    session,
                    chip_erase=("chip" if self._erase else "sector"),
                    trust_crc=not self._verify,
                    progress=self._on_progress,
                )
                programmer.program(str(self._firmware_path))

                if self._reset_after:
                    session.target.reset(reset_type=Target.ResetType.SYSRESETREQ)
                    self.log.emit("Target reset — program is running.")
                else:
                    self.log.emit("Target left halted.")

                self.log.emit("Flash complete.")
                self.progress.emit(100)
                self.finished.emit(True)

        except Exception as exc:
            self.log.emit(f"Error: {exc}")
            self.finished.emit(False)

    # ------------------------------------------------------------------
    def _on_progress(self, amount: float) -> None:
        self.progress.emit(int(amount * 100))
