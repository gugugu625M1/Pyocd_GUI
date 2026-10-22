"""Automatic target detection by reading vendor-specific identification registers.

The detected part is resolved to a target type name that pyOCD can actually use
(either a built-in target or one from an installed CMSIS-Pack), so that the name
can be passed directly to ``target_override`` for flash programming.
"""

from functools import lru_cache
from typing import Optional

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.target import TARGET

# ---------------------------------------------------------------------------
# STM32 DBGMCU_IDCODE base addresses, by device generation.
# The first readable register whose DEV_ID is known wins.
#   G0                                        : 0x40015800
#   legacy (F0/F1/F2/F3/F4/F7/L0/L1/L4/G4)   : 0xE0042000
#   H7                                        : 0x5C001000
#   new    (L5/U5/H5/H7RS/WBA)                : 0xE0044000
# ---------------------------------------------------------------------------
_DBGMCU_IDCODE_ADDRS = (0x40015800, 0xE0042000, 0x5C001000, 0xE0044000)

# STM32 flash size register addresses, tried in order until a sane value reads.
#   0x1FFFF7E0 — F0/F1/F3/L1      0x1FFF7A22 — F2/F4
#   0x1FFF75E0 — G0/G4/L4         0x1FF0F442 — F7
#   0x1FF1E880 — H7
_FLASH_SIZE_ADDRS = (0x1FFFF7E0, 0x1FFF7A22, 0x1FFF75E0, 0x1FF0F442, 0x1FF1E880)

_FLASH_KB_MIN, _FLASH_KB_MAX = 16, 4096

# ---------------------------------------------------------------------------
# STM32 DBGMCU_IDCODE → pyocd target name map
# Key: (DEV_ID, flash_kb)   Value: base part name.
# Base names are resolved to an exact, installed pyOCD target name at runtime
# (pack names carry temperature/package suffixes, e.g. stm32h743zi → stm32h743zitx).
# ---------------------------------------------------------------------------
_STM32_TARGETS: dict[tuple[int, int], str] = {
    # F0
    (0x440, 32):   "stm32f030c8",
    (0x440, 64):   "stm32f030r8",
    (0x442, 256):  "stm32f030cc",
    (0x444, 16):   "stm32f030x4",
    (0x445, 32):   "stm32f042f6",
    (0x448, 64):   "stm32f070rb",
    # F1 Low-density
    (0x412, 16):   "stm32f103c4",
    (0x412, 32):   "stm32f103c6",
    # F1 Medium-density
    (0x410, 64):   "stm32f103c8",
    (0x410, 128):  "stm32f103rb",
    # F1 High-density
    (0x414, 256):  "stm32f103rc",
    (0x414, 384):  "stm32f103rd",
    (0x414, 512):  "stm32f103re",
    # F1 Connectivity
    (0x418, 256):  "stm32f105rc",
    # F2
    (0x411, 128):  "stm32f205rb",
    (0x411, 256):  "stm32f205rc",
    (0x411, 512):  "stm32f205re",
    # F3
    (0x422, 256):  "stm32f303cc",
    (0x422, 512):  "stm32f303ce",
    (0x438, 64):   "stm32f334r8",
    # F4
    (0x413, 512):  "stm32f405rg",
    (0x413, 1024): "stm32f407zg",
    (0x419, 1024): "stm32f429zi",
    (0x419, 2048): "stm32f437ig",
    (0x423, 256):  "stm32f401rc",
    (0x423, 512):  "stm32f401re",
    (0x431, 512):  "stm32f411re",
    (0x433, 384):  "stm32f401cc",
    (0x441, 512):  "stm32f412zg",
    (0x421, 512):  "stm32f446re",
    (0x434, 1024): "stm32f469ni",
    # F7
    (0x449, 1024): "stm32f745zg",
    (0x451, 1024): "stm32f767zi",
    (0x451, 2048): "stm32f769ni",
    (0x452, 512):  "stm32f723ze",
    # L4
    (0x415, 256):  "stm32l431rc",
    (0x435, 256):  "stm32l433rc",
    (0x462, 512):  "stm32l452re",
    (0x461, 1024): "stm32l496zg",
    (0x470, 1024): "stm32l4r5zi",
    # G0
    (0x460, 32):   "stm32g031c8",
    (0x460, 64):   "stm32g031k8",
    (0x467, 512):  "stm32g0b1ret",
    # G4
    (0x468, 128):  "stm32g431rb",
    (0x469, 512):  "stm32g474re",
    # H7
    (0x450, 1024): "stm32h743zg",
    (0x450, 2048): "stm32h743zi",
    # WB
    (0x495, 1024): "stm32wb55rg",
}

# DEV_ID → family label (fallback when flash size not in map)
_STM32_FAMILY: dict[int, str] = {
    0x412: "STM32F103 (Low-density)",
    0x410: "STM32F103 (Medium-density)",
    0x414: "STM32F103 (High-density)",
    0x418: "STM32F105/F107",
    0x411: "STM32F2xx",
    0x413: "STM32F40x/41x",
    0x419: "STM32F42x/43x",
    0x423: "STM32F401 B/C",
    0x431: "STM32F411",
    0x421: "STM32F446",
    0x449: "STM32F74x/75x",
    0x451: "STM32F76x/77x",
    0x452: "STM32F72x/73x",
    0x415: "STM32L4x1/L4x2",
    0x435: "STM32L43x/44x",
    0x462: "STM32L45x/46x",
    0x461: "STM32L496/4A6",
    0x470: "STM32L4R/4S",
    0x460: "STM32G03x/04x",
    0x467: "STM32G0B/0C",
    0x468: "STM32G43x/44x",
    0x469: "STM32G47x/48x",
    0x450: "STM32H74x/75x",
    0x495: "STM32WB55",
}


