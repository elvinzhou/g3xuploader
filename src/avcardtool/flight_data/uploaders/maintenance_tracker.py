"""
Maintenance Tracker flight data uploader.

Generic webhook uploader for maintenance tracking systems.
"""

import logging
import requests
from typing import Optional

from avcardtool.flight_data.base import FlightData, FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)


class MaintenanceTrackerUploader(FlightDataUploader):
    """
    Upload flight data summary to a custom maintenance tracker.

    This is a generic webhook uploader that sends flight analysis data
    (Hobbs, Tach, OOOI times) to a custom HTTP endpoint.

    Configuration:
        enabled: bool - Enable/disable this uploader
        url: str - Webhook URL
        api_key: str - API key for authentication (sent as Bearer token)
        headers: dict - Additional custom headers (optional)
    """

    SERVICE_NAME = "Maintenance Tracker"

    def __init__(self, config: dict):
        """
        Initialize maintenance tracker uploader.

        Args:
            config: Configuration dictionary with url and api_key
        """
        super().__init__(config)
        self.url = config.get('url', '')
        self.api_key = config.get('api_key', '')
        self.custom_headers = config.get('headers', {})

    def authenticate(self) -> bool:
        """
        Verify authentication credentials.

        Returns:
            True if URL and API key are configured
        """
        if not self.url or not self.api_key:
            logger.error("Maintenance tracker URL or API key not configured")
            return False
        return True

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[dict] = None
    ) -> UploadResult:
        """
        Upload flight summary to maintenance tracker.

        Sends a JSON payload with flight analysis data including Hobbs, Tach,
        and OOOI times to the configured webhook URL.

        Args:
            flight_data: Parsed flight data
            analysis_results: Dictionary with analysis results (from FlightDataAnalyzer.analyze_summary)

        Returns:
            UploadResult with success status and details
        """
        if not self.enabled:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Maintenance tracker upload not enabled"
            )

        if not self.url or not self.api_key:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Maintenance tracker URL or API key not configured"
            )

        if not analysis_results:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="No analysis results provided"
            )

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            # Add any custom headers
            headers.update(self.custom_headers)

            # Build payload from analysis results
            payload = {
                "aircraft_ident": analysis_results.get('aircraft_ident'),
                "date": analysis_results.get('date'),
                "file_path": str(flight_data.file_path),
                "file_hash": flight_data.file_hash,
                "hobbs": analysis_results.get('hobbs'),
                "tach": analysis_results.get('tach'),
                "oooi": analysis_results.get('oooi'),
                "metrics": analysis_results.get('metrics')
            }

            response = requests.post(
                self.url,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code in [200, 201]:
                logger.info(f"Successfully uploaded to maintenance tracker")
                return UploadResult(
                    success=True,
                    service=self.SERVICE_NAME,
                    message="Upload successful"
                )
            else:
                error_msg = f"Upload failed: {response.status_code} - {response.text}"
                logger.error(f"Maintenance tracker {error_msg}")
                return UploadResult(
                    success=False,
                    service=self.SERVICE_NAME,
                    message=error_msg
                )

        except Exception as e:
            error_msg = f"Upload error: {str(e)}"
            logger.error(f"Maintenance tracker {error_msg}")
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=error_msg
            )
