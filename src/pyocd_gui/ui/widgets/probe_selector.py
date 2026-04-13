"""Probe selector combo-box widget."""

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

from pyocd_gui.core.probe_manager import ProbeInfo, list_connected_probes


class ProbeSelector(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._combo = QComboBox()
        self._combo.setMinimumWidth(280)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo, 1)
        layout.addWidget(refresh_btn)

        self.refresh()

    def refresh(self) -> None:
        self._combo.clear()
        probes = list_connected_probes()
        if probes:
            for p in probes:
                self._combo.addItem(str(p), userData=p)
        else:
            self._combo.addItem("No probe detected", userData=None)

    def selected_probe(self) -> ProbeInfo | None:
        return self._combo.currentData()
