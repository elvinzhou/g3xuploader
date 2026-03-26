"""
Mock uploader for testing and debugging.

Logs the payload without actually uploading to any service.
"""

import logging
import json
from typing import Optional, Dict, Any

from avcardtool.flight_data.base.processor import FlightData
from avcardtool.flight_data.base.uploader import FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)


class MockUploader(FlightDataUploader):
    """
    Mock uploader that just logs the payload.
    Used for debugging and testing when no internet is available.
    """

    def authenticate(self) -> bool:
        """Always succeeds."""
        logger.info(f"[{self.get_name()}] Mock authentication successful")
        return True

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[Dict[str, Any]] = None
    ) -> UploadResult:
        """
        Prepare and log the payload, but don't upload.
        """
        payload = self.prepare_upload_data(flight_data, analysis_results)
        
        # Log the payload as JSON for easy inspection
        logger.info(f"[{self.get_name()}] MOCK UPLOAD PAYLOAD:")
        logger.info(json.dumps(payload, indent=2, default=str))
        
        return UploadResult(
            success=True,
            message="Mock upload successful (no data actually sent)",
            service=self.get_name(),
            upload_id="mock-12345"
        )

    def is_enabled(self) -> bool:
        """Mock uploader is always enabled if requested."""
        return True
