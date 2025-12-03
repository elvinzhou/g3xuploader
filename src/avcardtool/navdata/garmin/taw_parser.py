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


class TAWRegionType(IntEnum):
    """Known TAW region types and their purposes"""
    NAVIGATION = 0x01      # Navigation database (ldr_sys/avtn_db.bin)
    BASEMAP = 0x02         # Base map data
    TERRAIN_TDB = 0x21     # Terrain database (.tdb)
    TERRAIN_ODB = 0x22     # Terrain obstacles (terrain_9as.odb)
    TERRAIN_TRN = 0x23     # Terrain data (trn.dat)
    FCHARTS = 0x24         # FliteCharts data
    FCHARTS_INDEX = 0x25   # FliteCharts index
    OBSTACLES = 0x26       # Obstacle database
    TERRAIN_ODB2 = 0x27    # Secondary terrain obstacles
    SAFETAXI = 0x10        # SafeTaxi database
    CHARTVIEW = 0x11       # ChartView data
    FC_TPC = 0x14          # FliteCharts TPC (fc_tpc/fc_tpc.dat)
    RASTERS = 0x1A         # Raster data (rasters/rasters.xml)
    UNKNOWN_0C = 0x0C      # Unknown region type


# Map region types to output paths
TAW_REGION_PATHS: Dict[int, str] = {
    0x01: "ldr_sys/avtn_db.bin",
    0x02: "basemap.bin",
    0x10: "safetaxi.bin",
    0x11: "chartview.bin",
    0x14: "fc_tpc/fc_tpc.dat",
    0x1A: "rasters/rasters.xml",
    0x21: "terrain.tdb",
    0x22: "terrain_9as.odb",  # Note: jdmtool issue #57 correction
    0x23: "trn.dat",
    0x24: "FCharts.dat",
    0x25: "Fcharts.fca",
    0x26: "obstacles.odb",
    0x27: "terrain.odb",
}


