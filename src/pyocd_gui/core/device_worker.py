"""Background reset and device-information operations."""

from PySide6.QtCore import QThread, Signal

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target


class DeviceWorker(QThread):
    log = Signal(str)
    result = Signal(object)
    finished = Signal(bool)

    def __init__(
        self, target: str, action: str, probe_uid: str | None = None,
        clock_khz: int | None = None, reset_type: str = "system",
    ) -> None:
        super().__init__()
        self._target = target
        self._action = action
        self._probe_uid = probe_uid
        self._clock_khz = clock_khz
        self._reset_type = reset_type

    def run(self) -> None:
        try:
            if not self._target or self._action not in ("info", "reset"):
                raise ValueError("Select a target and a valid device action.")
            reset_type = Target.ResetType[self._reset_type.upper()]
            options = {"resume_on_disconnect": False}
            if self._clock_khz:
                options["frequency"] = self._clock_khz * 1000
            with ConnectHelper.session_with_chosen_probe(
                unique_id=self._probe_uid, target_override=self._target,
                auto_unlock=False, connect_mode="attach", options=options,
            ) as session:
                if self._action == "reset":
                    session.target.reset(reset_type=reset_type)
                    self.log.emit(f"Target reset ({self._reset_type}).")
                else:
                    target = session.target
                    core = target.selected_core
                    regions = [
                        {
                            "name": region.name, "type": region.type.name,
                            "start": region.start, "end": region.end,
                            "length": region.length,
                        }
                        for region in target.memory_map
                    ]
                    self.result.emit({
                        "target": self._target,
                        "part": str(getattr(target, "part_number", self._target)),
                        "vendor": str(getattr(target, "vendor", "")),
                        "board": str(getattr(session.board, "name", "")),
                        "probe_uid": str(session.probe.unique_id),
                        "core": str(getattr(core, "name", "Unknown")),
                        "regions": regions,
                    })
                    self.log.emit("Chip information read; regions are from the pyOCD target memory map.")
            self.finished.emit(True)
        except Exception as error:
            self.log.emit(f"Device {self._action} failed: {error}")
            self.finished.emit(False)