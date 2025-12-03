"""
CloudAhoy flight data uploader.

Uploads flight data in CSV format to CloudAhoy using their REST API.
"""

import json
import logging
import requests
from pathlib import Path
from typing import Optional

from avcardtool.flight_data.base import FlightData, FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)


class CloudAhoyUploader(FlightDataUploader):
    """
    Upload flight data to CloudAhoy.

    CloudAhoy accepts CSV files via their REST API with Bearer token authentication.

    Configuration:
        enabled: bool - Enable/disable this uploader
        api_token: str - CloudAhoy API token (Bearer token)

    API Documentation:
        https://support.foreflight.com/hc/en-us/articles/15691889029015-CloudAhoy-Data-Upload-API
    """

    API_URL = "https://www.cloudahoy.com/integration/v1/flights"
    SERVICE_NAME = "CloudAhoy"

    def __init__(self, config: dict):
        """
        Initialize CloudAhoy uploader.

        Args:
            config: Configuration dictionary with 'api_token'
        """
        super().__init__(config)
        self.api_token = config.get('api_token', '')

    def authenticate(self) -> bool:
        """
        Verify authentication credentials.

        Returns:
            True if credentials are valid
        """
        if not self.api_token:
            logger.error("CloudAhoy API token not configured")
            return False

        # CloudAhoy doesn't have a separate auth endpoint
        # Authentication is verified during upload
        return True

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[dict] = None
    ) -> UploadResult:
        """
        Upload flight data to CloudAhoy.

        Args:
            flight_data: Parsed flight data with file path
            analysis_results: Optional analysis results (not used by CloudAhoy)

        Returns:
            UploadResult with success status and details
        """
        if not self.enabled:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="CloudAhoy upload not enabled"
            )

        if not self.api_token:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="CloudAhoy API token not configured"
            )

        try:
            headers = {
                "Authorization": f"Bearer {self.api_token}"
            }

            metadata = {
                "importerVersion": "avcardtool_v2.0",
                "tail": flight_data.metadata.aircraft_ident
            }

            with open(flight_data.file_path, 'rb') as f:
                files = {
                    'IMPORT': (Path(flight_data.file_path).name, f, 'text/csv'),
                }
                data = {
                    'METADATA': json.dumps(metadata)
                }

                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=60
                )

            if response.status_code == 200:
                result = response.json()
                flight_key = result.get('key', '')
                debrief_url = f"https://www.cloudahoy.com/debrief/?key={flight_key}"
                logger.info(f"Successfully uploaded to CloudAhoy: {debrief_url}")
                return UploadResult(
                    success=True,
                    service=self.SERVICE_NAME,
                    message=f"Upload successful",
                    upload_id=flight_key,
                    url=debrief_url
                )
            else:
                error_msg = f"Upload failed: {response.status_code} - {response.text}"
                logger.error(f"CloudAhoy {error_msg}")
                return UploadResult(
                    success=False,
                    service=self.SERVICE_NAME,
                    message=error_msg
                )

        except Exception as e:
            error_msg = f"Upload error: {str(e)}"
            logger.error(f"CloudAhoy {error_msg}")
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=error_msg
            )
