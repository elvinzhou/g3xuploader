#!/usr/bin/env python3
"""
G3X SD Card Database Writer

Handles automatic detection of SD cards and writing of aviation databases
in the format expected by G3X Touch systems.

This module can:
- Detect SD cards via udev events or manual scanning
- Validate SD card format and capacity
- Write database files in the correct directory structure
- Create necessary metadata files
- Verify written data integrity
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from datetime import datetime
import struct

logger = logging.getLogger(__name__)


# G3X SD Card requirements
MIN_SD_CARD_SIZE_GB = 2
MAX_SD_CARD_SIZE_GB = 32  # FAT32 limitation
RECOMMENDED_SD_SIZE_GB = 8

# G3X database directory structure
G3X_DB_DIRS = [
    "ldr_sys",      # Navigation database
    "fc_tpc",       # FliteCharts TPC
    "rasters",      # Raster data
    "chartview",    # ChartView charts
    "safetaxi",     # SafeTaxi data
]

# Files that indicate a G3X SD card
G3X_INDICATOR_FILES = [
    "GarminDevice.xml",
    "ldr_sys/avtn_db.bin",
]

# Metadata file for tracking database versions
DB_METADATA_FILE = ".g3x_db_metadata.json"


@dataclass
class SDCardInfo:
    """Information about a detected SD card"""
    device_path: str          # e.g., /dev/sda1
    mount_point: Optional[str]
    label: str
    filesystem: str
    size_bytes: int
    free_bytes: int
    volume_id: str            # FAT32 volume ID
    is_garmin: bool           # Has G3X indicator files
    
    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)
    
    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024 ** 3)
    
    def is_suitable(self) -> Tuple[bool, str]:
        """Check if the SD card is suitable for G3X databases"""
        if self.filesystem.lower() not in ('vfat', 'fat32', 'fat'):
            return False, f"Filesystem must be FAT32, got {self.filesystem}"
        
        if self.size_gb < MIN_SD_CARD_SIZE_GB:
            return False, f"Card too small ({self.size_gb:.1f}GB < {MIN_SD_CARD_SIZE_GB}GB)"
        
        if self.size_gb > MAX_SD_CARD_SIZE_GB:
            return False, f"Card too large ({self.size_gb:.1f}GB > {MAX_SD_CARD_SIZE_GB}GB)"
        
        return True, "OK"


@dataclass
class DatabaseVersion:
    """Version information for an installed database"""
    db_type: str
    version: str
    cycle: str
    install_date: str
    source_file: str
    checksum: str


@dataclass
class WriteResult:
    """Result of a database write operation"""
    success: bool
    files_written: List[str]
    bytes_written: int
    duration_seconds: float
    errors: List[str]


class SDCardError(Exception):
    """Base exception for SD card operations"""
    pass


class SDCardNotFoundError(SDCardError):
    """Raised when no suitable SD card is found"""
    pass


class SDCardFormatError(SDCardError):
    """Raised when SD card has invalid format"""
    pass


class SDCardWriteError(SDCardError):
    """Raised when writing to SD card fails"""
    pass


class SDCardDetector:
    """
    Detects and manages SD cards suitable for G3X databases.
    
    Uses /proc/mounts and lsblk to find mounted SD cards,
    or can receive udev events for automatic detection.
    """
    
    def __init__(self):
        self._cached_cards: Dict[str, SDCardInfo] = {}
    
    def scan_for_cards(self) -> List[SDCardInfo]:
        """
        Scan the system for mounted SD cards.
        
        Returns:
            List of detected SD cards
        """
        logger.info("Scanning for SD cards...")
        cards = []
        
        # Use lsblk to find removable devices
        try:
            result = subprocess.run(
                ['lsblk', '-J', '-o', 'NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL,RM,TYPE,FSAVAIL'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.warning(f"lsblk failed: {result.stderr}")
                return cards
            
            data = json.loads(result.stdout)
            
            for device in data.get('blockdevices', []):
                # Look for removable devices
                if device.get('rm') and device.get('type') == 'disk':
                    # Check partitions
                    for child in device.get('children', []):
                        card = self._parse_device(child, device['name'])
                        if card:
                            cards.append(card)
                            
        except subprocess.TimeoutExpired:
            logger.error("lsblk timed out")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse lsblk output: {e}")
        except FileNotFoundError:
            logger.error("lsblk not found - using fallback detection")
            cards = self._fallback_scan()
        
        logger.info(f"Found {len(cards)} SD card(s)")
        return cards
    
    def _parse_device(self, device: Dict, parent: str) -> Optional[SDCardInfo]:
        """Parse a device entry from lsblk"""
        mount_point = device.get('mountpoint')
        fstype = device.get('fstype', '')
        
        # Only consider FAT filesystems
        if not fstype or fstype.lower() not in ('vfat', 'fat32', 'fat'):
            return None
        
        device_path = f"/dev/{device['name']}"
        
        # Get size
        size_str = device.get('size', '0')
        size_bytes = self._parse_size(size_str)
        
        # Get free space
        free_str = device.get('fsavail', '0')
        free_bytes = self._parse_size(free_str) if free_str else 0
        
        # Get volume ID
        volume_id = self._get_volume_id(device_path)
        
        # Check for Garmin files
        is_garmin = False
        if mount_point:
            is_garmin = self._check_garmin_files(Path(mount_point))
        
        return SDCardInfo(
            device_path=device_path,
            mount_point=mount_point,
            label=device.get('label', ''),
            filesystem=fstype,
            size_bytes=size_bytes,
            free_bytes=free_bytes,
            volume_id=volume_id,
            is_garmin=is_garmin,
        )
    
    def _parse_size(self, size_str: str) -> int:
        """Parse a size string (e.g., '8G', '512M') to bytes"""
        if not size_str:
            return 0
        
        multipliers = {
            'B': 1,
            'K': 1024,
            'M': 1024 ** 2,
            'G': 1024 ** 3,
            'T': 1024 ** 4,
        }
        
        size_str = size_str.strip().upper()
        
        for suffix, mult in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    return int(float(size_str[:-1]) * mult)
                except ValueError:
                    return 0
        
        try:
            return int(size_str)
        except ValueError:
            return 0
    
    def _get_volume_id(self, device_path: str) -> str:
        """Get the FAT32 volume ID for a device"""
        try:
            # Try using lsblk first
            result = subprocess.run(
                ['lsblk', '-d', '-o', 'UUID', '-n', device_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            # Try using blkid
            result = subprocess.run(
                ['blkid', '-s', 'UUID', '-o', 'value', device_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Fallback: read directly from FAT boot sector
        try:
            with open(device_path, 'rb') as f:
                f.seek(0x43)  # Volume ID offset in FAT32
                vol_id = f.read(4)
                return vol_id.hex()
        except (IOError, PermissionError):
            pass
        
        return ""
    
    def _check_garmin_files(self, mount_point: Path) -> bool:
        """Check if mount point contains Garmin files"""
        for indicator in G3X_INDICATOR_FILES:
            if (mount_point / indicator).exists():
                return True
        return False
    
    def _fallback_scan(self) -> List[SDCardInfo]:
        """Fallback scanning using /proc/mounts"""
        cards = []
        
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        device, mount, fstype = parts[0], parts[1], parts[2]
                        
                        if fstype.lower() in ('vfat', 'fat32', 'fat'):
                            if '/dev/sd' in device or '/dev/mmcblk' in device:
                                # Basic card info
                                try:
                                    stat = os.statvfs(mount)
                                    size = stat.f_blocks * stat.f_frsize
                                    free = stat.f_bavail * stat.f_frsize
                                    
                                    card = SDCardInfo(
                                        device_path=device,
                                        mount_point=mount,
                                        label="",
                                        filesystem=fstype,
                                        size_bytes=size,
                                        free_bytes=free,
                                        volume_id=self._get_volume_id(device),
                                        is_garmin=self._check_garmin_files(Path(mount)),
                                    )
                                    cards.append(card)
                                except OSError:
                                    pass
        except IOError:
            pass
        
        return cards
    
    def mount_card(self, device_path: str, mount_point: str = None) -> str:
        """
        Mount an SD card.
        
        Args:
            device_path: Device path (e.g., /dev/sda1)
            mount_point: Optional mount point (will be auto-created)
            
        Returns:
            Mount point path
        """
        if not mount_point:
            mount_point = f"/mnt/g3x_sdcard_{int(time.time())}"
        
        os.makedirs(mount_point, exist_ok=True)
        
        try:
            subprocess.run(
                ['mount', '-o', 'rw,sync', device_path, mount_point],
                check=True,
                capture_output=True,
                timeout=30
            )
            logger.info(f"Mounted {device_path} at {mount_point}")
            return mount_point
        except subprocess.CalledProcessError as e:
            raise SDCardError(f"Failed to mount {device_path}: {e.stderr.decode()}")
    
    def unmount_card(self, mount_point: str):
        """Unmount an SD card, preferring udisksctl (no root required) over umount."""
        # Try udisksctl first — works as a regular user
        result = subprocess.run(
            ['udisksctl', 'unmount', '--mount-point', mount_point],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            logger.info(f"Unmounted {mount_point}")
            return

        # Fallback: direct umount (requires root)
        try:
            subprocess.run(
                ['umount', mount_point],
                check=True,
                capture_output=True,
                timeout=30
            )
            logger.info(f"Unmounted {mount_point}")

            try:
                os.rmdir(mount_point)
            except OSError:
                pass

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to unmount {mount_point}: {e.stderr.decode()}")


class G3XDatabaseWriter:
    """
    Writes aviation databases to SD cards for G3X Touch systems.
    
    This class handles:
    - Database file installation
    - Directory structure creation
    - Metadata tracking
    - Integrity verification
    """
    
    def __init__(self, sd_card: SDCardInfo):
        if not sd_card.mount_point:
            raise SDCardError("SD card must be mounted")
        
        self.sd_card = sd_card
        self.mount_point = Path(sd_card.mount_point)
        self._metadata: Dict[str, DatabaseVersion] = {}
        self._load_metadata()
    
    def _load_metadata(self):
        """Load existing database metadata from SD card"""
        metadata_path = self.mount_point / DB_METADATA_FILE
        
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r') as f:
                    data = json.load(f)
                    for db_type, info in data.get('databases', {}).items():
                        self._metadata[db_type] = DatabaseVersion(
                            db_type=db_type,
                            version=info.get('version', ''),
                            cycle=info.get('cycle', ''),
                            install_date=info.get('install_date', ''),
                            source_file=info.get('source_file', ''),
                            checksum=info.get('checksum', ''),
                        )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Could not load metadata: {e}")
    
    def _save_metadata(self):
        """Save database metadata to SD card"""
        metadata_path = self.mount_point / DB_METADATA_FILE
        
        data = {
            'version': '1.0',
            'updated': datetime.now().isoformat(),
            'databases': {
                db_type: {
                    'version': ver.version,
                    'cycle': ver.cycle,
                    'install_date': ver.install_date,
                    'source_file': ver.source_file,
                    'checksum': ver.checksum,
                }
                for db_type, ver in self._metadata.items()
            }
        }
        
        try:
            with open(metadata_path, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.warning(f"Could not save metadata: {e}")
    
    def get_installed_databases(self) -> Dict[str, DatabaseVersion]:
        """Get information about installed databases"""
        return self._metadata.copy()
    
    def write_database_files(
        self,
        source_files: Dict[str, Path],
        db_type: str,
        version: str,
        cycle: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> WriteResult:
        """
        Write database files to the SD card.
        
        Args:
            source_files: Dict mapping destination paths to source file paths
            db_type: Type of database (navdata, terrain, etc.)
            version: Database version
            cycle: Database cycle
            progress_callback: Optional progress callback (bytes_written, total_bytes)
            
        Returns:
            WriteResult with operation details
        """
        start_time = time.time()
        files_written = []
        bytes_written = 0
        errors = []
        
        # Calculate total bytes
        total_bytes = sum(f.stat().st_size for f in source_files.values() if f.exists())
        
        logger.info(f"Writing {db_type} database ({len(source_files)} files, {total_bytes} bytes)")
        
        for dest_rel_path, source_file in source_files.items():
            dest_path = (self.mount_point / dest_rel_path).resolve()

            # Guard against path traversal attacks
            if not str(dest_path).startswith(str(self.mount_point.resolve())):
                logger.warning(f"Skipping unsafe destination path: {dest_rel_path}")
                errors.append(f"Unsafe destination path rejected: {dest_rel_path}")
                continue

            try:
                # Create parent directories
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Copy file
                shutil.copy2(source_file, dest_path)
                
                file_size = source_file.stat().st_size
                bytes_written += file_size
                files_written.append(str(dest_rel_path))
                
                if progress_callback:
                    progress_callback(bytes_written, total_bytes)
                
                logger.debug(f"Wrote {dest_path} ({file_size} bytes)")
                
            except IOError as e:
                error_msg = f"Failed to write {dest_path}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Sync to ensure data is written
        try:
            subprocess.run(['sync'], timeout=60)
        except subprocess.TimeoutExpired:
            logger.warning("Sync timed out")
        
        # Update metadata
        if files_written:
            checksum = self._calculate_checksum(files_written[0])
            self._metadata[db_type] = DatabaseVersion(
                db_type=db_type,
                version=version,
                cycle=cycle,
                install_date=datetime.now().isoformat(),
                source_file=str(source_files.get(files_written[0], '')),
                checksum=checksum,
            )
            self._save_metadata()
        
        duration = time.time() - start_time
        success = len(errors) == 0 and bytes_written > 0
        
        result = WriteResult(
            success=success,
            files_written=files_written,
            bytes_written=bytes_written,
            duration_seconds=duration,
            errors=errors,
        )
        
        logger.info(f"Write complete: {bytes_written} bytes in {duration:.1f}s, "
                   f"{len(errors)} errors")
        
        return result
    
    def _calculate_checksum(self, rel_path: str) -> str:
        """Calculate SHA256 checksum of a file"""
        file_path = self.mount_point / rel_path
        
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except IOError:
            return ""
    
    def verify_installation(self, db_type: str) -> Tuple[bool, str]:
        """
        Verify that a database is correctly installed.
        
        Args:
            db_type: Type of database to verify
            
        Returns:
            (success, message) tuple
        """
        if db_type not in self._metadata:
            return False, f"Database {db_type} not found in metadata"
        
        version = self._metadata[db_type]
        
        # Check main file exists
        # This is a simplified check - real verification would check all files
        from .taw_parser import G3X_DATABASE_STRUCTURE
        
        if db_type not in G3X_DATABASE_STRUCTURE:
            return True, "Unknown database type - skipping verification"
        
        structure = G3X_DATABASE_STRUCTURE[db_type]
        
        for required_file in structure['required']:
            file_path = self.mount_point / required_file
            if not file_path.exists():
                return False, f"Missing required file: {required_file}"
        
        return True, "Verification successful"
    
    def prepare_for_g3x(self):
        """
        Prepare the SD card directory structure for G3X.
        
        Creates necessary directories and placeholder files.
        """
        for dir_name in G3X_DB_DIRS:
            dir_path = self.mount_point / dir_name
            dir_path.mkdir(exist_ok=True)
        
        logger.info("SD card prepared for G3X databases")
    
    def get_space_available(self) -> int:
        """Get available space on SD card in bytes"""
        try:
            stat = os.statvfs(self.mount_point)
            return stat.f_bavail * stat.f_frsize
        except OSError:
            return 0


class AutoDatabaseUpdater:
    """
    Automatic database update orchestrator.
    
    Coordinates between:
    - SD card detection
    - Database downloads
    - TAW extraction
    - SD card writing
    """
    
    def __init__(
        self,
        download_dir: Path,
        detector: Optional[SDCardDetector] = None
    ):
        self.download_dir = download_dir
        self.detector = detector or SDCardDetector()
        
        # Ensure download directory exists
        self.download_dir.mkdir(parents=True, exist_ok=True)
    
    def update_sd_card(
        self,
        sd_card: SDCardInfo,
        taw_files: List[Path],
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> List[WriteResult]:
        """
        Update an SD card with databases from TAW files.
        
        Args:
            sd_card: SD card to update
            taw_files: List of TAW files to install
            progress_callback: Optional progress callback (operation, current, total)
            
        Returns:
            List of WriteResult for each TAW file
        """
        from .taw_parser import TAWExtractor, TAWParser
        
        results = []
        extractor = TAWExtractor()
        writer = G3XDatabaseWriter(sd_card)
        
        # Prepare SD card
        writer.prepare_for_g3x()
        
        total_files = len(taw_files)
        
        for i, taw_file in enumerate(taw_files):
            if progress_callback:
                progress_callback(f"Processing {taw_file.name}", i, total_files)
            
            try:
                # Parse TAW file
                parsed = extractor.list_contents(taw_file)
                
                # Extract to temporary directory
                temp_dir = self.download_dir / f"extract_{int(time.time())}"
                temp_dir.mkdir(exist_ok=True)
                
                try:
                    extracted = extractor.extract_to_directory(
                        taw_file, temp_dir, preserve_paths=True
                    )
                    
                    # Prepare source files mapping
                    source_files = {}
                    for extracted_file in extracted:
                        rel_path = extracted_file.relative_to(temp_dir)
                        source_files[str(rel_path)] = extracted_file
                    
                    # Write to SD card
                    result = writer.write_database_files(
                        source_files=source_files,
                        db_type=parsed.header.db_type_name or "unknown",
                        version=str(parsed.header.version),
                        cycle=parsed.header.cycle_string,
                    )
                    
                    results.append(result)
                    
                finally:
                    # Clean up temp directory
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    
            except Exception as e:
                logger.error(f"Failed to process {taw_file}: {e}")
                results.append(WriteResult(
                    success=False,
                    files_written=[],
                    bytes_written=0,
                    duration_seconds=0,
                    errors=[str(e)],
                ))
        
        if progress_callback:
            progress_callback("Complete", total_files, total_files)
        
        return results
    
    def find_and_update(
        self,
        taw_files: List[Path],
        auto_mount: bool = True
    ) -> Tuple[Optional[SDCardInfo], List[WriteResult]]:
        """
        Find a suitable SD card and update it.
        
        Args:
            taw_files: TAW files to install
            auto_mount: Whether to auto-mount unmounted cards
            
        Returns:
            (SDCardInfo or None, list of WriteResults)
        """
        cards = self.detector.scan_for_cards()
        
        # Find first suitable card
        for card in cards:
            suitable, reason = card.is_suitable()
            if suitable:
                logger.info(f"Using SD card: {card.device_path} ({card.size_gb:.1f}GB)")
                
                if not card.mount_point and auto_mount:
                    mount_point = self.detector.mount_card(card.device_path)
                    card.mount_point = mount_point
                
                if card.mount_point:
                    results = self.update_sd_card(card, taw_files)
                    return card, results
        
        raise SDCardNotFoundError("No suitable SD card found")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    detector = SDCardDetector()
    cards = detector.scan_for_cards()
    
    print(f"Found {len(cards)} SD card(s):")
    for card in cards:
        suitable, reason = card.is_suitable()
        status = "✓" if suitable else "✗"
        print(f"  {status} {card.device_path}: {card.size_gb:.1f}GB {card.filesystem}")
        print(f"      Mount: {card.mount_point or 'Not mounted'}")
        print(f"      Garmin: {'Yes' if card.is_garmin else 'No'}")
        if not suitable:
            print(f"      Issue: {reason}")
