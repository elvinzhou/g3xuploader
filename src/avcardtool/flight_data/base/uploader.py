"""
Abstract base class for flight data uploaders.

This module defines the interface that all upload service integrations must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Union
import logging

from avcardtool.flight_data.base.processor import FlightData

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of an upload operation."""
    success: bool
    message: str
    service: Optional[str] = None
    upload_id: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class FlightDataUploader(ABC):
    """
    Abstract base class for flight data uploaders.

    Each upload service (CloudAhoy, FlySto, etc.) implements this interface.

    Example:
        class CloudAhoyUploader(FlightDataUploader):
            def __init__(self, config):
                self.api_token = config.get('api_token')

            def authenticate(self):
                # Verify API token
                ...

            def upload_flight(self, flight_data, analysis_results):
                # Upload to CloudAhoy
                ...
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize uploader with configuration.

        Args:
            config: Configuration dictionary for this uploader
        """
        self.config = config
        self.enabled = config.get('enabled', False)
        self.debug = config.get('debug', False)
        self.data_dir = config.get('data_dir', '/var/lib/avcardtool')

    @abstractmethod
    def authenticate(self) -> bool:
        """
        Authenticate with the upload service.

        Returns:
            True if authentication successful

        Raises:
            AuthenticationError: If authentication fails
        """
        pass

    @abstractmethod
    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[Dict[str, Any]] = None
    ) -> UploadResult:
        """
        Upload flight data to the service.

        Args:
            flight_data: Parsed flight data
            analysis_results: Optional analysis results (Hobbs/Tach/OOOI)

        Returns:
            UploadResult with status and details

        Raises:
            UploadError: If upload fails
        """
        pass

    def is_enabled(self) -> bool:
        """Check if uploader is enabled in configuration."""
        return self.enabled

    def get_name(self) -> str:
        """
        Get human-readable name for this uploader.

        Returns:
            Uploader name (e.g., "CloudAhoy")
        """
        return self.__class__.__name__.replace("Uploader", "")

    def validate_config(self) -> bool:
        """
        Validate uploader configuration.

        Returns:
            True if configuration is valid

        Raises:
            ValueError: If configuration is invalid
        """
        return True

    def supports_format(self, format_type: str) -> bool:
        """
        Check if uploader supports a specific data format.

        Args:
            format_type: Format type (e.g., 'csv', 'gpx', 'kml')

        Returns:
            True if format is supported
        """
        return False

    def prepare_upload_data(
        self,
        flight_data: FlightData,
        analysis_results: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Prepare data for upload in service-specific format.

        Args:
            flight_data: Parsed flight data
            analysis_results: Optional analysis results

        Returns:
            Dictionary with data in service-specific format
        """
        data = {
            'aircraft_ident': flight_data.metadata.aircraft_ident,
            'date': flight_data.metadata.date,
            'duration_seconds': flight_data.duration_seconds,
            'max_altitude_ft': flight_data.max_altitude_ft,
            'max_ground_speed_kts': flight_data.max_ground_speed_kts,
        }

        if analysis_results:
            data.update(analysis_results)

        return data

    def _save_debug_payload(self, filename: str, content: Union[bytes, str]) -> None:
        """
        Save a payload to the debug directory for inspection.

        Args:
            filename: Name of the file to save (e.g. 'flysto_log.zip')
            content: Bytes or string content to write
        """
        try:
            debug_dir = Path(self.data_dir) / 'debug'
            debug_dir.mkdir(parents=True, exist_ok=True)
            dest = debug_dir / filename
            mode = 'wb' if isinstance(content, bytes) else 'w'
            with open(dest, mode) as f:
                f.write(content)
            logger.debug(f"[DEBUG] Saved payload to {dest}")
        except Exception as e:
            logger.warning(f"Could not save debug payload: {e}")

    def should_upload(self, flight_data: FlightData) -> bool:
        """
        Determine if this flight should be uploaded.

        Override to implement service-specific filtering logic.

        Args:
            flight_data: Parsed flight data

        Returns:
            True if flight should be uploaded
        """
        return True


class AuthenticationError(Exception):
    """Raised when authentication with upload service fails."""
    pass


class UploadError(Exception):
    """Raised when upload to service fails."""
    pass


class DuplicateFlightError(UploadError):
    """Raised when flight has already been uploaded."""
    pass
