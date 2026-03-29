"""
Garmin feat_unlk.dat writer.

feat_unlk.dat is a copy-protection file on the SD card.  The G3X reads it to
verify that the installed databases were authorised for *this* device and *this*
card.  Each database type has a fixed 913-byte slot at a predetermined offset.

Structure of one slot (913 bytes total):
  CONTENT1  (85 bytes): magic, security ID, feature bit, encoded volume serial,
                        database file CRC, nav-db preview data, block CRC
  CONTENT2 (824 bytes): avionics system ID, padding, block CRC
  CHK3       (4 bytes): CRC over CONTENT1 + CONTENT2

Implementation is a Python port of the reverse-engineering work by jdmtool
(https://github.com/dimaryaz/jdmtool, MIT licence).  Credit: dimaryaz et al.
"""

from __future__ import annotations

import logging
import struct
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Optional

from avcardtool.navdata.garmin.taw_parser import _set_readonly, _clear_readonly

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (hard-coded in GrmNavdata.dll per jdmtool analysis)
# ---------------------------------------------------------------------------

CONTENT1_LEN = 0x55   # 85
CONTENT2_LEN = 0x338  # 824
SLOT_SIZE = CONTENT1_LEN + CONTENT2_LEN + 4  # 913

SEC_ID_OFFSET = 191
MAGIC1 = 0x0001
MAGIC2 = 0x7648329A   # little-endian bytes: 9A 32 48 76
MAGIC3 = 0x6501       # NAVIGATION feature only

CHUNK_SIZE = 0x8000

# Preview bytes copied verbatim from the nav-db file (NAVIGATION feature only)
NAVIGATION_PREVIEW_START = 129
NAVIGATION_PREVIEW_END   = 146   # 17 bytes


# ---------------------------------------------------------------------------
# CRC checksum (custom CRC-32 variant used by feat_unlk.dat / GrmNavdata.dll)
# ---------------------------------------------------------------------------

_FEAT_UNLK_P1 = 0x076DC419
_FEAT_UNLK_P2 = 0x77073096


def _make_table(polynomial: int, length: int) -> list[int]:
    table: list[int] = []
    for index in range(length):
        value = index << 24
        for _ in range(8):
            if value & (1 << 31):
                value = ((value << 1) & 0xFFFFFFFF) ^ polynomial
            else:
                value <<= 1
        table.append(value)
    return table


_LOOKUP = [x ^ y for x in _make_table(_FEAT_UNLK_P1, 64) for y in _make_table(_FEAT_UNLK_P2, 4)]


def feat_unlk_checksum(data: bytes, value: int = 0xFFFFFFFF) -> int:
    """CRC-32 variant used in feat_unlk.dat blocks."""
    for b in data:
        index = b ^ (value & 0xFF)
        value = _LOOKUP[index] ^ (value >> 8)
    return value


# ---------------------------------------------------------------------------
# Feature definitions (offset in file, feature bit, recognised filenames)
# ---------------------------------------------------------------------------

class Feature(Enum):
    """
    Each member is (file_offset, feature_bit, [filenames]).

    file_offset: byte offset of this feature's 913-byte slot within feat_unlk.dat
    feature_bit: feat_unlk stores (1 << bit) in the slot header
    filenames:   installed filenames that trigger this feature (relative to SD card root)
    """
    NAVIGATION   = (    0,  0, ['ldr_sys/avtn_db.bin', 'avtn_db.bin'])
    TERRAIN      = ( 1826,  3, ['terrain_9as.tdb', 'trn.dat'])
    OBSTACLE     = ( 2739,  4, ['terrain.odb', 'obstacle.odb'])
    APT_TERRAIN  = ( 3652,  5, ['terrain.adb'])
    SAFETAXI     = ( 5478,  7, ['safetaxi.bin', 'safetaxi.img'])
    FLITE_CHARTS = ( 6391,  8, ['fc_tpc/fc_tpc.dat', 'fc_tpc.dat',
                                'fc_tpc/fc_tpc.fca'])
    BASEMAP      = ( 7304, 10, ['bmap.bin'])
    AIRPORT_DIR  = ( 8217, 10, ['apt_dir.gca', 'fbo.gpi'])
    SECTIONALS   = (10956, 10, ['rasters/rasters.xml', 'rasters.xml',
                                'rasters/rasters.hif', 'rasters/rasters.hif'])

    def __init__(self, offset: int, bit: int, filenames: list):
        self.offset = offset
        self.bit = bit
        self.filenames = filenames


# Map relative SD-card path → Feature
FILENAME_TO_FEATURE: dict[str, Feature] = {
    fname: feature
    for feature in Feature
    for fname in feature.filenames
}


# ---------------------------------------------------------------------------
# Volume ID encoding / system ID truncation
# ---------------------------------------------------------------------------

