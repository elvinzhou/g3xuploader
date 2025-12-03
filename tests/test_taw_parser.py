"""Tests for TAW parser"""

import pytest
from pathlib import Path
import struct
import io

from avcardtool.navdata.garmin.taw_parser import (
    TAWParser, TAWExtractor, TAWFile, TAWHeader, TAWRegion,
    TAWParseError, TAW_REGION_PATHS, TAWRegionType
)


class TestTAWParser:
    """Tests for TAWParser class"""
    
    def test_magic_validation(self, tmp_path):
        """Test that parser rejects files with invalid magic"""
        invalid_file = tmp_path / "invalid.taw"
        invalid_file.write_bytes(b"XXXX" + b"\x00" * 100)
        
        parser = TAWParser()
        with pytest.raises(TAWParseError) as exc:
            parser.parse(invalid_file)
        
        assert "Invalid magic" in str(exc.value)
    
    def test_taw_magic_accepted(self, tmp_path):
        """Test that TAW magic is accepted"""
        # Create a minimal valid TAW file structure
        taw_file = tmp_path / "test.taw"
        
        data = io.BytesIO()
        data.write(b'TAW\x00')  # Magic
        data.write(struct.pack('<HH', 1, 100))  # version, db_type
        data.write(struct.pack('<BB', 24, 13))  # year, cycle
        data.write(b'Test Avionics\x00')
        data.write(b'US Coverage\x00')
        data.write(b'NavData\x00')
        data.write(struct.pack('<I', 0))  # num_regions = 0
        
        taw_file.write_bytes(data.getvalue())
        
        parser = TAWParser()
        result = parser.parse(taw_file)
        
        assert result.header.version == 1
        assert result.header.database_type == 100
        assert result.header.year == 24
        assert result.header.cycle == 13
        assert result.header.avionics == 'Test Avionics'
        assert result.header.coverage == 'US Coverage'
        assert result.header.cycle_string == '2413'
    
    def test_awp_magic_accepted(self, tmp_path):
        """Test that AWP magic is accepted"""
        awp_file = tmp_path / "test.awp"
        
        data = io.BytesIO()
        data.write(b'AWP\x00')  # Magic
        data.write(struct.pack('<HH', 1, 190))  # version, db_type
        data.write(struct.pack('<BB', 24, 13))  # year, cycle
        data.write(b'GNS 430W/530W\x00')
        data.write(b'US Garmin Navigation Database\x00')
        data.write(b'\x00')  # empty type name
        data.write(struct.pack('<I', 0))  # num_regions = 0
        
        awp_file.write_bytes(data.getvalue())
        
        parser = TAWParser()
        result = parser.parse(awp_file)
        
        assert result.header.avionics == 'GNS 430W/530W'


class TestTAWRegion:
    """Tests for TAWRegion class"""
    
    def test_type_name_known(self):
        """Test that known region types have names"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.type_name == "NAVIGATION"
    
    def test_type_name_unknown(self):
        """Test that unknown region types have fallback names"""
        region = TAWRegion(
            region_type=0xFF,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.type_name == "UNKNOWN_ff"
    
    def test_output_path_from_dest_path(self):
        """Test output path uses dest_path when available"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path="custom/path/file.bin"
        )
        assert region.output_path == "custom/path/file.bin"
    
    def test_output_path_fallback(self):
        """Test output path fallback to default mapping"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.output_path == TAW_REGION_PATHS[0x01]


class TestTAWExtractor:
    """Tests for TAWExtractor class"""
    
    def test_list_contents(self, tmp_path):
        """Test listing TAW contents without extracting"""
        # Create minimal TAW file
        taw_file = tmp_path / "test.taw"
        
        data = io.BytesIO()
        data.write(b'TAW\x00')
        data.write(struct.pack('<HH', 1, 100))
        data.write(struct.pack('<BB', 24, 13))
        data.write(b'Test\x00')
        data.write(b'Coverage\x00')
        data.write(b'Type\x00')
        data.write(struct.pack('<I', 0))
        
        taw_file.write_bytes(data.getvalue())
        
        extractor = TAWExtractor()
        result = extractor.list_contents(taw_file)
        
        assert isinstance(result, TAWFile)
        assert result.filepath == taw_file


class TestTAWRegionPaths:
    """Tests for TAW region path mappings"""
    
    def test_navigation_path(self):
        """Test navigation database path"""
        assert TAW_REGION_PATHS[0x01] == "ldr_sys/avtn_db.bin"
    
    def test_terrain_9as_path(self):
        """Test terrain_9as.odb path (jdmtool issue #57 fix)"""
        # This tests the fix from jdmtool issue #57
        assert TAW_REGION_PATHS[0x22] == "terrain_9as.odb"
    
    def test_fc_tpc_path(self):
        """Test FliteCharts TPC path"""
        assert TAW_REGION_PATHS[0x14] == "fc_tpc/fc_tpc.dat"
    
    def test_rasters_path(self):
        """Test rasters path"""
        assert TAW_REGION_PATHS[0x1A] == "rasters/rasters.xml"


# Fixtures

@pytest.fixture
def sample_taw_file(tmp_path):
    """Create a sample TAW file for testing"""
    taw_file = tmp_path / "sample.taw"
    
    # Build a simple TAW file with one region
    header = io.BytesIO()
    header.write(b'TAW\x00')
    header.write(struct.pack('<HH', 1, 100))  # version, db_type
    header.write(struct.pack('<BB', 24, 13))  # year, cycle
    header.write(b'Test Avionics\x00')
    header.write(b'US Coverage\x00')
    header.write(b'NavData\x00')
    header.write(struct.pack('<I', 1))  # num_regions = 1
    
    # Region header
    region_data = b'Hello, World!'
    header.write(struct.pack('<I', 0x01))  # region_type
    header.write(struct.pack('<I', 0))  # offset (will be 0 from data start)
    header.write(struct.pack('<I', len(region_data)))  # compressed_size
    header.write(struct.pack('<I', len(region_data)))  # uncompressed_size
    header.write(b'test.bin\x00')  # dest_path
    
    # Region data (uncompressed)
    header.write(region_data)
    
    taw_file.write_bytes(header.getvalue())
    return taw_file