def detect_target(
    probe_uid: Optional[str] = None, clock_khz: Optional[int] = None
) -> tuple[Optional[str], str]:
    """Connect with generic cortex_m, read vendor ID registers, return (target_name, info_msg).

    ``target_name`` is guaranteed to be a usable pyOCD target type (built-in or
    from an installed CMSIS-Pack), suitable for ``target_override``. If the chip
    is identified but its target support is not installed, ``None`` is returned
    with an explanatory message.

    Returns
    -------
    target_name : str or None
        pyocd target name if identified and usable, None otherwise.
    info_msg : str
        Human-readable description of what was found.
    """
    try:
        options = {"smart_flash": False}
        if clock_khz:
            options["frequency"] = clock_khz * 1000

        with ConnectHelper.session_with_chosen_probe(
            unique_id=probe_uid,
            target_override="cortex_m",
            auto_unlock=False,
            connect_mode="under-reset",
            options=options,
        ) as session:
            session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)

            # --- STM32: DBGMCU_IDCODE (address differs by device generation) ---
            read_errors: list[str] = []
            unknown_idcodes: list[str] = []
            for idcode_addr in _DBGMCU_IDCODE_ADDRS:
                try:
                    idcode = session.target.read32(idcode_addr)
                except Exception as exc:
                    read_errors.append(f"{idcode_addr:#010x}: {type(exc).__name__}")
                    continue

                dev_id = idcode & 0xFFF
                rev_id = (idcode >> 16) & 0xFFFF
                if dev_id not in _STM32_FAMILY:
                    unknown_idcodes.append(f"{idcode_addr:#010x}=0x{idcode:08X}")
                    continue  # readable but not a known STM32 — try next address

                return _describe_stm32(session.target, dev_id, rev_id)

            return (
                None,
                "Could not identify target (no known STM32 DBGMCU_IDCODE readable"
                + (f"; unknown IDCODEs: {', '.join(unknown_idcodes)}" if unknown_idcodes else "")
                + (f"; errors: {'; '.join(read_errors)}" if read_errors else "")
                + ")",
            )

    except Exception as exc:
        return None, f"Detection failed: {exc}"


def _describe_stm32(target, dev_id: int, rev_id: int) -> tuple[Optional[str], str]:
    """Look up the detected STM32 and resolve it to a usable pyOCD target name."""
    flash_kb = _read_flash_size(target)
    family = _STM32_FAMILY[dev_id]
    flash_str = f"{flash_kb} KB" if flash_kb else "unknown size"
    rev_str = f"REV=0x{rev_id:04X}"

    base = _STM32_TARGETS.get((dev_id, flash_kb))
    if base is None:
        return (
            None,
            f"Detected: {family}, Flash {flash_str}, {rev_str} "
            f"(no exact match — select manually)",
        )

    resolved = resolve_target_name(base)
    if resolved is not None:
        return resolved, f"Detected: {family}, Flash {flash_str}, {rev_str} → {resolved}"

    return (
        None,
        f"Detected: {family}, Flash {flash_str}, {rev_str}, but target support for "
        f"'{base}' is not installed — run 'pyocd pack install {base}' "
        f"or select a target manually",
    )


def resolve_target_name(base: str) -> Optional[str]:
    """Resolve a base part name to an exact, installed pyOCD target type name.

    Pack target names carry temperature/package suffixes (e.g. ``stm32h743zi``
    is installed as ``stm32h743zitx``), so an exact match is preferred and a
    prefix match is accepted. Flash algorithms are identical across those
    variants, so any matching variant programs the device correctly.

    Returns None when neither a built-in target nor an installed pack provides
    support for the part.
    """
    base = base.lower()
    if base in TARGET:
        return base

    parts = _installed_part_numbers()
    exact = [p for p in parts if p == base]
    if exact:
        return exact[0]
    prefixed = [p for p in parts if p.startswith(base)]
    if prefixed:
        return prefixed[0]
    return None


@lru_cache(maxsize=1)
def _installed_part_numbers() -> tuple[str, ...]:
    """Lower-case part numbers of all targets from managed (installed) CMSIS-Packs."""
    try:
        from pyocd.target.pack.pack_target import ManagedPacks

        return tuple(dev.part_number.lower() for dev in ManagedPacks.get_installed_targets())
    except Exception:
        return ()


def _read_flash_size(target) -> Optional[int]:
    """Read flash size in KB by trying known STM32 flash size register addresses."""
    for addr in _FLASH_SIZE_ADDRS:
        try:
            val = target.read16(addr)
            if _FLASH_KB_MIN <= val <= _FLASH_KB_MAX:  # sanity: 16 KB – 4 MB
                return val
        except Exception:
            continue
    return None
