"""
Savvy Aviation flight data uploader.

Stages files for upload to Savvy Aviation (no public API available).
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from avcardtool.flight_data.base import FlightData, FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)


class SavvyAviationUploader(FlightDataUploader):
    """
    Upload flight data to Savvy Aviation.

    Note: Savvy Aviation doesn't have a public API. This uploader copies files
    to a staging directory for manual upload or future automation.

    Configuration:
        enabled: bool - Enable/disable this uploader
        staging_dir: str - Directory to stage files (default: /var/lib/avcardtool/savvy_staging)
        email: str - Savvy Aviation account email (for documentation)
        password: str - Savvy Aviation account password (for future automation)
    """

    UPLOAD_URL = "https://www.savvyaviation.com/upload/"
    SERVICE_NAME = "Savvy Aviation"

    def __init__(self, config: dict):
        """
        Initialize Savvy Aviation uploader.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.email = config.get('email', '')
        self.password = config.get('password', '')

        # Staging directory for files
        data_dir = config.get('data_dir', '/var/lib/avcardtool')
        self.staging_dir = Path(config.get('staging_dir', f'{data_dir}/savvy_staging'))

    def authenticate(self) -> bool:
        """
        Verify authentication credentials.

        Returns:
            True (no authentication needed for staging)
        """
        # No authentication needed for staging to a local directory
        return True

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[dict] = None
    ) -> UploadResult:
        """
        Stage a flight file for Savvy Aviation upload.

        Savvy Aviation doesn't have a public API. This method copies the file
        to a staging directory for manual upload or future automation.

        Possible future implementations:
        1. Use browser automation (selenium)
        2. Reverse-engineer their form submission
        3. Wait for an official API

        Args:
            flight_data: Parsed flight data with file path
            analysis_results: Optional analysis results (not used)

        Returns:
            UploadResult with success status and staged file path
        """
        if not self.enabled:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Savvy Aviation upload not enabled"
            )

        try:
            # Create staging directory
            self.staging_dir.mkdir(parents=True, exist_ok=True)

            # Stage the file
            source_path = Path(flight_data.file_path)
            dest_path = self.staging_dir / source_path.name

            shutil.copy2(source_path, dest_path)

            logger.info(f"Staged {source_path.name} for Savvy Aviation upload at {dest_path}")

            return UploadResult(
                success=True,
                service=self.SERVICE_NAME,
                message=f"Staged for upload: {dest_path}",
                url=str(dest_path)
            )

        except Exception as e:
            error_msg = f"Staging error: {str(e)}"
            logger.error(f"Savvy Aviation {error_msg}")
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=error_msg
            )