def encode_volume_id(vol_id: int) -> int:
    """
    Encode the FAT32 volume serial for storage in feat_unlk.dat.

    Encoding:  ~( (vol_id << 31) | (vol_id >> 1) )  (all 32-bit)

    Example: vol_id=0x64306664  →  encoded=0xCDE7CCCD
    This is the value stored in every feat_unlk.dat slot AND in .evidf.dat.
    """
    return (~((vol_id << 31 & 0xFFFFFFFF) | (vol_id >> 1))) & 0xFFFFFFFF


def truncate_system_id(system_id: int) -> int:
    """
    Truncate a 64-bit avionics system ID to 32 bits for CONTENT2.

    The G3X compares truncate_system_id(own_hardware_id) against this value.
    """
    return ((system_id & 0xFFFFFFFF) + (system_id >> 32)) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# File checksum helpers
# ---------------------------------------------------------------------------

def read_file_checksum(feature: Feature, filepath: Path) -> tuple[int, Optional[bytes]]:
    """
    Read the database CRC embedded at the end of a Garmin database file.

    Garmin's database files end with their own CRC-32 (feat_unlk variant).
    Processing the entire file with feat_unlk_checksum should yield 0 if the
    file is intact.  The CRC value itself is the last 4 bytes.

    Returns:
        (checksum, preview) where preview is 17 bytes from NAVIGATION files.

    Raises:
        ValueError: if the file does not pass its embedded checksum.
    """
    chk = 0xFFFFFFFF
    preview = b'\x00' * (NAVIGATION_PREVIEW_END - NAVIGATION_PREVIEW_START)

    with open(filepath, 'rb') as fd:
        block = fd.read(CHUNK_SIZE)
        if feature == Feature.NAVIGATION and len(block) >= NAVIGATION_PREVIEW_END:
            preview = block[NAVIGATION_PREVIEW_START:NAVIGATION_PREVIEW_END]
        while True:
            chk = feat_unlk_checksum(block, chk)
            next_block = fd.read(CHUNK_SIZE)
            if not next_block:
                break
            block = next_block

    if chk != 0:
        raise ValueError(
            f"{filepath.name}: embedded checksum mismatch "
            f"(got 0x{chk:08X}, expected 0x00000000)"
        )

    file_crc = int.from_bytes(block[-4:], 'little')
    return file_crc, preview


# ---------------------------------------------------------------------------
# Core writer
# ---------------------------------------------------------------------------

def update_feat_unlk(
    dest_dir: Path,
    feature: Feature,
    vol_id: int,
    security_id: int,
    system_id: int,
    checksum: int,
    preview: Optional[bytes] = None,
) -> None:
    """
    Write (or update) one feature's 913-byte slot in feat_unlk.dat.

    Args:
        dest_dir:    SD card root directory (feat_unlk.dat lives here).
        feature:     Which database type to update.
        vol_id:      FAT32 volume serial of the SD card (raw, not encoded).
        security_id: Garmin device-type code from the TAW header (database_type).
        system_id:   Avionics hardware ID for this device (from API systemId).
        checksum:    CRC-32 read from the last 4 bytes of the database file.
        preview:     17 preview bytes from avtn_db.bin (NAVIGATION only).
    """
    preview_len = NAVIGATION_PREVIEW_END - NAVIGATION_PREVIEW_START  # 17

    # --- CONTENT1 ---
    content1 = BytesIO()
    content1.write(MAGIC1.to_bytes(2, 'little'))
    content1.write(((security_id - SEC_ID_OFFSET + 0x10000) & 0xFFFF).to_bytes(2, 'little'))
    content1.write(MAGIC2.to_bytes(4, 'little'))
    content1.write((1 << feature.bit).to_bytes(4, 'little'))
    content1.write((0).to_bytes(4, 'little'))                        # reserved
    content1.write(encode_volume_id(vol_id).to_bytes(4, 'little'))  # offset 16

    if feature == Feature.NAVIGATION:
        content1.write(MAGIC3.to_bytes(2, 'little'))

    content1.write(checksum.to_bytes(4, 'little'))

    if feature == Feature.NAVIGATION and preview and len(preview) >= preview_len:
        content1.write(preview[:preview_len])
    else:
        content1.write(b'\x00' * preview_len)

    # Pad to (CONTENT1_LEN - 4) then append block CRC
    pad_len = CONTENT1_LEN - len(content1.getbuffer()) - 4
    if pad_len > 0:
        content1.write(b'\x00' * pad_len)

    chk1 = feat_unlk_checksum(bytes(content1.getbuffer()))
    content1.write(chk1.to_bytes(4, 'little'))

    # --- CONTENT2 ---
    content2 = BytesIO()
    content2.write((0).to_bytes(4, 'little'))                                # unit_count = 0
    content2.write(truncate_system_id(system_id).to_bytes(4, 'little'))     # avionics ID

    pad_len2 = CONTENT2_LEN - len(content2.getbuffer()) - 4
    if pad_len2 > 0:
        content2.write(b'\x00' * pad_len2)

    chk2 = feat_unlk_checksum(bytes(content2.getbuffer()))
    content2.write(chk2.to_bytes(4, 'little'))

    # --- Overall CRC ---
    chk3 = feat_unlk_checksum(content1.getvalue() + content2.getvalue())

    # --- Write to file at feature's fixed offset ---
    feat_unlk_path = dest_dir / 'feat_unlk.dat'

    # Clear read-only attribute before writing (FAT32 + non-FAT)
    if feat_unlk_path.exists():
        _clear_readonly(feat_unlk_path)

    # Ensure file exists (open in append mode so it doesn't truncate)
    with open(feat_unlk_path, 'ab'):
        pass

    with open(feat_unlk_path, 'r+b') as out:
        out.seek(feature.offset)
        out.write(content1.getbuffer())
        out.write(content2.getbuffer())
        out.write(chk3.to_bytes(4, 'little'))

    # Match JDM: make read-only after writing (FAT32 + non-FAT)
    _set_readonly(feat_unlk_path)


