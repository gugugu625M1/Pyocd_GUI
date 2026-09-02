"""Background worker thread for erasing the target's entire flash."""

from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.flash.eraser import FlashEraser


class EraseWorker(QThread):
    """Mass-erase all flash on the target.

    Uses ``FlashEraser`` in MASS mode, which falls back to a chip erase on
    targets that do not implement a dedicated mass erase (and can additionally
    unlock security / clear config regions on those that do). Requires a
    concrete target type (built-in or CMSIS-Pack) since erasing needs the
    target's flash algorithm.

    Signals
    -------
    log(str)        text line to show in the log viewer
    finished(bool)  True on success, False on failure
    """

    log = Signal(str)
    finished = Signal(bool)

    def __init__(
        self,
        target: str,
        probe_uid: Optional[str] = None,
        connect_mode: str = "under-reset",
        reset_after: bool = True,
        clock_khz: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._target = target
        self._probe_uid = probe_uid
        self._connect_mode = connect_mode
        self._reset_after = reset_after
        self._clock_khz = clock_khz

    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self._target or self._target.strip().lower() == "cortex_m":
            self.log.emit(
                "No target type selected — the generic 'cortex_m' target has no "
                "flash algorithm. Pick a target or run Detect first."
            )
            self.finished.emit(False)
            return

        try:
            options = {"smart_flash": False}
            if self._clock_khz:
                options["frequency"] = self._clock_khz * 1000

            self.log.emit(f"Connecting to target '{self._target}' (mode: {self._connect_mode}) …")
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid,
                target_override=self._target,
                auto_unlock=True,
                connect_mode=self._connect_mode,
                options=options,
            ) as session:
                self.log.emit("Connected. Halting target …")
                session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)

                self.log.emit("Mass-erasing entire flash …")
                FlashEraser(session, FlashEraser.Mode.MASS).erase()
                self.log.emit("Erase complete.")

                if self._reset_after:
                    session.target.reset(reset_type=Target.ResetType.SYSRESETREQ)
                    self.log.emit("Target reset — program is running.")

                self.finished.emit(True)

        except Exception as exc:
            self.log.emit(f"Error: {exc}")
            self.finished.emit(False)
