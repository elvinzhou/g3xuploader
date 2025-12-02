"""Pytest configuration and fixtures"""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_downloads(tmp_path):
    """Create a temporary downloads directory"""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return downloads


@pytest.fixture
def mock_sdcard(tmp_path):
    """Create a mock SD card mount point"""
    sdcard = tmp_path / "sdcard"
    sdcard.mkdir()
    return sdcard
