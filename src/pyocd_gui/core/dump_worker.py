"""Background worker thread for reading (dumping) the target's flash contents."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.memory_map import MemoryType
from pyocd.core.target import Target


class DumpWorker(QThread):
    """Read the entire flash region of the target and save it to a file.

    Requires a concrete target type (built-in or CMSIS-Pack) so that the flash
    region can be located in the target's memory map — the generic ``cortex_m``
    target has no memory map.

    Signals
    -------
    progress(int)   0–100 percentage
    log(str)        text line to show in the log viewer
    finished(bool)  True on success, False on failure
    """

    progress = Signal(int)
    log = Signal(str)
    finished = Signal(bool)

    _CHUNK_SIZE = 64 * 1024  # read 64 KB at a time to keep progress updating

    def __init__(
        self,
        out_path: Path,
        target: str,
        probe_uid: Optional[str] = None,
        connect_mode: str = "under-reset",
        reset_after: bool = True,
        clock_khz: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._out_path = out_path
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
                "memory map, so the flash region is unknown. "
                "Pick a target or run Detect first."
            )
            self.finished.emit(False)
            return

        try:
            options = {"smart_flash": False, "resume_on_disconnect": False}
            if self._clock_khz:
                options["frequency"] = self._clock_khz * 1000

            self.log.emit(f"Connecting to target '{self._target}' (mode: {self._connect_mode}) …")
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid,
                target_override=self._target,
                auto_unlock=False,
                connect_mode=self._connect_mode,
                options=options,
            ) as session:
                self.log.emit("Connected. Halting target …")
                session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)

                region = session.target.memory_map.get_first_matching_region(
                    type=MemoryType.FLASH
                )
                if region is None:
                    self.log.emit("Target memory map has no flash region.")
                    self.finished.emit(False)
                    return

                start, length = region.start, region.length
                self.log.emit(
                    f"Reading flash: {length // 1024} KB at 0x{start:08X} …"
                )

                data = bytearray()
                chunk_count = (length + self._CHUNK_SIZE - 1) // self._CHUNK_SIZE
                if session.target.get_state() != Target.State.HALTED:
                    raise RuntimeError("Target did not remain halted before Flash read.")
                for index in range(chunk_count):
                    offset = index * self._CHUNK_SIZE
                    size = min(self._CHUNK_SIZE, length - offset)
                    block = session.target.read_memory_block8(start + offset, size)
                    if session.target.get_state() != Target.State.HALTED:
                        raise RuntimeError(
                            "Target resumed or reset during Flash read; no dump was saved. "
                            "If a watchdog is active, configure debug freeze before retrying."
                        )
                    data += bytes(block)
                    self.progress.emit(int((index + 1) * 100 / chunk_count))

                self._write(self._out_path, bytes(data), start)
                self.log.emit(
                    f"Flash dumped to {self._out_path} ({len(data)} bytes, base 0x{start:08X})."
                )

                if self._reset_after:
                    session.target.reset(reset_type=Target.ResetType.SYSRESETREQ)
                    self.log.emit("Target reset — program is running.")

                self.finished.emit(True)

        except Exception as exc:
            self.log.emit(f"Error: {exc}")
            self.finished.emit(False)

    # ------------------------------------------------------------------
    @staticmethod
    def _write(path: Path, data: bytes, base_addr: int) -> None:
        """Write the dump; ``.hex`` saves as Intel HEX (with correct base address),
        anything else as a raw binary file."""
        if path.suffix.lower() == ".hex":
            from intelhex import IntelHex  # provided by pyocd's dependencies

            ihex = IntelHex()
            ihex.frombytes(data, offset=base_addr)
            ihex.write_hex_file(str(path))
        else:
            path.write_bytes(data)
