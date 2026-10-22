import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyocd.core.memory_map import FlashRegion, MemoryMap
from pyocd.core.target import Target
from pyocd.flash.eraser import FlashEraser
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox
from intelhex import IntelHex

from pyocd_gui.core.firmware import FirmwareImage, parse_address, validate_images
from pyocd_gui.core.flash_worker import FlashWorker
from pyocd_gui.core.erase_worker import EraseWorker, validate_erase_range
from pyocd_gui.core.device_worker import DeviceWorker
from pyocd_gui.core.dump_worker import DumpWorker
from pyocd_gui.ui.widgets.file_picker import FilePicker
from pyocd_gui.ui.main_window import MainWindow


class BufferedLoader:
    instances = []

    def __init__(self, *args, **kwargs):
        self.data = {}
        self.commits = 0
        self.instances.append(self)

    def add_data(self, address, data):
        for offset, value in enumerate(data):
            if address + offset in self.data:
                raise ValueError("Overlapping image")
            self.data[address + offset] = value

    def commit(self):
        self.commits += 1


class FirmwareFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original = bytes(range(256)) * 32
        self.whole = self.root / "whole.bin"
        self.whole.write_bytes(self.original)
        self.first = self.root / "part1.bin"
        self.second = self.root / "part2.bin"
        self.first.write_bytes(self.original[:4096])
        self.second.write_bytes(self.original[4096:])
        self.session = MagicMock()
        self.session.target.memory_map = MemoryMap(
            FlashRegion(start=0x08000000, length=0x10000, blocksize=2048, is_boot_memory=True)
        )
        BufferedLoader.instances = []

    def run_worker(self, images):
        outcomes = []
        worker = FlashWorker(None, "test_chip", images=images, reset_after=False)
        worker.finished.connect(outcomes.append)
        with patch("pyocd_gui.core.flash_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            with patch("pyocd.flash.file_programmer.FlashLoader", BufferedLoader):
                worker.run()
        return outcomes

class FirmwareTests(FirmwareFixture):
    def test_split_bin_matches_original_with_one_commit(self):
        self.assertEqual(self.run_worker([
            FirmwareImage(self.first, 0x08000000),
            FirmwareImage(self.second, 0x08001000),
        ]), [True])
        loader, = BufferedLoader.instances
        self.assertEqual(loader.commits, 1)
        self.assertEqual(bytes(loader.data[0x08000000 + offset] for offset in range(8192)), self.original)

    def test_default_address_matches_target_boot_memory(self):
        self.assertEqual(self.run_worker([FirmwareImage(self.whole)]), [True])
        self.assertEqual(min(BufferedLoader.instances[0].data), 0x08000000)

    def test_flash_without_reset_does_not_resume_on_disconnect(self):
        with patch("pyocd_gui.core.flash_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            with patch("pyocd.flash.file_programmer.FlashLoader", BufferedLoader):
                FlashWorker(self.whole, "test_chip", reset_after=False).run()
            self.assertFalse(connect.call_args.kwargs["options"]["resume_on_disconnect"])
            self.session.target.reset.assert_not_called()

    def test_mixed_bin_hex_uses_embedded_addresses(self):
        path = self.root / "data.hex"
        contents = IntelHex()
        contents.frombytes([1, 2, 3, 4], offset=0x08002000)
        contents.write_hex_file(str(path))
        self.assertEqual(self.run_worker([FirmwareImage(self.first), FirmwareImage(path)]), [True])
        self.assertEqual(BufferedLoader.instances[0].data[0x08002000], 1)
        self.assertEqual(BufferedLoader.instances[0].commits, 1)

    def test_mixed_format_overlap_is_rejected_before_commit(self):
        path = self.root / "overlap.hex"
        contents = IntelHex()
        contents.frombytes([1, 2, 3, 4], offset=0x08000000)
        contents.write_hex_file(str(path))
        self.assertEqual(self.run_worker([
            FirmwareImage(self.first, 0x08000000), FirmwareImage(path),
        ]), [False])
        self.assertEqual(BufferedLoader.instances, [])

    def test_invalid_elf_never_connects(self):
        path = self.root / "invalid.elf"
        path.write_bytes(b"not an ELF file")
        with patch("pyocd_gui.core.flash_worker.ConnectHelper.session_with_chosen_probe") as connect:
            FlashWorker(path, "test_chip").run()
            connect.assert_not_called()

    def test_overlap_never_commits(self):
        self.assertEqual(self.run_worker([
            FirmwareImage(self.first), FirmwareImage(self.second, 0x08000800),
        ]), [False])
        self.assertEqual(BufferedLoader.instances, [])
        self.session.target.reset_and_halt.assert_not_called()

    def test_out_of_flash_never_commits(self):
        self.assertEqual(self.run_worker([FirmwareImage(self.whole, 0x20000000)]), [False])
        self.assertEqual(BufferedLoader.instances, [])

    def test_missing_second_image_never_connects(self):
        with patch("pyocd_gui.core.flash_worker.ConnectHelper.session_with_chosen_probe") as connect:
            worker = FlashWorker(None, "test_chip", images=[
                FirmwareImage(self.first), FirmwareImage(self.root / "missing.bin"),
            ])
            worker.run()
            connect.assert_not_called()

    def test_address_parser_and_non_bin_address_rejection(self):
        self.assertEqual(parse_address("0x08001000"), 0x08001000)
        self.assertEqual(parse_address("4096"), 4096)
        for text in ("-1", "0x100000000", "invalid"):
            with self.assertRaises(ValueError):
                parse_address(text)
        hex_path = self.root / "image.hex"
        hex_path.write_text(":00000001FF\n", encoding="ascii")
        with self.assertRaises(ValueError):
            validate_images((FirmwareImage(hex_path, 0x08000000),))


class DumpTests(FirmwareFixture):
    def test_hex_export_has_exact_length_and_base(self):
        path = self.root / "dump.hex"
        data = bytes([0x12, 0x00, 0xFF])
        DumpWorker._write(path, data, 0x08000000)
        actual = IntelHex(str(path))
        self.assertEqual(actual.segments(), [(0x08000000, 0x08000003)])
        self.assertEqual(bytes(actual.tobinarray()), data)

    def test_dump_binary_reads_every_chunk_and_stays_halted(self):
        path = self.root / "dump.bin"
        data = bytes(range(256)) * 256
        self.session.target.read_memory_block8.side_effect = lambda address, size: list(
            data[address - 0x08000000:address - 0x08000000 + size]
        )
        self.session.target.get_state.return_value = Target.State.HALTED
        outcomes = []
        progress = []
        with patch("pyocd_gui.core.dump_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            worker = DumpWorker(path, "test_chip", reset_after=False)
            worker._CHUNK_SIZE = 4096
            worker.finished.connect(outcomes.append)
            worker.progress.connect(progress.append)
            worker.run()
            self.assertEqual(outcomes, [True])
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(self.session.target.read_memory_block8.call_count, 16)
            self.assertEqual(progress[-1], 100)
            self.assertFalse(connect.call_args.kwargs["auto_unlock"])
            self.assertFalse(connect.call_args.kwargs["options"]["resume_on_disconnect"])
            self.session.target.reset.assert_not_called()

    def test_dump_does_not_save_if_target_resets_during_read(self):
        path = self.root / "unstable.bin"
        outcomes = []
        self.session.target.get_state.side_effect = [Target.State.HALTED, Target.State.RUNNING]
        self.session.target.read_memory_block8.return_value = [0xFF] * 65536
        with patch("pyocd_gui.core.dump_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            worker = DumpWorker(path, "test_chip", reset_after=False)
            worker.finished.connect(outcomes.append)
            worker.run()
        self.assertEqual(outcomes, [False])
        self.assertFalse(path.exists())


class ImagePickerTests(FirmwareFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_image_list_parses_addresses_and_removes_rows(self):
        with patch("pyocd_gui.ui.widgets.file_picker.config.get", return_value=""):
            picker = FilePicker()
        picker._add_paths([str(self.first), str(self.second)])
        picker._table.item(0, 1).setText("0x08000000")
        picker._table.item(1, 1).setText("0x08001000")
        images = picker.firmware_images()
        self.assertEqual([image.base_address for image in images], [0x08000000, 0x08001000])
        picker._table.selectRow(1)
        picker._remove_selected()
        self.assertEqual(len(picker.firmware_images()), 1)
        picker.close()


class DeviceOperationTests(FirmwareFixture):
    def setUp(self):
        super().setUp()
        self.flash = MagicMock()
        self.session.target.memory_map.regions[0].flash = self.flash
        self.flash.get_sector_info.side_effect = lambda address: SimpleNamespace(
            base_addr=address - address % 2048, size=2048,
        )

    def test_range_erase_passes_exclusive_end_after_preflight(self):
        with patch("pyocd_gui.core.erase_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            with patch("pyocd_gui.core.erase_worker.FlashEraser", wraps=FlashEraser) as eraser:
                eraser.Mode = FlashEraser.Mode
                with patch.object(FlashEraser, "erase") as erase:
                    worker = EraseWorker("test_chip", mode="range", start_address=0x08000000,
                                         end_address=0x08001000, reset_after=False)
                    worker.run()
                    eraser.assert_called_once_with(self.session, FlashEraser.Mode.SECTOR)
                    erase.assert_called_once_with([(0x08000000, 0x08001000)])
                    self.assertFalse(connect.call_args.kwargs["auto_unlock"])
                    self.assertFalse(connect.call_args.kwargs["options"]["resume_on_disconnect"])

    def test_unaligned_or_out_of_flash_range_never_erases(self):
        for start, end in ((0x08000001, 0x08001000), (0x08000000, 0x08001001),
                           (0x0800F800, 0x08010800), (0x08001000, 0x08000000)):
            with self.subTest(start=start, end=end):
                with patch("pyocd_gui.core.erase_worker.ConnectHelper.session_with_chosen_probe") as connect:
                    connect.return_value.__enter__.return_value = self.session
                    with patch("pyocd_gui.core.erase_worker.FlashEraser") as eraser:
                        worker = EraseWorker("test_chip", mode="range", start_address=start, end_address=end)
                        worker.run()
                        eraser.assert_not_called()

    def test_default_mass_erase_is_preserved(self):
        with patch("pyocd_gui.core.erase_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            with patch("pyocd_gui.core.erase_worker.FlashEraser") as eraser:
                eraser.Mode = FlashEraser.Mode
                EraseWorker("test_chip").run()
                eraser.assert_called_once_with(self.session, FlashEraser.Mode.MASS)
                eraser.return_value.erase.assert_called_once_with()

    def test_reset_uses_selected_type_without_auto_unlock(self):
        with patch("pyocd_gui.core.device_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            worker = DeviceWorker("test_chip", "reset", reset_type="hardware", clock_khz=240)
            worker.run()
            self.session.target.reset.assert_called_once_with(reset_type=Target.ResetType.HARDWARE)
            self.assertFalse(connect.call_args.kwargs["auto_unlock"])
            self.assertEqual(connect.call_args.kwargs["options"]["frequency"], 240000)

    def test_info_reports_memory_map_without_reset_or_halt(self):
        results = []
        self.session.target.part_number = "TEST_CHIP"
        self.session.target.vendor = "Test Vendor"
        self.session.target.selected_core.name = "Cortex-M3"
        self.session.probe.unique_id = "TEST_PROBE"
        with patch("pyocd_gui.core.device_worker.ConnectHelper.session_with_chosen_probe") as connect:
            connect.return_value.__enter__.return_value = self.session
            worker = DeviceWorker("test_chip", "info")
            worker.result.connect(results.append)
            worker.run()
            self.assertEqual(results[0]["part"], "TEST_CHIP")
            self.assertEqual(results[0]["regions"][0]["start"], 0x08000000)
            self.assertEqual(results[0]["probe_uid"], "TEST_PROBE")
            self.assertEqual(results[0]["core"], "Cortex-M3")
            self.assertEqual(connect.call_args.kwargs["connect_mode"], "attach")
            self.session.target.reset.assert_not_called()
            self.session.target.halt.assert_not_called()


class MainWindowTests(FirmwareFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        with patch("pyocd_gui.ui.widgets.probe_selector.list_connected_probes", return_value=[]), \
             patch("pyocd_gui.ui.widgets.target_selector.ManagedPacks.get_installed_targets", return_value=[]), \
             patch("pyocd_gui.ui.widgets.file_picker.config.get", return_value=""):
            self.window = MainWindow()
        self.window._target_selector.set_target("test_chip")

    def tearDown(self):
        self.window._worker = None
        self.window._erase_worker = None
        self.window._device_worker = None
        self.window.close()

    def test_flash_receives_all_images_and_addresses(self):
        self.window._file_picker._add_paths([str(self.first), str(self.second)])
        self.window._file_picker._table.item(0, 1).setText("0x08000000")
        self.window._file_picker._table.item(1, 1).setText("0x08001000")
        with patch("pyocd_gui.ui.main_window.FlashWorker") as worker:
            self.window._start_flash()
            self.assertEqual(len(worker.call_args.kwargs["images"]), 2)
            self.assertEqual(worker.call_args.kwargs["images"][1].base_address, 0x08001000)
            self.assertTrue(self.window._busy)
            self.assertFalse(self.window._detect_btn.isEnabled())
            self.assertFalse(self.window._packs_btn.isEnabled())
            self.window._on_flash_finished(True)
            self.assertTrue(self.window._reset_btn.isEnabled())

    def test_range_erase_ui_passes_bounds(self):
        self.window._erase_scope.setCurrentIndex(self.window._erase_scope.findData("range"))
        self.window._erase_start.setText("0x08000000")
        self.window._erase_end.setText("0x08001000")
        self.assertTrue(self.window._erase_start.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch("pyocd_gui.ui.main_window.EraseWorker") as worker:
            self.window._start_erase()
            self.assertEqual(worker.call_args.kwargs["mode"], "range")
            self.assertEqual(worker.call_args.kwargs["end_address"], 0x08001000)

    def test_info_panel_populates_regions(self):
        self.window._show_device_info({
            "target": "test_chip", "part": "TEST", "vendor": "Vendor", "core": "Cortex-M0+",
            "board": "Board", "probe_uid": "1234",
            "regions": [{"name": "flash", "type": "FLASH", "start": 0x08000000,
                         "end": 0x0800FFFF, "length": 65536}],
        })
        self.assertEqual(self.window._region_table.rowCount(), 1)
        self.assertIn("65,536", self.window._info_summary.text())
        self.assertFalse(self.window._info_dock.isHidden())
        self.assertGreaterEqual(self.window._info_dock.minimumWidth(), 380)

    def test_busy_blocks_device_actions(self):
        self.window._set_busy(True)
        with patch("pyocd_gui.ui.main_window.DeviceWorker") as worker:
            self.window._start_device_action("reset")
            worker.assert_not_called()
        self.assertFalse(self.window._info_btn.isEnabled())
        self.assertFalse(self.window._compare_btn.isEnabled())

    def test_compare_dialog_receives_images_and_releases_busy_state(self):
        self.window._file_picker._add_paths([str(self.first)])
        self.window._file_picker._table.item(0, 1).setText("0x08001000")
        with patch("pyocd_gui.ui.main_window.CompareDialog") as dialog:
            dialog.return_value.exec.side_effect = lambda: self.assertTrue(self.window._busy)
            self.window._open_compare()
            self.assertEqual(dialog.call_args.args[0][0].base_address, 0x08001000)
            self.assertEqual(dialog.call_args.args[1], "test_chip")
            dialog.return_value.exec.assert_called_once()
            dialog.return_value.deleteLater.assert_called_once()
            self.assertFalse(self.window._busy)


if __name__ == "__main__":
    unittest.main()