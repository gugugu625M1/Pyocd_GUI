"""Background worker for whole-device and strictly aligned range erases."""

from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.flash.eraser import FlashEraser


def validate_erase_range(memory_map, start: int, end: int) -> None:
    if not 0 <= start < end <= 0x100000000:
        raise ValueError("Erase range must have a valid start and an exclusive end greater than start.")
    address = start
    while address < end:
        region = memory_map.get_region_for_address(address)
        if region is None or not region.is_flash or region.flash is None:
            raise ValueError(f"Erase range is not entirely Flash at 0x{address:08X}.")
        sector = region.flash.get_sector_info(address)
        if sector is None or sector.size <= 0 or sector.base_addr != address:
            raise ValueError(f"Erase start/boundary 0x{address:08X} is not sector-aligned.")
        following = address + sector.size
        if following > end or following > region.end + 1:
            raise ValueError("Erase end must be exactly on a sector boundary.")
        address = following


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
        mode: str = "mass",
        start_address: int | None = None,
        end_address: int | None = None,
    ) -> None:
        super().__init__()
        self._target = target
        self._probe_uid = probe_uid
        self._connect_mode = connect_mode
        self._reset_after = reset_after
        self._clock_khz = clock_khz
        self._mode = mode
        self._start_address = start_address
        self._end_address = end_address

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
            if self._mode not in ("mass", "chip", "range"):
                raise ValueError("Unknown erase mode.")
            if self._mode == "range" and (
                self._start_address is None or self._end_address is None
                or not 0 <= self._start_address < self._end_address <= 0x100000000
            ):
                raise ValueError("Provide a valid erase start and exclusive end address.")
            options = {"smart_flash": False, "resume_on_disconnect": False}
            if self._clock_khz:
                options["frequency"] = self._clock_khz * 1000

            self.log.emit(f"Connecting to target '{self._target}' (mode: {self._connect_mode}) …")
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid,
                target_override=self._target,
                auto_unlock=(self._mode == "mass"),
                connect_mode=self._connect_mode,
                options=options,
            ) as session:
                if self._mode == "range":
                    validate_erase_range(session.target.memory_map, self._start_address, self._end_address)
                self.log.emit("Connected. Halting target …")
                session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)

                mode = {
                    "mass": FlashEraser.Mode.MASS,
                    "chip": FlashEraser.Mode.CHIP,
                    "range": FlashEraser.Mode.SECTOR,
                }[self._mode]
                self.log.emit(f"Erasing flash ({self._mode}) ...")
                eraser = FlashEraser(session, mode)
                if self._mode == "range":
                    eraser.erase([(self._start_address, self._end_address)])
                else:
                    eraser.erase()
                self.log.emit("Erase complete.")

                if self._reset_after:
                    session.target.reset(reset_type=Target.ResetType.SYSRESETREQ)
                    self.log.emit("Target reset — program is running.")

                self.finished.emit(True)

        except Exception as exc:
            self.log.emit(f"Error: {exc}")
            self.finished.emit(False)
