import os
import csv
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from intelhex import IntelHex
from pyocd.core.memory_map import FlashRegion, MemoryMap
from pyocd.core.target import Target
from PySide6.QtWidgets import QApplication, QFileDialog

from pyocd_gui.core.compare_worker import CompareWorker
from pyocd_gui.core.firmware import FirmwareImage
from pyocd_gui.ui.compare_dialog import CompareDialog


class CompareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "reference.bin"
        self.contents = bytes(range(256)) * 32
        self.path.write_bytes(self.contents)
        self.session = MagicMock()
        self.target = self.session.target
        self.target.memory_map = MemoryMap(FlashRegion(
            start=0x08000000, length=0x10000, blocksize=1024, is_boot_memory=True,
        ))
        self.target.get_state.return_value = Target.State.HALTED
        self.target.read_memory_block8.side_effect = lambda address, size: list(
            self.contents[address - 0x08000000:address - 0x08000000 + size]
        )

    def compare(self, images=None, freeze=False):
        reports, errors, outcomes = [], [], []
        worker = CompareWorker(images or [FirmwareImage(self.path)], "stm32f103rb",
                               freeze_stm32f1_watchdogs=freeze)
        worker.result.connect(reports.append)
        worker.error.connect(errors.append)
        worker.finished.connect(outcomes.append)
        with patch("pyocd_gui.core.compare_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            worker.run()
            if connect.called:
                self.assertFalse(connect.call_args.kwargs["auto_unlock"])
                self.assertEqual(connect.call_args.kwargs["connect_mode"], "attach")
        self.target.reset.assert_not_called()
        self.target.reset_and_halt.assert_not_called()
        self.target.write_memory_block8.assert_not_called()
        return reports, errors, outcomes

    def test_identical_bin_without_writes_or_reset(self):
        reports, errors, outcomes = self.compare()
        self.assertEqual(errors, [])
        self.assertEqual(outcomes, [True])
        self.assertTrue(reports[0]["matched"])
        self.assertEqual(reports[0]["compared_bytes"], 8192)
        self.target.write32.assert_not_called()

    def test_differences_report_addresses_and_both_bytes(self):
        altered = bytearray(self.contents)
        altered[5] = 0xFF
        altered[4097] = 0xAA
        self.path.write_bytes(altered)
        reports, errors, outcomes = self.compare()
        report, = reports
        self.assertEqual(report["mismatch_count"], 2)
        self.assertEqual(report["first_mismatch"], 0x08000005)
        self.assertEqual(report["differences"][0]["file_byte"], 0xFF)
        self.assertEqual(report["differences"][0]["flash_byte"], 5)
        self.assertEqual(report["differences"][1]["address"], 0x08001001)

    def test_counts_all_differences_but_limits_detail(self):
        self.target.read_memory_block8.side_effect = lambda address, size: [0xFF] * size
        reports, errors, outcomes = self.compare()
        self.assertEqual(reports[0]["mismatch_count"], 8192 - 32)
        self.assertEqual(len(reports[0]["differences"]), 256)

    def test_sparse_hex_does_not_compare_gaps(self):
        path = self.root / "sparse.hex"
        contents = IntelHex()
        contents.frombytes(self.contents[:4], offset=0x08000000)
        contents.frombytes(self.contents[4096:4100], offset=0x08001000)
        contents.write_hex_file(str(path))
        reports, errors, outcomes = self.compare([FirmwareImage(path)])
        self.assertTrue(reports[0]["matched"])
        self.assertEqual(reports[0]["compared_bytes"], 8)

    def test_elf_uses_physical_load_address_and_file_size(self):
        path = self.root / "reference.elf"
        identity = b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
        header = struct.pack("<16sHHIIIIIHHHHHH", identity, 2, 40, 1, 0x08001000,
                             52, 0, 0, 52, 32, 1, 0, 0, 0)
        segment = struct.pack("<IIIIIIII", 1, 84, 0x20000000, 0x08001000, 4, 16, 5, 4)
        path.write_bytes(header + segment + self.contents[4096:4100])
        reports, errors, outcomes = self.compare([FirmwareImage(path)])
        self.assertEqual(errors, [])
        self.assertTrue(reports[0]["matched"])
        self.assertEqual(reports[0]["compared_bytes"], 4)
        self.target.read_memory_block8.assert_called_once_with(0x08001000, 4)

    def test_cancel_does_not_publish_partial_result(self):
        with patch.object(CompareWorker, "isInterruptionRequested", return_value=True):
            reports, errors, outcomes = self.compare()
        self.assertEqual(reports, [])
        self.assertEqual(outcomes, [False])
        self.assertIn("cancelled", errors[0])
        self.target.read_memory_block8.assert_not_called()

    def test_read_error_restores_watchdog_register_and_running_state(self):
        self.target.get_state.side_effect = [Target.State.RUNNING, Target.State.HALTED]
        self.target.read32.return_value = 7
        self.target.read_memory_block8.side_effect = RuntimeError("probe disconnected")
        reports, errors, outcomes = self.compare(freeze=True)
        self.assertEqual(reports, [])
        self.assertIn("probe disconnected", errors[0])
        self.assertEqual(self.target.write32.call_args_list[-1].args, (0xE0042004, 7))
        self.target.resume.assert_called_once()

    def test_reset_during_read_discards_partial_result(self):
        self.target.get_state.side_effect = [Target.State.HALTED, Target.State.HALTED, Target.State.RUNNING]
        reports, errors, outcomes = self.compare()
        self.assertEqual(reports, [])
        self.assertEqual(outcomes, [False])
        self.assertIn("reset", errors[0])

    def test_short_read_is_not_reported_as_match(self):
        self.target.read_memory_block8.side_effect = lambda address, size: [0] * (size - 1)
        reports, errors, outcomes = self.compare()
        self.assertEqual(reports, [])
        self.assertIn("Short Flash read", errors[0])

    def test_watchdog_freeze_is_restored_and_running_target_resumed(self):
        self.target.get_state.side_effect = [Target.State.RUNNING] + [Target.State.HALTED] * 10
        self.target.read32.return_value = 7
        reports, errors, outcomes = self.compare(freeze=True)
        self.assertTrue(reports[0]["matched"])
        self.assertEqual(self.target.write32.call_args_list[0].args, (0xE0042004, 0x307))
        self.assertEqual(self.target.write32.call_args_list[-1].args, (0xE0042004, 7))
        self.target.resume.assert_called_once()


class CompareDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        path = self.root / "reference.bin"
        path.write_bytes(b"\x12\x34")
        self.dialog = CompareDialog([FirmwareImage(path, 0x08000000)], "stm32f103rb")

    def tearDown(self):
        self.dialog._worker = None
        self.dialog._busy = False
        self.dialog.close()

    def report(self):
        return {"matched": False, "compared_bytes": 2, "mismatch_count": 1,
                "first_mismatch": 0x08000001, "difference_limit": 256,
                "differences": [{"address": 0x08000001, "image": "reference.bin",
                                 "file_byte": 0x34, "flash_byte": 0xFF}]}

    def test_displays_file_flash_difference_and_summary(self):
        self.dialog._show_result(self.report())
        self.dialog._on_finished(True)
        self.assertIn("DIFFERENT", self.dialog._status.text())
        self.assertIn("0x08000001", self.dialog._summary.text())
        self.assertEqual(self.dialog._differences.item(0, 2).text(), "34")
        self.assertEqual(self.dialog._differences.item(0, 3).text(), "FF")
        self.assertTrue(self.dialog._export_btn.isEnabled())

    def test_export_csv_contains_displayed_differences(self):
        self.dialog._show_result(self.report())
        path = self.root / "differences.csv"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "CSV")):
            self.dialog._export()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[1], ["0x08000001", "reference.bin", "34", "FF"])

    def test_cancel_requests_interruption_and_close_is_blocked(self):
        self.dialog.show()
        self.dialog._worker = MagicMock()
        self.dialog._worker.isRunning.return_value = True
        self.dialog._set_busy(True)
        self.dialog._cancel()
        self.dialog._worker.requestInterruption.assert_called_once()
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible())

    def test_start_passes_watchdog_setting(self):
        self.dialog._freeze_check.setChecked(True)
        with patch("pyocd_gui.ui.compare_dialog.CompareWorker") as worker:
            self.dialog._start()
            self.assertTrue(worker.call_args.kwargs["freeze_stm32f1_watchdogs"])
            worker.return_value.start.assert_called_once()
            self.assertFalse(self.dialog._start_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()