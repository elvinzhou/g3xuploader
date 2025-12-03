"""
Tests for flight data uploaders.

This test suite validates Phase 2 implementation without requiring actual service credentials.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from avcardtool.flight_data import GarminG3XProcessor, FlightDataAnalyzer
from avcardtool.flight_data.uploaders import (
    CloudAhoyUploader,
    FlyStoUploader,
    SavvyAviationUploader,
    MaintenanceTrackerUploader
)
from avcardtool.core.config import FlightDataConfig


# Get test data directory
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SAMPLE_FLIGHT = TEST_DATA_DIR / "sample_flight.csv"


@pytest.fixture
def sample_flight_data():
    """Provide parsed flight data from sample flight."""
    processor = GarminG3XProcessor()
    return processor.parse_log(SAMPLE_FLIGHT)


@pytest.fixture
def sample_analysis():
    """Provide flight analysis results."""
    processor = GarminG3XProcessor()
    flight_data = processor.parse_log(SAMPLE_FLIGHT)
    config = FlightDataConfig()
    analyzer = FlightDataAnalyzer(config)
    return analyzer.analyze(flight_data)


@pytest.fixture
def sample_analysis_summary():
    """Provide flight analysis summary."""
    processor = GarminG3XProcessor()
    flight_data = processor.parse_log(SAMPLE_FLIGHT)
    config = FlightDataConfig()
    analyzer = FlightDataAnalyzer(config)
    return analyzer.analyze_summary(flight_data)


class TestCloudAhoyUploader:
    """Tests for CloudAhoy uploader."""

    def test_uploader_creation(self):
        """Test creating a CloudAhoy uploader."""
        config = {
            'enabled': True,
            'api_token': 'test_token_123'
        }
        uploader = CloudAhoyUploader(config)
        assert uploader.enabled is True
        assert uploader.api_token == 'test_token_123'

    def test_authentication_no_token(self):
        """Test authentication fails without API token."""
        config = {'enabled': True}
        uploader = CloudAhoyUploader(config)
        assert uploader.authenticate() is False

    def test_authentication_with_token(self):
        """Test authentication succeeds with API token."""
        config = {
            'enabled': True,
            'api_token': 'test_token'
        }
        uploader = CloudAhoyUploader(config)
        assert uploader.authenticate() is True

    def test_upload_disabled(self, sample_flight_data):
        """Test upload when uploader is disabled."""
        config = {'enabled': False, 'api_token': 'test'}
        uploader = CloudAhoyUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not enabled" in result.message

    def test_upload_no_token(self, sample_flight_data):
        """Test upload without API token."""
        config = {'enabled': True}
        uploader = CloudAhoyUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not configured" in result.message

    @patch('avcardtool.flight_data.uploaders.cloudahoy.requests.post')
    def test_upload_success(self, mock_post, sample_flight_data):
        """Test successful upload to CloudAhoy."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'key': 'test_flight_key_123'}
        mock_post.return_value = mock_response

        config = {
            'enabled': True,
            'api_token': 'test_token'
        }
        uploader = CloudAhoyUploader(config)
        result = uploader.upload_flight(sample_flight_data)

        assert result.success is True
        assert result.service == "CloudAhoy"
        assert result.upload_id == 'test_flight_key_123'
        assert 'cloudahoy.com/debrief' in result.url

        # Verify API call
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs['headers']['Authorization'] == 'Bearer test_token'

    @patch('avcardtool.flight_data.uploaders.cloudahoy.requests.post')
    def test_upload_failure(self, mock_post, sample_flight_data):
        """Test failed upload to CloudAhoy."""
        # Mock failed API response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = 'Unauthorized'
        mock_post.return_value = mock_response

        config = {
            'enabled': True,
            'api_token': 'invalid_token'
        }
        uploader = CloudAhoyUploader(config)
        result = uploader.upload_flight(sample_flight_data)

        assert result.success is False
        assert '401' in result.message


class TestFlyStoUploader:
    """Tests for FlySto uploader."""

    def test_uploader_creation(self):
        """Test creating a FlySto uploader."""
        config = {
            'enabled': True,
            'client_id': 'test_client',
            'client_secret': 'test_secret',
            'refresh_token': 'test_refresh'
        }
        uploader = FlyStoUploader(config)
        assert uploader.enabled is True
        assert uploader.client_id == 'test_client'
        assert uploader.refresh_token == 'test_refresh'

    def test_get_authorization_url(self):
        """Test getting OAuth authorization URL."""
        config = {
            'enabled': True,
            'client_id': 'my_client_id',
            'client_secret': 'secret'
        }
        uploader = FlyStoUploader(config)
        url = uploader.get_authorization_url()
        assert 'flysto.net/oauth/authorize' in url
        assert 'client_id=my_client_id' in url

    def test_upload_disabled(self, sample_flight_data):
        """Test upload when uploader is disabled."""
        config = {'enabled': False}
        uploader = FlyStoUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not enabled" in result.message

    def test_upload_no_credentials(self, sample_flight_data):
        """Test upload without OAuth credentials."""
        config = {'enabled': True}
        uploader = FlyStoUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not configured" in result.message

    def test_upload_not_authorized(self, sample_flight_data):
        """Test upload without completing OAuth flow."""
        config = {
            'enabled': True,
            'client_id': 'test',
            'client_secret': 'test'
        }
        uploader = FlyStoUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not authorized" in result.message
        assert "OAuth setup" in result.message


