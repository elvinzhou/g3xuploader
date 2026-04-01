"""
Carryd flight data uploader.

Pushes flight time data (Hobbs and engine tach) to the Carryd maintenance
tracking platform via its public API.
"""

import json
import logging
import requests
from pathlib import Path
from typing import Optional

from avcardtool.flight_data.base import FlightData, FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)

CARRYD_BASE_URL = "https://carryd.app"


class CarrydUploader(FlightDataUploader):
    """
    Upload flight time data to Carryd.

    Sends Hobbs (total airframe time) and engine tach times to the Carryd
    API after each flight. Engine logbook UUIDs must be configured in advance
    (find them under Logbook → Settings in the Carryd dashboard).

    Configuration:
        enabled: bool - Enable/disable this uploader
        api_key: str - Carryd API key (format: eal_...)
        engine_logbooks: list[str] - Ordered list of engine logbook UUIDs.
            Index 0 is the primary engine. Omit to update only total time.
    """

    SERVICE_NAME = "Carryd"

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get('api_key', '')
        self.engine_logbooks: list = config.get('engine_logbooks', [])

    def authenticate(self) -> bool:
        if not self.api_key:
            logger.error("Carryd API key not configured")
            return False
        return True

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[dict] = None
    ) -> UploadResult:
        """
        Upload flight time to Carryd.

        Maps Hobbs ending hours to totalTime and tach ending hours to
        engineTimes using the configured logbook UUIDs.

        Args:
            flight_data: Parsed flight data
            analysis_results: Dictionary from FlightDataAnalyzer.analyze_summary

        Returns:
            UploadResult with success status and details
        """
        if not self.enabled:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Carryd upload not enabled"
            )

        if not analysis_results:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="No analysis results provided"
            )

        hobbs = analysis_results.get('hobbs')
        if not hobbs:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="No Hobbs data in analysis results"
            )

        registration = analysis_results.get('aircraft_ident')
        if not registration:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="No aircraft registration in analysis results"
            )

        # Build engineTimes from tach result and configured logbook UUIDs
        engine_times = {}
        tach = analysis_results.get('tach')
        if tach and self.engine_logbooks:
            engine_times[self.engine_logbooks[0]] = tach['ending_hours']

        # Prefer wheels-off time for recordedAt, fall back to out_time then date
        recorded_at = None
        oooi = analysis_results.get('oooi')
        if oooi:
            recorded_at = oooi.get('off_time') or oooi.get('out_time')
        if not recorded_at:
            recorded_at = analysis_results.get('date')

        payload = {
            "registration": registration,
            "totalTime": hobbs['ending_hours'],
            "engineTimes": engine_times,
            "recordedAt": recorded_at,
        }

        if self.debug:
            debug_filename = f"carryd_{Path(flight_data.file_path).stem}.json"
            self._save_debug_payload(debug_filename, json.dumps(payload, indent=2, default=str))
            logger.info(f"[DEBUG] Carryd payload saved: {debug_filename}")

        if not self.api_key:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Carryd API key not configured"
            )

        try:
            response = requests.post(
                f"{CARRYD_BASE_URL}/api/v1/flight-times",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30
            )

            body = {}
            try:
                body = response.json()
            except Exception:
                pass

            if response.status_code == 200 and body.get('success'):
                aircraft = body.get('aircraft', {})
                logger.info(
                    f"Carryd upload successful: {aircraft.get('registration')} "
                    f"total time {aircraft.get('totalTime')}"
                )
                return UploadResult(
                    success=True,
                    service=self.SERVICE_NAME,
                    message="Upload successful",
                    metadata={"aircraft": aircraft}
                )
            else:
                error = body.get('error') or response.text
                error_msg = f"Upload failed: {response.status_code} - {error}"
                logger.error(f"Carryd {error_msg}")
                return UploadResult(
                    success=False,
                    service=self.SERVICE_NAME,
                    message=error_msg
                )

        except Exception as e:
            error_msg = f"Upload error: {str(e)}"
            logger.error(f"Carryd {error_msg}")
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=error_msg
            )
