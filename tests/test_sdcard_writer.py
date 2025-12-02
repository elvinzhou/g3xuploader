"""Tests for SD card writer"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json
import os

from g3x_db_updater.sdcard_writer import (
    SDCardInfo, SDCardDetector, G3XDatabaseWriter,
    AutoDatabaseUpdater, WriteResult, DatabaseVersion,
    SDCardError, SDCardNotFoundError,
    MIN_SD_CARD_SIZE_GB, MAX_SD_CARD_SIZE_GB,
    G3X_DB_DIRS, DB_METADATA_FILE
)


class TestSDCardInfo:
    """Tests for SDCardInfo dataclass"""
    
    def test_size_conversion(self):
        """Test size conversion to GB"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point="/mnt/sd",
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,  # 8 GB
            free_bytes=4 * 1024 ** 3,  # 4 GB
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        assert info.size_gb == 8.0
        assert info.free_gb == 4.0
    
    def test_suitability_vfat(self):
        """Test that vfat filesystem is suitable"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point="/mnt/sd",
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        suitable, reason = info.is_suitable()
        assert suitable is True
        assert reason == "OK"
    
    def test_suitability_wrong_filesystem(self):
        """Test that non-FAT filesystems are rejected"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point="/mnt/sd",
            label="TEST",
            filesystem="ext4",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        suitable, reason = info.is_suitable()
        assert suitable is False
        assert "FAT32" in reason
    
    def test_suitability_too_small(self):
        """Test that small cards are rejected"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point="/mnt/sd",
            label="TEST",
            filesystem="vfat",
            size_bytes=1 * 1024 ** 3,  # 1 GB - too small
            free_bytes=1 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        suitable, reason = info.is_suitable()
        assert suitable is False
        assert "small" in reason.lower()
    
    def test_suitability_too_large(self):
        """Test that large cards are rejected"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point="/mnt/sd",
            label="TEST",
            filesystem="vfat",
            size_bytes=64 * 1024 ** 3,  # 64 GB - too large for FAT32
            free_bytes=60 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        suitable, reason = info.is_suitable()
        assert suitable is False
        assert "large" in reason.lower()


class TestSDCardDetector:
    """Tests for SDCardDetector class"""
    
    def test_parse_size_bytes(self):
        """Test parsing size strings"""
        detector = SDCardDetector()
        
        assert detector._parse_size("8G") == 8 * 1024 ** 3
        assert detector._parse_size("512M") == 512 * 1024 ** 2
        assert detector._parse_size("1024K") == 1024 * 1024
        assert detector._parse_size("1024B") == 1024
        assert detector._parse_size("1024") == 1024
        assert detector._parse_size("") == 0
        assert detector._parse_size("invalid") == 0
    
    @patch('subprocess.run')
    def test_scan_handles_lsblk_failure(self, mock_run):
        """Test that scan handles lsblk failure gracefully"""
        mock_run.return_value = Mock(returncode=1, stderr="error")
        
        detector = SDCardDetector()
        cards = detector.scan_for_cards()
        
        assert cards == []


class TestG3XDatabaseWriter:
    """Tests for G3XDatabaseWriter class"""
    
    def test_init_requires_mount(self):
        """Test that writer requires mounted SD card"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point=None,  # Not mounted
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        with pytest.raises(SDCardError):
            G3XDatabaseWriter(info)
    
    def test_prepare_for_g3x(self, tmp_path):
        """Test directory structure creation"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point=str(tmp_path),
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        writer = G3XDatabaseWriter(info)
        writer.prepare_for_g3x()
        
        for dir_name in G3X_DB_DIRS:
            assert (tmp_path / dir_name).is_dir()
    
    def test_write_database_files(self, tmp_path):
        """Test writing database files"""
        # Set up source and destination
        source_dir = tmp_path / "source"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        dest_dir.mkdir()
        
        # Create source file
        source_file = source_dir / "test.bin"
        source_file.write_bytes(b"test data")
        
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point=str(dest_dir),
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        writer = G3XDatabaseWriter(info)
        
        result = writer.write_database_files(
            source_files={"test.bin": source_file},
            db_type="navdata",
            version="1",
            cycle="2413"
        )
        
        assert result.success is True
        assert len(result.files_written) == 1
        assert result.bytes_written == 9
        assert (dest_dir / "test.bin").exists()
    
    def test_metadata_persistence(self, tmp_path):
        """Test that metadata is saved and loaded"""
        info = SDCardInfo(
            device_path="/dev/sda1",
            mount_point=str(tmp_path),
            label="TEST",
            filesystem="vfat",
            size_bytes=8 * 1024 ** 3,
            free_bytes=4 * 1024 ** 3,
            volume_id="1234ABCD",
            is_garmin=False
        )
        
        # Create source file
        source_file = tmp_path / "source.bin"
        source_file.write_bytes(b"test")
        
        # Write with first instance
        writer1 = G3XDatabaseWriter(info)
        writer1.write_database_files(
            source_files={"test.bin": source_file},
            db_type="navdata",
            version="1",
            cycle="2413"
        )
        
        # Read with second instance
        writer2 = G3XDatabaseWriter(info)
        installed = writer2.get_installed_databases()
        
        assert "navdata" in installed
        assert installed["navdata"].cycle == "2413"


class TestWriteResult:
    """Tests for WriteResult dataclass"""
    
    def test_success_result(self):
        """Test successful write result"""
        result = WriteResult(
            success=True,
            files_written=["file1.bin", "file2.bin"],
            bytes_written=1024,
            duration_seconds=5.0,
            errors=[]
        )
        
        assert result.success is True
        assert len(result.files_written) == 2
        assert result.bytes_written == 1024
    
    def test_failure_result(self):
        """Test failed write result"""
        result = WriteResult(
            success=False,
            files_written=[],
            bytes_written=0,
            duration_seconds=1.0,
            errors=["Write failed", "Permission denied"]
        )
        
        assert result.success is False
        assert len(result.errors) == 2


class TestAutoDatabaseUpdater:
    """Tests for AutoDatabaseUpdater class"""
    
    def test_init_creates_download_dir(self, tmp_path):
        """Test that init creates download directory"""
        download_dir = tmp_path / "downloads"
        
        updater = AutoDatabaseUpdater(download_dir)
        
        assert download_dir.is_dir()
    
    @patch.object(SDCardDetector, 'scan_for_cards')
    def test_find_and_update_no_cards(self, mock_scan, tmp_path):
        """Test that find_and_update raises when no cards found"""
        mock_scan.return_value = []
        
        updater = AutoDatabaseUpdater(tmp_path)
        
        with pytest.raises(SDCardNotFoundError):
            updater.find_and_update([])
