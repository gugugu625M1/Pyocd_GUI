"""Read-only, byte-for-byte comparison of firmware images with target Flash."""

from PySide6.QtCore import QThread, Signal
from elftools.elf.elffile import ELFFile
from intelhex import IntelHex
from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target

from pyocd_gui.core.firmware import FirmwareImage, resolve_bin_addresses, validate_images


def _segments(image: FirmwareImage) -> list[tuple[int, bytes]]:
    suffix = image.path.suffix.lower()
    if suffix == ".bin":
        return [(image.base_address, image.path.read_bytes())]
    if suffix == ".hex":
        contents = IntelHex(str(image.path))
        return [
            (start, bytes(contents.tobinarray(start=start, size=end - start)))
            for start, end in contents.segments()
        ]
    with image.path.open("rb") as stream:
        elf = ELFFile(stream)
        return [
            (segment["p_paddr"], bytes(segment.data()))
            for segment in elf.iter_segments()
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]
        ]


class CompareWorker(QThread):
    progress = Signal(int)
    log = Signal(str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal(bool)

    _CHUNK_SIZE = 4096
    _DIFFERENCE_LIMIT = 256

    def __init__(
        self, images: list[FirmwareImage], target: str, probe_uid: str | None = None,
        clock_khz: int | None = None, freeze_stm32f1_watchdogs: bool = False,
    ) -> None:
        super().__init__()
        self._images = tuple(images)
        self._target = target
        self._probe_uid = probe_uid
        self._clock_khz = clock_khz
        self._freeze_watchdogs = freeze_stm32f1_watchdogs

    def run(self) -> None:
        try:
            if not self._target or self._target.lower() == "cortex_m":
                raise ValueError("Select a concrete target before comparing Flash.")
            if self._freeze_watchdogs and not self._target.lower().startswith("stm32f1"):
                raise ValueError("Watchdog freeze is only supported for STM32F1 targets.")
            validate_images(self._images)
            options = {"resume_on_disconnect": False}
            if self._clock_khz:
                options["frequency"] = self._clock_khz * 1000
            self.progress.emit(0)
            self.log.emit(f"Comparing Flash of '{self._target}' (attach, no erase/program/reset).")
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid, target_override=self._target,
                auto_unlock=False, connect_mode="attach", options=options,
            ) as session:
                target = session.target
                images = resolve_bin_addresses(self._images, target.memory_map)
                segments = []
                for image in images:
                    for start, data in _segments(image):
                        region = target.memory_map.get_region_for_address(start)
                        if not data or region is None or not region.is_flash or start + len(data) > region.end + 1:
                            raise ValueError(f"Comparison data must fit in Flash: {image.path.name}")
                        segments.append((start, data, image.path.name))
                segments.sort(key=lambda item: item[0])
                for previous, current in zip(segments, segments[1:]):
                    if previous[0] + len(previous[1]) > current[0]:
                        raise ValueError("Comparison images overlap.")
                total = sum(len(data) for start, data, name in segments)
                if not total:
                    raise ValueError("No Flash data to compare.")
                was_running = target.get_state() == Target.State.RUNNING
                original_debug_register = None
                try:
                    target.halt()
                    if self._freeze_watchdogs:
                        original_debug_register = target.read32(0xE0042004)
                        target.write32(0xE0042004, original_debug_register | 0x300)
                    report = self._compare(target, segments, total)
                finally:
                    try:
                        if original_debug_register is not None:
                            target.write32(0xE0042004, original_debug_register)
                    finally:
                        if was_running:
                            target.resume()
            self.result.emit(report)
            self.log.emit(
                f"Compared {report['compared_bytes']:,} bytes: {report['mismatch_count']:,} differing bytes."
            )
            self.finished.emit(True)
        except Exception as error:
            self.error.emit(str(error))
            self.log.emit(f"Comparison stopped: {error}")
            self.finished.emit(False)

    def _compare(self, target, segments: list[tuple[int, bytes, str]], total: int) -> dict:
        compared = 0
        mismatches = 0
        differences = []
        first_mismatch = None
        for start, data, name in segments:
            self.log.emit(f"Comparing {name}: {len(data):,} bytes at 0x{start:08X}")
            for offset in range(0, len(data), self._CHUNK_SIZE):
                if self.isInterruptionRequested():
                    raise InterruptedError("Comparison cancelled.")
                if target.get_state() != Target.State.HALTED:
                    raise RuntimeError("Target resumed or reset. Freeze active watchdogs before retrying.")
                expected = data[offset:offset + self._CHUNK_SIZE]
                address = start + offset
                actual = bytes(target.read_memory_block8(address, len(expected)))
                if target.get_state() != Target.State.HALTED:
                    raise RuntimeError("Target resumed or reset during comparison; result discarded.")
                if len(actual) != len(expected):
                    raise RuntimeError(f"Short Flash read at 0x{address:08X}.")
                if actual != expected:
                    for index, (file_byte, flash_byte) in enumerate(zip(expected, actual)):
                        if file_byte != flash_byte:
                            mismatches += 1
                            if first_mismatch is None:
                                first_mismatch = address + index
                            if len(differences) < self._DIFFERENCE_LIMIT:
                                differences.append({
                                    "address": address + index, "image": name,
                                    "file_byte": file_byte, "flash_byte": flash_byte,
                                })
                compared += len(expected)
                self.progress.emit(int(compared * 100 / total))
        return {
            "matched": mismatches == 0, "compared_bytes": compared,
            "mismatch_count": mismatches, "first_mismatch": first_mismatch,
            "differences": differences, "difference_limit": self._DIFFERENCE_LIMIT,
        }