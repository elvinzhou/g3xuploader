#!/usr/bin/env python3
"""
TAW/AWP File Parser and Extractor

Parses Garmin TAW (Transfer Archive for Windows) and AWP (Aviation Waypoint)
files and extracts their contents for writing to SD cards.

This implementation is inspired by the jdmtool TAW extractor.

TAW File Format:
- Header containing database type, version, and metadata
- Multiple regions, each containing specific database components
- Each region has a type ID, compressed data, and destination path
"""

import struct
import zlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, BinaryIO, Iterator, Tuple
from enum import IntEnum

logger = logging.getLogger(__name__)


def _set_hidden(path: Path) -> None:
    """
    Set the FAT hidden attribute on a file to match Garmin's own output.

    On Linux with a FAT filesystem, uses `fatattr +h`.  Silently skips
    if fatattr is not installed — the G3X reads files regardless of the
    hidden attribute; this just keeps the card visually clean on Windows.
    """
    import subprocess
    try:
        subprocess.run(["fatattr", "+h", str(path)],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


class TAWRegionType(IntEnum):
    """Known TAW region types and their purposes"""
    NAVIGATION = 0x01      # Navigation database (ldr_sys/avtn_db.bin)
    BASEMAP = 0x02         # Base map data
    BASEMAP_2 = 0x03       # Basemap variant
    BASEMAP_3 = 0x06       # Basemap variant
    SAFETAXI = 0x0A        # SafeTaxi database (safetaxi.bin) — confirmed from real TAW
    SAFETAXI_IMG = 0x0B    # SafeTaxi image variant (safetaxi.img)
    UNKNOWN_0C = 0x0C      # Unknown (183 KB, appears in nav files — not extracted)
    FC_TPC = 0x14          # FliteCharts TPC (fc_tpc/fc_tpc.dat)
    RASTERS = 0x1A         # Raster data (rasters/rasters.hif)
    TERRAIN_TDB = 0x22     # Terrain tile database (terrain_9as.tdb)
    TERRAIN_TRN = 0x23     # Terrain data (trn.dat)
    FCHARTS = 0x24         # FliteCharts data (FCharts.dat)
    FCHARTS_INDEX = 0x25   # FliteCharts additional data (fc_tpc/fc_tpc.fca)
    OBSTACLES = 0x26       # Obstacle database (obstacles.odb)
    TERRAIN_ODB = 0x27     # Obstacle terrain database (terrain.odb) — G3X Touch
    AIRPORT_DIR = 0x4C     # Airport Directory (fbo.gpi)


# Map region types to output paths on the SD card.
# Confirmed from real TAW files (parsed from fly.garmin.com downloads).
TAW_REGION_PATHS: Dict[int, str] = {
    0x01: "ldr_sys/avtn_db.bin",
    0x02: "basemap.bin",
    0x0A: "safetaxi.bin",      # confirmed from sg3xt-us-26S2.taw (G3X Touch)
    0x0B: "safetaxi.img",      # SafeTaxi for other avionics units
    0x14: "fc_tpc/fc_tpc.dat",
    0x1A: "rasters/rasters.xml",  # confirmed: 41,067 bytes matches GADM rasters.xml
    0x22: "terrain_9as.tdb",   # confirmed from tg3xt-ww-20T1.taw
    0x23: "trn.dat",
    0x24: "fc_tpc/fc_tpc.dat",  # confirmed: 3,097,678 bytes matches GADM output
    0x25: "fc_tpc/fc_tpc.fca",
    0x26: "obstacles.odb",
    0x27: "terrain.odb",       # confirmed from bg3xt-us-26B2.taw (G3X Touch obstacles)
    0x4C: "fbo.gpi",           # confirmed from dg3xt-us-26D2.taw (Airport Directory)
}

# Region types that appear in TAW files but are NOT extracted to the SD card.
# 0x0c: fixed-size (183,808 bytes) certificate/signature block in nav files.
SKIP_REGIONS: set = {0x0C}

# Files Garmin sets as hidden on the SD card (FAT hidden attribute).
# On Linux/FAT32 these can be hidden with: fatattr +h <file>
# The G3X reads them regardless of the attribute; hiding matches
# what Garmin's desktop software produces.
HIDDEN_PATHS = {
    "ldr_sys/avtn_db.bin",
    "safetaxi.bin",
    "terrain.odb",
    "terrain_9as.tdb",
    "trn.dat",
    "obstacles.odb",
    "fc_tpc/fc_tpc.dat",
    "fc_tpc/fc_tpc.fca",
    "feat_unlk.dat",
    "fbo.gpi",
    ".evidf.dat",
    ".gadm.meta",
}


# G3X-specific database file mappings (used for post-install verification)
G3X_DATABASE_STRUCTURE = {
    "navdata": {
        "required": ["ldr_sys/avtn_db.bin"],
        "optional": [],
    },
    "terrain": {
        "required": ["terrain.odb", "terrain_9as.tdb"],
        "optional": ["trn.dat"],
    },
    "obstacles": {
        "required": ["obstacles.odb"],
        "optional": [],
    },
    "safetaxi": {
        "required": ["safetaxi.bin"],
        "optional": [],
    },
    "flitecharts": {
        "required": ["FCharts.dat"],
        "optional": ["fc_tpc/fc_tpc.dat", "fc_tpc/fc_tpc.fca"],
    },
    "chartview": {
        "required": ["chartview.bin"],
        "optional": [],
    },
}


@dataclass
class TAWHeader:
    """TAW file header information"""
    magic: bytes
    version: int
    database_type: int
    year: int
    cycle: int
    avionics: str
    coverage: str
    db_type_name: str
    num_regions: int
    
    @property
    def cycle_string(self) -> str:
        """Get human-readable cycle string (e.g., '2413' for 2024 cycle 13)"""
        return f"{self.year:02d}{self.cycle:02d}"


@dataclass
class TAWRegion:
    """A single region within a TAW file"""
    region_type: int
    offset: int
    compressed_size: int
    uncompressed_size: int
    dest_path: str
    data: Optional[bytes] = None
    
    @property
    def type_name(self) -> str:
        """Get human-readable region type name"""
        try:
            return TAWRegionType(self.region_type).name
        except ValueError:
            return f"UNKNOWN_{self.region_type:02x}"
    
    @property
    def output_path(self) -> str:
        """Get the output path for this region"""
        if self.dest_path:
            return self.dest_path
        return TAW_REGION_PATHS.get(self.region_type, f"region_{self.region_type:02x}.bin")


@dataclass
class TAWFile:
    """Parsed TAW/AWP file"""
    filepath: Path
    header: TAWHeader
    regions: List[TAWRegion] = field(default_factory=list)
    
    def get_region(self, region_type: int) -> Optional[TAWRegion]:
        """Get a specific region by type"""
        for region in self.regions:
            if region.region_type == region_type:
                return region
        return None
    
    def get_regions_by_types(self, types: List[int]) -> List[TAWRegion]:
        """Get multiple regions by types"""
        return [r for r in self.regions if r.region_type in types]


class TAWParseError(Exception):
    """Raised when TAW file parsing fails"""
    pass


# ---------------------------------------------------------------------------
# Actual TAW binary format (reverse-engineered by jdmtool)
#
# File layout:
#   [5]  magic:     b'wAt.d' or b'pWa.d'
#   [15] separator: TAW_SEPARATOR
#   [25] sqa1:      null-delimited strings (ignored)
#   [4]  meta_len:  LE uint32 — length of metadata block
#   [1]  'F':       section marker
#   [N]  metadata:  database_type (2 B LE), year, cycle, null-term strings
#   [4]  remaining: skip
#   [1]  'R':       section marker
#   [5]  KpGrd:     second magic
#   [15] separator: TAW_SEPARATOR again
#   [25] sqa2:      null-delimited strings (ignored)
#
# Sections follow, each:
#   [4]  sect_size: LE uint32 (total section size)
#   [1]  type:      b'R' = region data, b'S' = stop
#   [2]  region:    LE uint16 (index into TAW_REGION_PATHS)
#   [4]  unknown:   LE uint32 (ignored)
#   [4]  data_size: LE uint32
#   [N]  data:      data_size bytes (raw or zlib-compressed)
# ---------------------------------------------------------------------------

_TAW_MAGIC_BYTES = (b'wAt.d', b'pWa.d')
_TAW_SEPARATOR = b'\x00\x02\x00\x00\x00Dd\x00\x1b\x00\x00\x00A\xc8\x00'  # 15 bytes
_TAW_MAGIC2 = b'KpGrd'   # 5 bytes, second magic inside header


class TAWParser:
    """
    Parser for Garmin TAW (Transfer Archive for Windows) files.

    Handles the binary format used by flyGarmin (magic: wAt.d / pWa.d).
    Format reverse-engineered by jdmtool (https://github.com/dimaryaz/jdmtool).
    """

    def __init__(self):
        pass

    def parse(self, filepath: Path) -> TAWFile:
        """
        Parse a TAW file.

        Args:
            filepath: Path to the TAW file

        Returns:
            Parsed TAWFile object

        Raises:
            TAWParseError: If the file cannot be parsed
        """
        logger.info(f"Parsing {filepath}")

        try:
            with open(filepath, 'rb') as f:
                magic, header = self._parse_header(f)
                regions = self._parse_regions(f)

                return TAWFile(
                    filepath=filepath,
                    header=header,
                    regions=regions,
                )

        except TAWParseError:
            raise
        except Exception as e:
            raise TAWParseError(f"Failed to parse {filepath}: {e}")

    def _parse_header(self, f: BinaryIO) -> tuple:
        """Parse the TAW file header; returns (magic_bytes, TAWHeader)."""
        # ---- 5-byte magic ----
        magic = f.read(5)
        if magic not in _TAW_MAGIC_BYTES:
            raise TAWParseError(f"Invalid magic bytes: {magic.hex()}")

        # ---- separator ----
        sep = f.read(len(_TAW_SEPARATOR))
        if sep != _TAW_SEPARATOR:
            raise TAWParseError(f"Unexpected separator: {sep.hex()}")

        # ---- sqa1 (25 bytes, ignored) ----
        f.read(25)

        # ---- metadata block ----
        meta_len = struct.unpack('<I', f.read(4))[0]
        section_type = f.read(1)
        if section_type != b'F':
            raise TAWParseError(f"Expected 'F' section, got {section_type!r}")
        metadata = f.read(meta_len)
        f.read(4)  # remaining field (skip)

        # ---- second header block ----
        section_type = f.read(1)
        if section_type != b'R':
            raise TAWParseError(f"Expected 'R' section, got {section_type!r}")
        magic2 = f.read(5)
        if magic2 != _TAW_MAGIC2:
            raise TAWParseError(f"Expected KpGrd, got {magic2!r}")
        sep2 = f.read(len(_TAW_SEPARATOR))
        if sep2 != _TAW_SEPARATOR:
            raise TAWParseError(f"Unexpected second separator: {sep2.hex()}")
        f.read(25)  # sqa2 (ignored)

        # ---- parse metadata ----
        header = self._parse_metadata(magic, metadata)
        return magic, header

    def _parse_metadata(self, magic: bytes, metadata: bytes) -> TAWHeader:
        """Decode the metadata block into a TAWHeader."""
        db_type = struct.unpack('<H', metadata[:2])[0]  # security_id / database_type

        # Two metadata layouts depending on metadata[2]
        if metadata[2] == 0x00:
            year  = metadata[8]
            cycle = metadata[12]
            text  = metadata[16:]
        else:
            year  = metadata[4]
            cycle = metadata[6]
            text  = metadata[8:]

        parts = text.split(b'\x00')
        avionics   = parts[0].decode('utf-8', errors='replace') if len(parts) > 0 else ''
        coverage   = parts[1].decode('utf-8', errors='replace') if len(parts) > 1 else ''
        db_type_nm = parts[2].decode('utf-8', errors='replace') if len(parts) > 2 else ''

        header = TAWHeader(
            magic=magic,
            version=0,        # not in this format; kept for API compatibility
            database_type=db_type,
            year=year,
            cycle=cycle,
            avionics=avionics,
            coverage=coverage,
            db_type_name=db_type_nm,
            num_regions=0,    # filled in after reading sections
        )

        logger.debug(
            f"Metadata: db_type=0x{db_type:04X} year={year} cycle={cycle} "
            f"avionics='{avionics}' coverage='{coverage}' type='{db_type_nm}'"
        )
        return header

    def _parse_regions(self, f: BinaryIO) -> List[TAWRegion]:
        """Read all region sections sequentially until the 'S' stop marker."""
        regions: List[TAWRegion] = []

        while True:
            size_bytes = f.read(4)
            if len(size_bytes) < 4:
                break  # EOF

            sect_size   = struct.unpack('<I', size_bytes)[0]
            sect_type   = f.read(1)

            if sect_type == b'S':
                break   # stop sentinel

            if sect_type != b'R':
                raise TAWParseError(f"Unexpected section type: {sect_type!r}")

            region_id   = struct.unpack('<H', f.read(2))[0]
            _unknown    = f.read(4)
            data_size   = struct.unpack('<I', f.read(4))[0]
            data_start  = f.tell()

            dest_path = TAW_REGION_PATHS.get(region_id, f"region_{region_id:02x}.bin")

            region = TAWRegion(
                region_type=region_id,
                offset=data_start,           # absolute file offset of raw data
                compressed_size=data_size,   # we call this "compressed" for the extractor
                uncompressed_size=data_size, # updated after decompression attempt
                dest_path=dest_path,
            )
            regions.append(region)

            logger.debug(
                f"Region 0x{region_id:02x}: {data_size} bytes at offset {data_start}"
                f" → {dest_path}"
            )

            f.seek(data_start + data_size)

        return regions


    def extract_region(self, f: BinaryIO, region: TAWRegion) -> bytes:
        """
        Extract a single region's data.

        Section data in flyGarmin TAW files is typically stored raw
        (uncompressed).  A zlib fallback is tried in case a section uses
        deflate compression.

        Args:
            f: Open file handle positioned at the start of the TAW file
            region: Region to extract (offset is absolute file position)

        Returns:
            Raw region bytes ready to write to the SD card
        """
        f.seek(region.offset)
        data = f.read(region.compressed_size)

        # Try zlib decompression (deflate with zlib header, then raw deflate).
        # If neither works, the data is raw — return as-is.
        try:
            return zlib.decompress(data)
        except zlib.error:
            pass
        try:
            return zlib.decompress(data, -15)
        except zlib.error:
            pass

        return data


class TAWExtractor:
    """
    Extracts TAW file contents to SD card directory structure.
    
    This class handles the extraction of TAW files and creates
    the proper directory structure expected by G3X devices.
    """
    
    def __init__(self, parser: Optional[TAWParser] = None):
        self.parser = parser or TAWParser()
    
    def extract_to_directory(
        self, 
        taw_file: Path, 
        output_dir: Path,
        preserve_paths: bool = True,
        overwrite: bool = False
    ) -> List[Path]:
        """
        Extract TAW file contents to a directory.
        
        Args:
            taw_file: Path to TAW/AWP file
            output_dir: Output directory (typically SD card mount point)
            preserve_paths: If True, maintain subdirectory structure
            overwrite: If True, overwrite existing files
            
        Returns:
            List of extracted file paths
        """
        logger.info(f"Extracting {taw_file} to {output_dir}")
        
        parsed = self.parser.parse(taw_file)
        extracted_files = []
        
        with open(taw_file, 'rb') as f:
            for region in parsed.regions:
                if region.region_type in SKIP_REGIONS:
                    logger.debug(f"Skipping region 0x{region.region_type:02x} (not for SD card)")
                    continue

                # Skip regions with no known destination (unknown avionics type or
                # region for a different device sharing the same subscription).
                if region.region_type not in TAW_REGION_PATHS:
                    logger.info(
                        f"Skipping unknown region 0x{region.region_type:02x} "
                        f"({region.compressed_size:,} bytes) — not a G3X destination"
                    )
                    continue

                output_path = self._get_output_path(
                    output_dir, region, preserve_paths
                )

                if output_path.exists() and not overwrite:
                    logger.warning(f"Skipping existing file: {output_path}")
                    extracted_files.append(output_path)
                    continue
                
                # Create parent directories
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Extract and write data
                try:
                    data = self.parser.extract_region(f, region)

                    with open(output_path, 'wb') as out:
                        out.write(data)

                    # Clear data from memory
                    del data

                    # Set FAT hidden attribute to match what Garmin's own client
                    # produces. Uses fatattr on Linux if available; silent on failure.
                    _set_hidden(output_path)

                    logger.info(f"Extracted: {output_path} ({region.uncompressed_size} bytes)")
                    extracted_files.append(output_path)

                except TAWParseError as e:
                    logger.error(f"Failed to extract region 0x{region.region_type:02x}: {e}")
        
        return extracted_files
    
    def _get_output_path(
        self, 
        output_dir: Path, 
        region: TAWRegion,
        preserve_paths: bool
    ) -> Path:
        """Determine the output path for a region"""
        if preserve_paths and region.dest_path:
            # Use the full path from the region
            return output_dir / region.dest_path
        elif region.dest_path:
            # Use just the filename
            return output_dir / Path(region.dest_path).name
        else:
            # Use default path based on region type
            default_path = TAW_REGION_PATHS.get(
                region.region_type, 
                f"region_{region.region_type:02x}.bin"
            )
            if preserve_paths:
                return output_dir / default_path
            else:
                return output_dir / Path(default_path).name
    
    def list_contents(self, taw_file: Path) -> TAWFile:
        """
        List contents of a TAW file without extracting.
        
        Args:
            taw_file: Path to TAW/AWP file
            
        Returns:
            Parsed TAWFile object with region information
        """
        return self.parser.parse(taw_file)
    
    def extract_single_region(
        self,
        taw_file: Path,
        region_type: int,
        output_file: Path
    ) -> bool:
        """
        Extract a single region from a TAW file.
        
        Args:
            taw_file: Path to TAW/AWP file
            region_type: Type of region to extract
            output_file: Output file path
            
        Returns:
            True if extraction successful
        """
        parsed = self.parser.parse(taw_file)
        region = parsed.get_region(region_type)
        
        if not region:
            logger.error(f"Region type 0x{region_type:02x} not found")
            return False
        
        with open(taw_file, 'rb') as f:
            data = self.parser.extract_region(f, region)
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'wb') as out:
            out.write(data)
        
        logger.info(f"Extracted region 0x{region_type:02x} to {output_file}")
        return True


def print_taw_info(taw_file: Path):
    """Print information about a TAW file"""
    parser = TAWParser()
    parsed = parser.parse(taw_file)
    
    print(f"File: {taw_file}")
    print(f"Database type: {parsed.header.database_type}")
    print(f"Year: {parsed.header.year}")
    print(f"Cycle: {parsed.header.cycle}")
    print(f"Avionics: '{parsed.header.avionics}'")
    print(f"Coverage: '{parsed.header.coverage}'")
    print(f"Type: '{parsed.header.db_type_name}'")
    print(f"Regions: {len(parsed.regions)}")
    print()
    
    for i, region in enumerate(parsed.regions):
        print(f"  Region {i}:")
        print(f"    Type: 0x{region.region_type:02x} ({region.type_name})")
        print(f"    Compressed: {region.compressed_size} bytes")
        print(f"    Uncompressed: {region.uncompressed_size} bytes")
        print(f"    Path: {region.output_path}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <taw_file>")
        sys.exit(1)
    
    logging.basicConfig(level=logging.INFO)
    print_taw_info(Path(sys.argv[1]))
