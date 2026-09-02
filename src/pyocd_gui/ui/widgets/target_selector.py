"""Target chip selector widget."""

from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget, QHBoxLayout

from pyocd.target import TARGET


def _all_targets() -> list[str]:
    return sorted(TARGET.keys())


class TargetSelector(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setMinimumWidth(200)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        targets = _all_targets()
        self._combo.addItems(targets)

        # 设置补全（输入时自动过滤）
        from PySide6.QtWidgets import QCompleter
        from PySide6.QtCore import Qt
        completer = QCompleter(targets)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._combo.setCompleter(completer)

        # 默认不选中：generic cortex_m 无法烧录，留空提示用户选择或 Detect
        self._combo.setCurrentIndex(-1)
        self._combo.lineEdit().setPlaceholderText("Select or detect target…")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo, 1)

    def current_target(self) -> str:
        return self._combo.currentText().strip()

    def set_target(self, name: str) -> None:
        idx = self._combo.findText(name)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setCurrentText(name)
