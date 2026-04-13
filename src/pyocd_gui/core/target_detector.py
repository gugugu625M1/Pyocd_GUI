"""Automatic target detection by reading vendor-specific identification registers."""

from typing import Optional

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target

# ---------------------------------------------------------------------------
# STM32 DBGMCU_IDCODE → pyocd target name map
# Key: (DEV_ID, flash_kb)   Value: pyocd target type name
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
    (0x467, 64):   "stm32g0b1re",
    # G4
    (0x468, 128):  "stm32g431rb",
    (0x469, 512):  "stm32g474re",
    # H7
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


def detect_target(probe_uid: Optional[str] = None) -> tuple[Optional[str], str]:
    """Connect with generic cortex_m, read vendor ID registers, return (target_name, info_msg).

    Returns
    -------
    target_name : str or None
        pyocd target name if identified, None otherwise.
    info_msg : str
        Human-readable description of what was found.
    """
    try:
        with ConnectHelper.session_with_chosen_probe(
            unique_id=probe_uid,
            target_override="cortex_m",
            auto_unlock=False,
            connect_mode="under-reset",
            options={"smart_flash": False},
        ) as session:
            session.target.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)

            # --- STM32: DBGMCU_IDCODE at 0xE0042000 ---
            try:
                idcode = session.target.read32(0xE0042000)
                dev_id = idcode & 0xFFF
                rev_id = (idcode >> 16) & 0xFFFF

                if dev_id in _STM32_FAMILY:
                    # Read flash size (0x1FFFF7E0 for F0/F1/F3/L1, 0x1FFF7A22 for F2/F4/F7/H7)
                    flash_kb = _read_flash_size(session.target, dev_id)

                    target_name = _STM32_TARGETS.get((dev_id, flash_kb))
                    family = _STM32_FAMILY[dev_id]
                    flash_str = f"{flash_kb} KB" if flash_kb else "unknown size"
                    rev_str = f"REV=0x{rev_id:04X}"

                    if target_name:
                        msg = (
                            f"Detected: {family}, Flash {flash_str}, {rev_str} "
                            f"→ {target_name}"
                        )
                    else:
                        target_name = None
                        msg = (
                            f"Detected: {family}, Flash {flash_str}, {rev_str} "
                            f"(no exact match — select manually)"
                        )
                    return target_name, msg
            except Exception:
                pass  # not STM32 or register not accessible

            return None, "Could not identify target (vendor-specific registers not readable)"

    except Exception as exc:
        return None, f"Detection failed: {exc}"


def _read_flash_size(target, dev_id: int) -> Optional[int]:
    """Read flash size in KB from the appropriate register for the detected family."""
    # STM32F0/F1/F3/L1 — 0x1FFFF7E0
    # STM32F2/F4       — 0x1FFF7A22  (16-bit, word-aligned read → mask lower 16)
    # STM32F7/H7/G0/G4/L4 — 0x1FF0F442 or 0x1FFF75E0
    # Fallback: try known addresses in order until a sane value is read
    candidates = [0x1FFFF7E0, 0x1FFF7A22, 0x1FFF75E0, 0x1FF0F442]
    for addr in candidates:
        try:
            val = target.read16(addr)
            if 16 <= val <= 4096:  # sanity: 16 KB – 4 MB
                return val
        except Exception:
            continue
    return None
