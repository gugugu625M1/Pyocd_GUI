"""Firmware image descriptions and validation."""

from dataclasses import dataclass
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from intelhex import IntelHex, IntelHexError


def parse_address(text: str) -> int:
    value = int(text.strip(), 0)
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("Address must be between 0 and 0xFFFFFFFF.")
    return value


@dataclass(frozen=True)
class FirmwareImage:
    path: Path
    base_address: int | None = None


def image_ranges(image: FirmwareImage) -> list[tuple[int, int]]:
    suffix = image.path.suffix.lower()
    if suffix == ".bin":
        return [] if image.base_address is None else [
            (image.base_address, image.base_address + image.path.stat().st_size)
        ]
    try:
        if suffix == ".hex":
            ranges = IntelHex(str(image.path)).segments()
        else:
            with image.path.open("rb") as stream:
                elf = ELFFile(stream)
                ranges = [
                    (segment["p_paddr"], segment["p_paddr"] + segment["p_filesz"])
                    for segment in elf.iter_segments()
                    if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]
                ]
    except (IntelHexError, ELFError) as error:
        raise ValueError(f"Invalid firmware {image.path.name}: {error}") from error
    if not ranges:
        raise ValueError(f"Firmware has no loadable data: {image.path.name}")
    return ranges


def validate_images(images: tuple[FirmwareImage, ...]) -> None:
    if not images:
        raise ValueError("Select at least one firmware image.")
    ranges = []
    for image in images:
        if not image.path.is_file():
            raise ValueError(f"Firmware file not found: {image.path}")
        if image.path.suffix.lower() not in (".bin", ".hex", ".elf"):
            raise ValueError(f"Unsupported firmware format: {image.path.name}")
        size = image.path.stat().st_size
        if not size:
            raise ValueError(f"Firmware file is empty: {image.path.name}")
        if image.base_address is not None:
            if image.path.suffix.lower() != ".bin":
                raise ValueError("Only BIN files accept a base address; HEX/ELF contain addresses.")
            if not 0 <= image.base_address <= 0xFFFFFFFF or image.base_address + size > 0x100000000:
                raise ValueError(f"BIN address range is invalid: {image.path.name}")
        for start, end in image_ranges(image):
            if not 0 <= start < end <= 0x100000000:
                raise ValueError(f"Firmware address range is invalid: {image.path.name}")
            ranges.append((start, end, image.path.name))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"Firmware images overlap: {previous[2]} and {current[2]}")


def resolve_bin_addresses(images: tuple[FirmwareImage, ...], memory_map) -> tuple[FirmwareImage, ...]:
    resolved = []
    for image in images:
        if image.path.suffix.lower() == ".bin":
            address = image.base_address
            if address is None:
                boot = memory_map.get_boot_memory()
                if boot is None:
                    raise ValueError("Target has no boot memory; specify a BIN address.")
                address = boot.start
            region = memory_map.get_region_for_address(address)
            if region is None or not region.is_flash or address + image.path.stat().st_size > region.end + 1:
                raise ValueError(f"BIN must fit within a Flash region: {image.path.name}")
            image = FirmwareImage(image.path, address)
        resolved.append(image)
    result = tuple(resolved)
    validate_images(result)
    for image in result:
        for start, end in image_ranges(image):
            region = memory_map.get_region_for_address(start)
            if region is None or not (region.is_flash or region.is_ram) or end > region.end + 1:
                raise ValueError(f"Firmware segment does not fit target memory: {image.path.name}")
    return result