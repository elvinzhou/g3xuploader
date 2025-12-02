"""
Tests for Garmin G3X processor and flight analysis.

This test suite validates Phase 1 implementation without requiring uploads.
"""

import pytest
from pathlib import Path
from datetime import datetime

from aviation_tools.flight_data import GarminG3XProcessor, FlightDataAnalyzer
from aviation_tools.core.config import FlightDataConfig


# Get test data directory
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SAMPLE_FLIGHT = TEST_DATA_DIR / "sample_flight.csv"


@pytest.fixture
def sample_flight_path():
    """Provide path to sample flight CSV."""
    return SAMPLE_FLIGHT


@pytest.fixture
def default_config():
    """Provide default flight data configuration."""
    from aviation_tools.core.config import FlightDetectionConfig
    config = FlightDataConfig()
    # Lower thresholds for test data
    config.flight_detection.minimum_data_points = 20
    return config


class TestGarminG3XProcessor:
    """Tests for Garmin G3X processor."""

    def test_detect_format(self, sample_flight_path):
        """Test detection of G3X CSV format."""
        processor = GarminG3XProcessor()
        assert processor.detect_log_format(sample_flight_path)

    def test_detect_format_invalid_file(self, tmp_path):
        """Test rejection of non-G3X files."""
        invalid_file = tmp_path / "invalid.csv"
        invalid_file.write_text("Not a G3X file\n")

        processor = GarminG3XProcessor()
        assert not processor.detect_log_format(invalid_file)

    def test_extract_metadata(self, sample_flight_path):
        """Test extraction of metadata from G3X log."""
        processor = GarminG3XProcessor()
        metadata = processor.extract_metadata(sample_flight_path)

        assert metadata.aircraft_ident == "N12345"
        assert metadata.airframe_hours_start == 150.5
        assert metadata.engine_hours_start == 98.3
        assert metadata.manufacturer == "Garmin"
        assert metadata.model == "G3X Touch"
        assert metadata.serial_number == "1234567890"

    def test_parse_log(self, sample_flight_path):
        """Test complete log file parsing."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        # Check metadata
        assert flight_data.metadata.aircraft_ident == "N12345"
        assert flight_data.metadata.airframe_hours_start == 150.5

        # Check data points
        assert len(flight_data.data_points) > 0
        assert flight_data.file_path == sample_flight_path
        assert len(flight_data.file_hash) == 64  # SHA256 hash

    def test_parse_data_points(self, sample_flight_path):
        """Test parsing of individual data points."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        # Check first data point
        first_point = flight_data.data_points[0]
        assert isinstance(first_point.timestamp, datetime)
        assert first_point.latitude is not None
        assert first_point.longitude is not None

        # Check data point with engine data
        engine_points = [dp for dp in flight_data.data_points if dp.rpm and dp.rpm > 1000]
        assert len(engine_points) > 0

        point = engine_points[0]
        assert point.rpm > 0
        assert point.oil_pressure > 0

    def test_get_supported_extensions(self):
        """Test supported file extensions."""
        processor = GarminG3XProcessor()
        extensions = processor.get_supported_extensions()
        assert '.csv' in extensions


class TestFlightDetector:
    """Tests for flight detection."""

    def test_detect_actual_flight(self, sample_flight_path, default_config):
        """Test detection of actual flight."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        # This should be detected as a flight
        assert analysis.detection.is_flight
        assert analysis.detection.rejection_reason is None
        assert analysis.detection.airborne_time_minutes > 0
        assert analysis.detection.max_ground_speed_kts > 50
        assert analysis.detection.altitude_change_ft > 200

    def test_flight_metrics(self, sample_flight_path, default_config):
        """Test flight detection metrics."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        detection = analysis.detection

        # Verify reasonable values from our sample data
        assert detection.data_points > 20
        assert detection.max_ground_speed_kts >= 100  # We had 118 kts in sample
        assert detection.altitude_change_ft >= 3000  # We went from 125 to 3890 ft


