import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QProcess
from PySide6.QtGui import QFont, QTextCharFormat

from pyocd_gui.ui.pack_manager import PackManagerDialog
from pyocd_gui.ui.widgets.target_selector import TargetSelector, _all_targets


class TargetRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("pyocd_gui.ui.widgets.target_selector.ManagedPacks.get_installed_targets")
    def test_includes_normalized_installed_targets(self, installed):
        installed.return_value = [SimpleNamespace(part_number="NEW-CHIP")]
        targets = _all_targets()
        self.assertIn("new_chip", targets)
        self.assertIn("cortex_m", targets)
        self.assertEqual(targets, sorted(set(targets)))

    @patch("pyocd_gui.ui.widgets.target_selector.ManagedPacks.get_installed_targets")
    def test_refresh_preserves_selection_and_updates_completion(self, installed):
        installed.return_value = []
        selector = TargetSelector()
        self.assertEqual(selector.current_target(), "")
        selector.set_target("cortex_m")
        installed.return_value = [SimpleNamespace(part_number="NEW-CHIP")]
        selector.refresh_targets()
        self.assertEqual(selector.current_target(), "cortex_m")
        self.assertGreaterEqual(selector._combo.findText("new_chip"), 0)
        self.assertIn("new_chip", selector._combo.completer().model().stringList())
        selector.close()


class PackManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(PackManagerDialog, "_run_command"):
            self.dialog = PackManagerDialog()

    def tearDown(self):
        self.dialog._operation = ""
        self.dialog.close()

    @patch.object(QProcess, "start")
    def test_find_passes_pattern_as_single_argument(self, start):
        self.dialog._query.setText("stm32g0*")
        self.dialog._find()
        start.assert_called_once_with()
        self.assertEqual(self.dialog._process.arguments()[-2:], ["-f", "stm32g0*"])
        self.assertFalse(self.dialog._install_btn.isEnabled())
        self.dialog._on_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertTrue(self.dialog._install_btn.isEnabled())

    @patch.object(QProcess, "start")
    def test_empty_search_does_not_start(self, start):
        self.dialog._find()
        start.assert_not_called()
        self.assertEqual(self.dialog._operation, "")

    def test_ansi_color_and_reset_preserve_plain_text(self):
        self.dialog._append_output("\x1b[31m<red>\x1b[0m plain")
        self.assertEqual(self.dialog._output.toPlainText(), "<red> plain")
        document = self.dialog._output.document()
        self.assertEqual(document.find("red").charFormat().foreground().color().name(), "#aa0000")
        self.assertFalse(document.find("plain").charFormat().hasProperty(
            QTextCharFormat.Property.ForegroundBrush
        ))

    def test_ansi_escape_can_span_output_chunks(self):
        self.dialog._append_output("before \x1b")
        self.dialog._append_output("[1;9")
        self.assertEqual(self.dialog._output.toPlainText(), "before ")
        self.dialog._append_output("1merror\x1b[0m after")
        self.assertEqual(self.dialog._output.toPlainText(), "before error after")
        char_format = self.dialog._output.document().find("error").charFormat()
        self.assertEqual(char_format.foreground().color().name(), "#ff5555")
        self.assertEqual(char_format.fontWeight(), QFont.Weight.Bold)

    def test_dim_style_and_normal_intensity(self):
        self.dialog._append_output("\x1b[2mdebug\x1b[22mnormal")
        document = self.dialog._output.document()
        self.assertLess(document.find("debug").charFormat().foreground().color().alpha(), 255)
        self.assertFalse(document.find("normal").charFormat().hasProperty(
            QTextCharFormat.Property.ForegroundBrush
        ))

    @patch.object(QProcess, "start")
    def test_new_command_forces_color_and_resets_previous_style(self, start):
        self.dialog._append_output("\x1b[31mred\x1b[")
        self.dialog._run_command("show")
        self.assertIn("always", self.dialog._process.arguments())
        self.assertEqual(self.dialog._ansi_pending, "")
        self.assertFalse(self.dialog._ansi_format.hasProperty(
            QTextCharFormat.Property.ForegroundBrush
        ))
        self.assertNotIn("\x1b", self.dialog._output.toPlainText())

    @patch.object(QProcess, "start")
    def test_partial_install_failure_still_refreshes_targets(self, start):
        changes = []
        self.dialog.packs_changed.connect(lambda: changes.append(True))
        self.dialog._query.setText("stm32g0*")
        self.dialog._run_command("install")
        self.dialog._on_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertEqual(changes, [True])
        self.assertIn("Failed", self.dialog._status.text())
        self.assertTrue(self.dialog._close_btn.isEnabled())

    @patch.object(QProcess, "start")
    def test_start_failure_restores_controls(self, start):
        self.dialog._run_command("show")
        self.dialog._on_error(QProcess.ProcessError.FailedToStart)
        self.assertEqual(self.dialog._operation, "")
        self.assertTrue(self.dialog._installed_btn.isEnabled())

    @patch.object(QProcess, "start")
    @patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No)
    def test_install_requires_confirmation(self, question, start):
        self.dialog._query.setText("stm32g0b1retx")
        self.dialog._install()
        question.assert_called_once()
        start.assert_not_called()

    @patch.object(QProcess, "start")
    @patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes)
    def test_confirmed_install_notifies_on_completion(self, question, start):
        changes = []
        self.dialog.packs_changed.connect(lambda: changes.append(True))
        self.dialog._query.setText("stm32g0b1retx")
        self.dialog._install()
        self.assertEqual(self.dialog._process.arguments()[-2:], ["-i", "stm32g0b1retx"])
        self.assertEqual(changes, [])
        self.dialog._on_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(changes, [True])
        self.assertIn("Completed", self.dialog._status.text())

    @patch.object(QProcess, "start")
    def test_reject_is_blocked_while_running(self, start):
        self.dialog.show()
        self.dialog._run_command("update")
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible())
        self.dialog._on_finished(0, QProcess.ExitStatus.NormalExit)
        self.dialog.reject()
        self.assertFalse(self.dialog.isVisible())


if __name__ == "__main__":
    unittest.main()