# G3X-specific database file mappings
G3X_DATABASE_STRUCTURE = {
    "navdata": {
        "required": ["ldr_sys/avtn_db.bin"],
        "optional": [],
    },
    "terrain": {
        "required": ["terrain.tdb", "terrain_9as.odb"],
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
        "required": ["FCharts.dat", "Fcharts.fca"],
        "optional": ["fc_tpc/fc_tpc.dat", "rasters/rasters.xml"],
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


class TAWParser:
    """
    Parser for TAW and AWP files.
    
    TAW files are used by Garmin to distribute aviation databases.
    They contain multiple compressed regions, each representing a
    different database component.
    """
    
    # TAW magic bytes
    TAW_MAGIC = b'TAW\x00'
    AWP_MAGIC = b'AWP\x00'
    
    def __init__(self):
        self.debug = False
    
    def parse(self, filepath: Path) -> TAWFile:
        """
        Parse a TAW or AWP file.
        
        Args:
            filepath: Path to the TAW/AWP file
            
        Returns:
            Parsed TAWFile object
            
        Raises:
            TAWParseError: If the file cannot be parsed
        """
        logger.info(f"Parsing {filepath}")
        
        try:
            with open(filepath, 'rb') as f:
                # Read and validate magic
                magic = f.read(4)
                if magic not in (self.TAW_MAGIC, self.AWP_MAGIC):
                    raise TAWParseError(f"Invalid magic bytes: {magic.hex()}")
                
                # Parse header
                header = self._parse_header(f, magic)
                
                # Parse regions
                regions = self._parse_regions(f, header.num_regions)
                
                return TAWFile(
                    filepath=filepath,
                    header=header,
                    regions=regions
                )
                
        except TAWParseError:
            raise
        except Exception as e:
            raise TAWParseError(f"Failed to parse {filepath}: {e}")
    
    def _parse_header(self, f: BinaryIO, magic: bytes) -> TAWHeader:
        """Parse the TAW file header"""
        # Header format varies slightly between TAW versions
        # Common format:
        # - 4 bytes: magic
        # - 2 bytes: version
        # - 2 bytes: database type
        # - 1 byte: year (offset from 2000)
        # - 1 byte: cycle
        # - Variable: strings for avionics, coverage, type name
        # - 4 bytes: number of regions
        
        version, db_type = struct.unpack('<HH', f.read(4))
        year, cycle = struct.unpack('<BB', f.read(2))
        
        # Read null-terminated strings
        avionics = self._read_string(f)
        coverage = self._read_string(f)
        db_type_name = self._read_string(f)
        
        # Read number of regions
        num_regions = struct.unpack('<I', f.read(4))[0]
        
        header = TAWHeader(
            magic=magic,
            version=version,
            database_type=db_type,
            year=year,
            cycle=cycle,
            avionics=avionics,
            coverage=coverage,
            db_type_name=db_type_name,
            num_regions=num_regions,
        )
        
        logger.debug(f"Header: type={db_type}, cycle={header.cycle_string}, "
                    f"avionics='{avionics}', regions={num_regions}")
        
        return header
    
    def _read_string(self, f: BinaryIO) -> str:
        """Read a null-terminated string"""
        chars = []
        while True:
            c = f.read(1)
            if not c or c == b'\x00':
                break
            chars.append(c)
        return b''.join(chars).decode('utf-8', errors='replace')
    
    def _parse_regions(self, f: BinaryIO, num_regions: int) -> List[TAWRegion]:
        """Parse all regions in the TAW file"""
        regions = []
        
        # First pass: read region headers
        region_headers = []
        for i in range(num_regions):
            # Region header format:
            # - 4 bytes: region type
            # - 4 bytes: offset (from start of data section)
            # - 4 bytes: compressed size
            # - 4 bytes: uncompressed size
            # - Variable: destination path (null-terminated)
            
            region_type = struct.unpack('<I', f.read(4))[0]
            offset = struct.unpack('<I', f.read(4))[0]
            compressed_size = struct.unpack('<I', f.read(4))[0]
            uncompressed_size = struct.unpack('<I', f.read(4))[0]
            dest_path = self._read_string(f)
            
            region_headers.append((
                region_type, offset, compressed_size, 
                uncompressed_size, dest_path
            ))
            
            logger.debug(f"Region {i}: type=0x{region_type:02x}, "
                        f"compressed={compressed_size}, "
                        f"uncompressed={uncompressed_size}, "
                        f"path='{dest_path}'")
        
        # Record the start of data section
        data_start = f.tell()
        
        # Second pass: create region objects
        for region_type, offset, compressed_size, uncompressed_size, dest_path in region_headers:
            region = TAWRegion(
                region_type=region_type,
                offset=data_start + offset,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                dest_path=dest_path,
            )
            regions.append(region)
        
        return regions
    
    def extract_region(self, f: BinaryIO, region: TAWRegion) -> bytes:
        """
        Extract and decompress a single region.
        
        Args:
            f: Open file handle
            region: Region to extract
            
        Returns:
            Decompressed region data
        """
        f.seek(region.offset)
        compressed_data = f.read(region.compressed_size)
        
        if region.compressed_size == region.uncompressed_size:
            # Data is not compressed
            return compressed_data
        
        try:
            # Try zlib decompression
            return zlib.decompress(compressed_data)
        except zlib.error:
            # Try raw deflate (no header)
            try:
                return zlib.decompress(compressed_data, -15)
            except zlib.error as e:
                raise TAWParseError(f"Failed to decompress region 0x{region.region_type:02x}: {e}")


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
                output_path = self._get_output_path(
                    output_dir, region, preserve_paths
                )
                
                if output_path.exists() and not overwrite:
                    logger.warning(f"Skipping existing file: {output_path}")
                    continue
                
                # Create parent directories
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Extract and write data
                try:
                    data = self.parser.extract_region(f, region)
                    
                    with open(output_path, 'wb') as out:
                        out.write(data)
                    
                    logger.info(f"Extracted: {output_path} ({len(data)} bytes)")
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