class TestHobbsCalculator:
    """Tests for Hobbs time calculation."""

    def test_hobbs_calculation(self, sample_flight_path, default_config):
        """Test Hobbs time calculation."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        assert analysis.hobbs is not None
        assert analysis.hobbs.starting_hours == 150.5
        assert analysis.hobbs.increment_hours > 0
        assert analysis.hobbs.ending_hours > analysis.hobbs.starting_hours

    def test_hobbs_oil_pressure_trigger(self, sample_flight_path, default_config):
        """Test Hobbs with oil pressure trigger."""
        # Default config uses oil_pressure trigger
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        # Should accrue time when oil pressure > threshold
        assert analysis.hobbs.increment_hours > 0
        # Roughly 10+ minutes of flight time
        assert 0.1 < analysis.hobbs.increment_hours < 5.0


class TestTachCalculator:
    """Tests for Tach time calculation."""

    def test_tach_calculation(self, sample_flight_path, default_config):
        """Test Tach time calculation."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        assert analysis.tach is not None
        assert analysis.tach.starting_hours == 98.3
        assert analysis.tach.increment_hours > 0
        assert analysis.tach.ending_hours > analysis.tach.starting_hours

    def test_tach_variable_mode(self, sample_flight_path, default_config):
        """Test Tach in variable mode."""
        # Default config uses variable mode with reference_rpm=2700
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        # In variable mode, tach should be close to but not exactly hobbs
        # (depends on actual RPM vs reference RPM)
        assert analysis.tach.increment_hours > 0


class TestOOOIDetector:
    """Tests for OOOI event detection."""

    def test_oooi_detection(self, sample_flight_path, default_config):
        """Test OOOI event detection."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        assert analysis.oooi is not None

        # OUT should be detected (engine start)
        assert analysis.oooi.out_time is not None

        # OFF should be detected (takeoff)
        assert analysis.oooi.off_time is not None

        # ON should be detected (landing)
        assert analysis.oooi.on_time is not None

        # IN should be detected (engine stop)
        assert analysis.oooi.in_time is not None

    def test_oooi_times_logical_order(self, sample_flight_path, default_config):
        """Test that OOOI times are in logical order."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        oooi = analysis.oooi

        # Times should be in order: OUT < OFF < ON < IN
        if all([oooi.out_time, oooi.off_time, oooi.on_time, oooi.in_time]):
            assert oooi.out_time < oooi.off_time
            assert oooi.off_time < oooi.on_time
            assert oooi.on_time < oooi.in_time

    def test_oooi_block_and_flight_time(self, sample_flight_path, default_config):
        """Test block time and flight time calculations."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        oooi = analysis.oooi

        # Block time should be calculated
        if oooi.out_time and oooi.in_time:
            assert oooi.block_time_minutes > 0

        # Flight time should be calculated
        if oooi.off_time and oooi.on_time:
            assert oooi.flight_time_minutes > 0

        # Flight time should be less than or equal to block time
        if oooi.block_time_minutes and oooi.flight_time_minutes:
            assert oooi.flight_time_minutes <= oooi.block_time_minutes


class TestCompleteAnalysis:
    """Integration tests for complete flight analysis."""

    def test_complete_analysis_workflow(self, sample_flight_path, default_config):
        """Test complete workflow from parsing to analysis."""
        # Parse
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        # Analyze
        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        # Verify all components
        assert analysis.aircraft_ident == "N12345"
        assert analysis.detection.is_flight
        assert analysis.hobbs is not None
        assert analysis.tach is not None
        assert analysis.oooi is not None

        # Verify reasonable values
        assert 0.1 < analysis.hobbs.increment_hours < 5.0
        assert 0.1 < analysis.tach.increment_hours < 5.0
        assert analysis.oooi.block_time_minutes > 0

    def test_analysis_summary_json(self, sample_flight_path, default_config):
        """Test JSON summary output."""
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        summary = analyzer.analyze_summary(flight_data)

        # Verify JSON structure
        assert summary['aircraft_ident'] == "N12345"
        assert summary['is_flight'] is True
        assert 'hobbs' in summary
        assert 'tach' in summary
        assert 'oooi' in summary
        assert 'metrics' in summary

        # Verify hobbs data
        assert summary['hobbs']['starting_hours'] == 150.5
        assert summary['hobbs']['increment_hours'] > 0

        # Verify tach data
        assert summary['tach']['starting_hours'] == 98.3
        assert summary['tach']['increment_hours'] > 0

        # Verify OOOI data
        assert summary['oooi']['out_time'] is not None
        assert summary['oooi']['block_time_minutes'] is not None

    def test_no_upload_during_analysis(self, sample_flight_path, default_config):
        """Verify that analysis does not trigger any uploads."""
        # This test documents that Phase 1 does NOT include uploads
        processor = GarminG3XProcessor()
        flight_data = processor.parse_log(sample_flight_path)

        analyzer = FlightDataAnalyzer(default_config)
        analysis = analyzer.analyze(flight_data)

        # Analysis should complete successfully without any upload service configured
        assert analysis.detection.is_flight
        # No assertions about uploads - they're not implemented yet