def write_feat_unlk_for_file(
    dest_dir: Path,
    installed_file: Path,
    vol_id: int,
    security_id: int,
    system_id: int,
) -> bool:
    """
    High-level helper: look up the Feature for *installed_file*, compute its
    embedded CRC, and write the feat_unlk.dat slot.

    Args:
        dest_dir:       SD card root (feat_unlk.dat lives here).
        installed_file: Absolute path to the file on the SD card.
        vol_id:         FAT32 volume serial (raw integer).
        security_id:    database_type from the TAW file header.
        system_id:      avionics hardware ID.

    Returns:
        True if the slot was written, False if skipped (unknown file or error).
    """
    # Compute the path relative to dest_dir for the FILENAME_TO_FEATURE lookup
    try:
        rel = installed_file.relative_to(dest_dir)
    except ValueError:
        rel = Path(installed_file.name)

    rel_str = str(rel).replace('\\', '/')
    feature = FILENAME_TO_FEATURE.get(rel_str)
    if feature is None:
        logger.debug(f"feat_unlk: no feature for {rel_str}, skipping")
        return False

    try:
        checksum, preview = read_file_checksum(feature, installed_file)
    except ValueError as e:
        logger.warning(f"feat_unlk checksum warning for {rel_str}: {e} — writing with available CRC")
        # Fall back: read last 4 bytes directly without full-file CRC check
        with open(installed_file, 'rb') as f:
            f.seek(-4, 2)
            checksum = struct.unpack('<I', f.read(4))[0]
        preview = None
    except Exception as e:
        logger.warning(f"feat_unlk: could not process {rel_str}: {e}, skipping")
        return False

    try:
        update_feat_unlk(dest_dir, feature, vol_id, security_id, system_id, checksum, preview)
        logger.info(f"feat_unlk: wrote slot for {feature.name} ({rel_str})")
        return True
    except Exception as e:
        logger.warning(f"feat_unlk: failed to write slot for {feature.name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Volume ID helpers
# ---------------------------------------------------------------------------

def get_vol_id_from_sd_card(sd_card_path: Path) -> Optional[int]:
    """
    Determine the FAT32 volume serial for the SD card mounted at *sd_card_path*.

    Strategy:
      1. findmnt → block device path
      2. blkid → UUID string (e.g. "6430-6664")
      3. Convert XXXX-XXXX hex string to integer

    Returns the vol_id as an integer, or None if it cannot be determined.
    """
    import subprocess

    # Step 1: find block device
    device = None
    try:
        r = subprocess.run(
            ['findmnt', '-n', '-o', 'SOURCE', '--target', str(sd_card_path)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            device = r.stdout.strip()
    except Exception:
        pass

    if not device:
        try:
            # Fallback: search /proc/mounts
            with open('/proc/mounts') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == str(sd_card_path):
                        device = parts[0]
                        break
        except Exception:
            pass

    if not device:
        return None

    # Step 2: get UUID via blkid
    uuid_str = None
    for cmd in [
        ['blkid', '-s', 'UUID', '-o', 'value', device],
        ['lsblk', '-d', '-o', 'UUID', '-n', device],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                uuid_str = r.stdout.strip()
                break
        except Exception:
            continue

    if not uuid_str:
        return None

    # Step 3: convert "XXXX-XXXX" or "XXXXXXXX" to int
    try:
        return int(uuid_str.replace('-', ''), 16)
    except ValueError:
        return None


def vol_id_from_card_serial(card_serial: str) -> Optional[int]:
    """
    Convert a card_serial string (as stored in the download manifest or
    returned by SDCardDetector) to a raw vol_id integer.

    Accepts "XXXX-XXXX", "XXXXXXXX", or a plain decimal integer string.
    """
    if not card_serial or card_serial == "0":
        return None
    # Try hex (with or without hyphen)
    try:
        return int(card_serial.replace('-', ''), 16)
    except ValueError:
        pass
    # Try decimal
    try:
        return int(card_serial)
    except ValueError:
        return None
