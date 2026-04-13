"""Probe (debug adapter) discovery and management."""

from dataclasses import dataclass
from typing import List

from pyocd.core.helpers import ConnectHelper


@dataclass
class ProbeInfo:
    uid: str
    description: str

    def __str__(self) -> str:
        return f"{self.description} [{self.uid}]"


def list_connected_probes() -> List[ProbeInfo]:
    """Return a list of all currently connected debug probes."""
    probes: List[ProbeInfo] = []
    for probe in ConnectHelper.get_all_connected_probes(blocking=False):
        probes.append(ProbeInfo(uid=probe.unique_id, description=probe.product_name))
    return probes
