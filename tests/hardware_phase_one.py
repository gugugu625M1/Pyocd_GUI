"""Opt-in hardware acceptance test; never discovered by unittest."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from intelhex import IntelHex
from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target

from pyocd_gui.core.device_worker import DeviceWorker
from pyocd_gui.core.dump_worker import DumpWorker
from pyocd_gui.core.erase_worker import EraseWorker, validate_erase_range
from pyocd_gui.core.firmware import FirmwareImage
from pyocd_gui.core.flash_worker import FlashWorker


def read_flash(session, region) -> bytes:
    running = session.target.get_state() == Target.State.RUNNING
    session.target.halt()
    try:
        data = bytearray()
        for address in range(region.start, region.end + 1, 4096):
            data.extend(session.target.read_memory_block8(address, min(4096, region.end + 1 - address)))
        return bytes(data)
    finally:
        if running:
            session.target.resume()


def run_erase_read_test(args, output: Path, connection: dict, backup: bytes, start: int, report: dict) -> None:
    def execute(worker) -> None:
        outcomes = []
        worker.log.connect(print)
        worker.finished.connect(outcomes.append)
        worker.run()
        if outcomes != [True]:
            raise RuntimeError(f"{type(worker).__name__} failed. Full backup: {output / 'flash-before.bin'}")

    def dump(name: str) -> bytes:
        path = output / name
        execute(DumpWorker(
            path, args.target, probe_uid=args.probe, connect_mode="attach",
            reset_after=False, clock_khz=1000,
        ))
        if path.suffix == ".hex":
            actual = IntelHex(str(path))
            if actual.segments() != [(start, start + len(backup))]:
                raise AssertionError("HEX dump has incorrect base address or length.")
            return bytes(actual.tobinarray())
        return path.read_bytes()

    if dump("baseline.bin") != backup or dump("baseline.hex") != backup:
        raise AssertionError("GUI dump disagrees with independent Flash backup.")
    with ConnectHelper.session_with_chosen_probe(**connection) as session:
        region = session.target.memory_map.get_region_for_address(start)
        sector = region.flash.get_sector_info(region.end)
        erase_start = sector.base_addr
        erase_end = erase_start + sector.size
        if erase_start <= start or erase_end != start + len(backup):
            raise ValueError("Test requires a non-boot sector ending at the end of Flash.")
        validate_erase_range(session.target.memory_map, erase_start, erase_end)
        if session.target.get_state() != Target.State.HALTED:
            raise AssertionError("Target resumed despite reset_after=False.")

    pattern = bytes((offset * 37 + 0x5A) & 0xFF for offset in range(sector.size))
    pattern_path = output / "sector-pattern.bin"
    pattern_path.write_bytes(pattern)
    report.update(
        erase_read_test=True, erase_start=f"0x{erase_start:08X}",
        erase_end_exclusive=f"0x{erase_end:08X}", erase_bytes=sector.size,
        restored=False, erase_read_passed=False,
    )
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Testing range [0x{erase_start:08X}, 0x{erase_end:08X}), {sector.size} bytes", flush=True)
    try:
        execute(FlashWorker(
            None, args.target, probe_uid=args.probe, erase_mode="sector", verify=True,
            reset_after=False, clock_khz=1000, images=[FirmwareImage(pattern_path, erase_start)],
        ))
        expected_pattern = bytearray(backup)
        expected_pattern[erase_start - start:erase_end - start] = pattern
        if dump("pattern-written.bin") != expected_pattern:
            raise AssertionError("Test pattern or outside-range data does not match.")
        execute(EraseWorker(
            args.target, probe_uid=args.probe, mode="range", start_address=erase_start,
            end_address=erase_end, reset_after=False, clock_khz=1000,
        ))
        expected_erased = bytearray(backup)
        expected_erased[erase_start - start:erase_end - start] = b"\xFF" * sector.size
        if dump("range-erased.bin") != expected_erased:
            raise AssertionError("Range is not all 0xFF, or bytes outside range changed.")
        if dump("range-erased.hex") != expected_erased:
            raise AssertionError("HEX and BIN do not contain the same erased Flash data.")
        report.update(erase_read_passed=True, bin_verified=True, hex_verified=True,
                      outside_range_preserved=True)
    finally:
        print("Restoring full Flash backup before leaving the test ...", flush=True)
        try:
            execute(FlashWorker(
                output / "flash-before.bin", args.target, probe_uid=args.probe, erase_mode="sector",
                verify=True, reset_after=False, clock_khz=1000,
            ))
            restored = dump("restored.bin")
            if restored != backup:
                raise AssertionError("Restore verification failed; target left halted.")
            report.update(restored=True, restored_sha256=hashlib.sha256(restored).hexdigest())
            execute(DeviceWorker(args.target, "reset", probe_uid=args.probe, clock_khz=1000))
            report["reset_verified"] = True
        finally:
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS: range erase, BIN/HEX reads, outside-range preservation, full restore and reset.")
    print(f"Artifacts: {output}")


def run(args) -> None:
    if args.erase_read_test and not args.write:
        raise ValueError("--erase-read-test requires explicit --write authorization.")
    firmware = Path(args.firmware)
    if firmware.suffix.lower() != ".bin":
        raise ValueError("Hardware split test requires a BIN file.")
    original = firmware.read_bytes()
    output = Path(tempfile.mkdtemp(prefix="pyocd-gui-stage1-"))
    print(f"Artifacts and full Flash backup: {output}", flush=True)
    results = []
    information = DeviceWorker(args.target, "info", probe_uid=args.probe, clock_khz=1000)
    information.log.connect(print)
    information.result.connect(results.append)
    information.run()
    if not results:
        raise RuntimeError("Device information could not be read.")
    print(json.dumps(results[0], indent=2), flush=True)

    connection = dict(
        unique_id=args.probe, target_override=args.target, auto_unlock=False,
        connect_mode="attach", options={"frequency": 1000000, "resume_on_disconnect": False},
    )
    with ConnectHelper.session_with_chosen_probe(**connection) as session:
        regions = [region for region in session.target.memory_map if region.is_flash]
        if len(regions) != 1:
            raise ValueError("This acceptance test requires exactly one Flash region.")
        region = regions[0]
        if not 0 < len(original) <= region.length:
            raise ValueError("Firmware must fit in the target Flash region.")
        sector = region.flash.get_sector_info(region.start)
        split = len(original) // 2 // sector.size * sector.size
        if not 0 < split < len(original):
            raise ValueError("Firmware is too small to split on a sector boundary.")
        validate_erase_range(session.target.memory_map, region.start, region.start + split)
        backup = read_flash(session, region)
        start = region.start
    backup_path = output / "flash-before.bin"
    backup_path.write_bytes(backup)
    first = output / "part1.bin"
    second = output / "part2.bin"
    first.write_bytes(original[:split])
    second.write_bytes(original[split:])
    if first.read_bytes() + second.read_bytes() != original:
        raise AssertionError("Split files do not reproduce the input firmware.")
    report = {
        "source": str(firmware), "target": args.target, "probe": args.probe,
        "firmware_bytes": len(original), "flash_bytes": len(backup),
        "firmware_sha256": hashlib.sha256(original).hexdigest(),
        "backup_sha256": hashlib.sha256(backup).hexdigest(),
        "images": [
            {"file": str(first), "address": f"0x{start:08X}", "bytes": split},
            {"file": str(second), "address": f"0x{start + split:08X}", "bytes": len(original) - split},
        ],
        "written": False,
        "watchdogs_frozen": args.freeze_stm32f1_watchdogs,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if args.erase_read_test:
        run_erase_read_test(args, output, connection, backup, start, report)
        return
    if not args.write:
        print("Read-only acceptance passed. Add --write to program and verify both split images.")
        return

    outcomes = []
    worker = FlashWorker(
        None, args.target, probe_uid=args.probe, erase_mode="sector", verify=True,
        reset_after=False, clock_khz=1000,
        images=[FirmwareImage(first, start), FirmwareImage(second, start + split)],
    )
    worker.log.connect(print)
    worker.finished.connect(outcomes.append)
    worker.run()
    if outcomes != [True]:
        raise RuntimeError(f"Split-image programming failed. Full backup: {backup_path}")
    with ConnectHelper.session_with_chosen_probe(**connection) as session:
        actual = read_flash(session, session.target.memory_map.get_region_for_address(start))
    expected = bytearray(backup)
    expected[:len(original)] = original
    (output / "flash-after.bin").write_bytes(actual)
    if actual != expected:
        offset = next(offset for offset, values in enumerate(zip(actual, expected)) if values[0] != values[1])
        raise AssertionError(f"Full Flash mismatch at 0x{start + offset:08X}. Backup: {backup_path}")
    reset_outcomes = []
    reset = DeviceWorker(args.target, "reset", probe_uid=args.probe, clock_khz=1000, reset_type="system")
    reset.log.connect(print)
    reset.finished.connect(reset_outcomes.append)
    reset.run()
    if reset_outcomes != [True]:
        raise RuntimeError("Programming verified, but the independent reset failed.")
    report.update(written=True, full_flash_verified=True, reset_verified=True,
                  after_sha256=hashlib.sha256(actual).hexdigest())
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS: split images programmed; full Flash matches; outside-image bytes preserved; reset passed.")
    print(f"Artifacts: {output}")


def run_with_watchdog_freeze(args) -> None:
    if not args.freeze_stm32f1_watchdogs:
        run(args)
        return
    if not args.target.lower().startswith("stm32f1"):
        raise ValueError("The watchdog freeze hook is only valid for STM32F1 targets.")
    original_factory = ConnectHelper.session_with_chosen_probe
    original_register = []

    class FreezeDelegate:
        def did_connect(self, board) -> None:
            target = board.target
            value = target.read32(0xE0042004)
            if not original_register:
                original_register.append(value)
            target.write32(0xE0042004, value | 0x300)
            target.halt()

    def factory(*positional, **keywords):
        session = original_factory(*positional, **keywords)
        if session is None:
            raise RuntimeError("Expected probe is not available.")
        session.delegate = FreezeDelegate()
        return session

    with patch.object(ConnectHelper, "session_with_chosen_probe", side_effect=factory):
        run(args)
    if original_register:
        with original_factory(
            unique_id=args.probe, target_override=args.target, auto_unlock=False,
            connect_mode="attach", options={"frequency": 1000000, "resume_on_disconnect": False},
        ) as session:
            session.target.write32(0xE0042004, original_register[0])
            session.target.reset(reset_type=Target.ResetType.SYSTEM)
            print(f"Original DBGMCU_CR restored: 0x{original_register[0]:X}; target reset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--write", action="store_true", help="Explicitly allow sector erase and programming")
    parser.add_argument("--erase-read-test", action="store_true",
                        help="Test one non-boot sector erase and GUI BIN/HEX reads, then restore the full backup")
    parser.add_argument("--freeze-stm32f1-watchdogs", action="store_true",
                        help="Freeze STM32F1 watchdogs while halted using a real did_connect hook")
    arguments = parser.parse_args()
    application = QCoreApplication([])
    run_with_watchdog_freeze(arguments)