class TestSavvyAviationUploader:
    """Tests for Savvy Aviation uploader."""

    def test_uploader_creation(self, tmp_path):
        """Test creating a Savvy Aviation uploader."""
        config = {
            'enabled': True,
            'staging_dir': str(tmp_path / 'staging'),
            'email': 'test@example.com'
        }
        uploader = SavvyAviationUploader(config)
        assert uploader.enabled is True
        assert uploader.email == 'test@example.com'

    def test_authentication_always_succeeds(self):
        """Test authentication always succeeds (no auth needed for staging)."""
        config = {'enabled': True}
        uploader = SavvyAviationUploader(config)
        assert uploader.authenticate() is True

    def test_upload_disabled(self, sample_flight_data):
        """Test upload when uploader is disabled."""
        config = {'enabled': False}
        uploader = SavvyAviationUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not enabled" in result.message

    def test_upload_stages_file(self, sample_flight_data, tmp_path):
        """Test that upload stages file to directory."""
        staging_dir = tmp_path / 'savvy_staging'
        config = {
            'enabled': True,
            'staging_dir': str(staging_dir)
        }
        uploader = SavvyAviationUploader(config)
        result = uploader.upload_flight(sample_flight_data)

        assert result.success is True
        assert result.service == "Savvy Aviation"
        assert staging_dir.exists()
        # Check that file was copied
        staged_file = staging_dir / Path(sample_flight_data.file_path).name
        assert staged_file.exists()


class TestMaintenanceTrackerUploader:
    """Tests for Maintenance Tracker uploader."""

    def test_uploader_creation(self):
        """Test creating a maintenance tracker uploader."""
        config = {
            'enabled': True,
            'url': 'https://example.com/webhook',
            'api_key': 'test_key'
        }
        uploader = MaintenanceTrackerUploader(config)
        assert uploader.enabled is True
        assert uploader.url == 'https://example.com/webhook'

    def test_authentication_no_credentials(self):
        """Test authentication fails without credentials."""
        config = {'enabled': True}
        uploader = MaintenanceTrackerUploader(config)
        assert uploader.authenticate() is False

    def test_authentication_with_credentials(self):
        """Test authentication succeeds with credentials."""
        config = {
            'enabled': True,
            'url': 'https://example.com/webhook',
            'api_key': 'test_key'
        }
        uploader = MaintenanceTrackerUploader(config)
        assert uploader.authenticate() is True

    def test_upload_disabled(self, sample_flight_data):
        """Test upload when uploader is disabled."""
        config = {'enabled': False}
        uploader = MaintenanceTrackerUploader(config)
        result = uploader.upload_flight(sample_flight_data)
        assert result.success is False
        assert "not enabled" in result.message

    def test_upload_no_analysis_results(self, sample_flight_data):
        """Test upload without analysis results."""
        config = {
            'enabled': True,
            'url': 'https://example.com/webhook',
            'api_key': 'test'
        }
        uploader = MaintenanceTrackerUploader(config)
        result = uploader.upload_flight(sample_flight_data, None)
        assert result.success is False
        assert "No analysis results" in result.message

    @patch('avcardtool.flight_data.uploaders.maintenance_tracker.requests.post')
    def test_upload_success(self, mock_post, sample_flight_data, sample_analysis_summary):
        """Test successful upload to maintenance tracker."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        config = {
            'enabled': True,
            'url': 'https://example.com/webhook',
            'api_key': 'test_key'
        }
        uploader = MaintenanceTrackerUploader(config)
        result = uploader.upload_flight(sample_flight_data, sample_analysis_summary)

        assert result.success is True
        assert result.service == "Maintenance Tracker"

        # Verify API call
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs['headers']['Authorization'] == 'Bearer test_key'
        assert call_kwargs['headers']['Content-Type'] == 'application/json'

        # Verify payload contains expected data
        payload = call_kwargs['json']
        assert 'aircraft_ident' in payload
        assert 'hobbs' in payload
        assert 'tach' in payload
        assert 'oooi' in payload


class TestUploaderIntegration:
    """Integration tests for uploaders."""

    def test_all_uploaders_importable(self):
        """Test that all uploaders can be imported."""
        from avcardtool.flight_data.uploaders import UPLOADERS
        assert 'cloudahoy' in UPLOADERS
        assert 'flysto' in UPLOADERS
        assert 'savvy_aviation' in UPLOADERS
        assert 'maintenance_tracker' in UPLOADERS

    def test_uploader_registry(self):
        """Test uploader registry structure."""
        from avcardtool.flight_data.uploaders import UPLOADERS
        for name, UploaderClass in UPLOADERS.items():
            # All uploaders should be instantiable
            uploader = UploaderClass({'enabled': False})
            assert hasattr(uploader, 'upload_flight')
            assert hasattr(uploader, 'authenticate')
