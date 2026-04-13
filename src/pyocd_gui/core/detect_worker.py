"""Background worker thread for target auto-detection."""

from typing import Optional

from PySide6.QtCore import QThread, Signal

from pyocd_gui.core.target_detector import detect_target


class DetectWorker(QThread):
    """Run target detection in a background thread.

    Signals
    -------
    result(str or None, str)   (target_name, info_message)
    """

    result = Signal(object, str)  # (Optional[str], str)

    def __init__(self, probe_uid: Optional[str] = None) -> None:
        super().__init__()
        self._probe_uid = probe_uid

    def run(self) -> None:
        target_name, msg = detect_target(self._probe_uid)
        self.result.emit(target_name, msg)
