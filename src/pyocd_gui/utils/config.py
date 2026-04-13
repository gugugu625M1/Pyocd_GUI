"""Application configuration — persisted via QSettings."""

from PySide6.QtCore import QSettings


_ORG = "pyocd-gui"
_APP = "pyocd-gui"


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def get(key: str, default=None):
    return _settings().value(key, default)


def set(key: str, value) -> None:
    s = _settings()
    s.setValue(key, value)
    s.sync()
