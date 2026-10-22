"""Background worker thread for flash operations."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.flash.file_programmer import FileProgrammer

from pyocd_gui.core.firmware import FirmwareImage, resolve_bin_addresses, validate_images


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
        firmware_path: Path | None,
        target: str,
        probe_uid: Optional[str] = None,
        erase_mode: str = "chip",
        verify: bool = True,
        connect_mode: str = "under-reset",
        reset_after: bool = True,
        clock_khz: Optional[int] = None,
        images: list[FirmwareImage] | None = None,
    ) -> None:
        super().__init__()
        self._images = tuple(images) if images is not None else (
            (FirmwareImage(firmware_path),) if firmware_path is not None else ()
        )
        self._target = target
        self._probe_uid = probe_uid
        self._erase_mode = erase_mode
        self._verify = verify
        self._connect_mode = connect_mode
        self._reset_after = reset_after
        self._clock_khz = clock_khz

    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self._target or self._target.strip().lower() == "cortex_m":
            self.log.emit(
                "No target type selected — the generic 'cortex_m' target cannot "
                "program flash. Pick a target or run Detect first."
            )
            self.finished.emit(False)
            return

        try:
            validate_images(self._images)
            options = {"smart_flash": False, "resume_on_disconnect": False}
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
                images = resolve_bin_addresses(self._images, session.target.memory_map)
                self.log.emit("Connected. Halting target …")
                session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)
                self.log.emit("Target halted. Starting flash operation …")

                programmer = FileProgrammer(
                    session,
                    # "smart" → sector erase + smart_flash: pages whose contents
                    # are unchanged are neither erased nor programmed, but pages
                    # that changed are still erased. NOT a no-erase mode — pyOCD's
                    # public API has no true "skip erase before programming".
                    chip_erase=("sector" if self._erase_mode == "smart" else self._erase_mode),
                    smart_flash=(True if self._erase_mode == "smart" else None),
                    trust_crc=not self._verify,
                    keep_unwritten=True,
                    progress=self._on_progress,
                )
                for image in images:
                    self.log.emit(
                        f"Buffering {image.path.name}"
                        + (f" at 0x{image.base_address:08X}" if image.base_address is not None else "")
                    )
                    arguments = {"base_address": image.base_address} if image.base_address is not None else {}
                    programmer.add_file(
                        str(image.path), file_format=image.path.suffix.lower()[1:], **arguments
                    )
                programmer.commit()

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
