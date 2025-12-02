"""
OOOI (Out/Off/On/In) time detector.

Detects key flight events:
- Out: Engine start
- Off: Takeoff
- On: Landing
- In: Engine stop
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging

from aviation_tools.flight_data.base import FlightData
from aviation_tools.core.config import OOOIConfig

logger = logging.getLogger(__name__)


@dataclass
class OOOIResult:
    """Result of OOOI detection."""
    out_time: Optional[datetime] = None
    off_time: Optional[datetime] = None
    on_time: Optional[datetime] = None
    in_time: Optional[datetime] = None

    @property
    def block_time_minutes(self) -> Optional[float]:
        """Calculate block time (Out to In) in minutes."""
        if self.out_time and self.in_time:
            return (self.in_time - self.out_time).total_seconds() / 60
        return None

    @property
    def flight_time_minutes(self) -> Optional[float]:
        """Calculate flight time (Off to On) in minutes."""
        if self.off_time and self.on_time:
            return (self.on_time - self.off_time).total_seconds() / 60
        return None


class OOOIDetector:
    """
    Detects Out/Off/On/In times from flight data.

    Events:
    - OUT: Engine start (RPM and oil pressure exceed thresholds)
    - OFF: Takeoff (ground speed exceeds takeoff threshold)
    - ON: Landing (ground speed drops below landing threshold after being airborne)
    - IN: Engine stop (RPM drops to idle)
    """

    def __init__(self, config: OOOIConfig):
        """
        Initialize OOOI detector.

        Args:
            config: OOOI detection configuration
        """
        self.config = config

    def detect(self, flight_data: FlightData) -> OOOIResult:
        """
        Detect OOOI times from flight data.

        Args:
            flight_data: Parsed flight data

        Returns:
            OOOIResult with detected times
        """
        result = OOOIResult()

        # State tracking
        engine_was_running = False
        was_above_takeoff_speed = False
        prev_rpm = 0

        for data_point in flight_data.data_points:
            rpm = data_point.rpm or 0
            oil_press = data_point.oil_pressure or 0
            ground_speed = data_point.ground_speed_kts or 0
            timestamp = data_point.timestamp

            # Check if engine is running
            engine_running = (
                rpm > self.config.engine_start_rpm and
                oil_press > self.config.engine_start_oil_psi
            )

            # OUT: Engine start
            if engine_running and not engine_was_running and result.out_time is None:
                result.out_time = timestamp
                logger.info(f"OUT detected at {timestamp}")

            # OFF: Takeoff
            if ground_speed > self.config.takeoff_speed_kts:
                if not was_above_takeoff_speed and result.off_time is None:
                    result.off_time = timestamp
                    logger.info(f"OFF detected at {timestamp}")
                was_above_takeoff_speed = True

            # ON: Landing
            if was_above_takeoff_speed and ground_speed < self.config.landing_speed_kts:
                if ground_speed > 0:  # Still moving (just landed, not stopped)
                    result.on_time = timestamp
                    logger.info(f"ON detected at {timestamp}")
                    was_above_takeoff_speed = False

            # IN: Engine stop
            if rpm <= 10 and prev_rpm > self.config.engine_stop_rpm:
                result.in_time = timestamp
                logger.info(f"IN detected at {timestamp}")

            # Update state
            prev_rpm = rpm
            engine_was_running = engine_running

        # Log summary
        if result.block_time_minutes:
            logger.info(f"Block time: {result.block_time_minutes:.1f} minutes")
        if result.flight_time_minutes:
            logger.info(f"Flight time: {result.flight_time_minutes:.1f} minutes")

        return result
