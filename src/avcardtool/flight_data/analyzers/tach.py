"""
Tach (engine time) calculator.

Calculates engine time based on configurable mode.
"""

from dataclasses import dataclass
import logging

from avcardtool.flight_data.base import FlightData
from avcardtool.core.config import EngineTimeConfig

logger = logging.getLogger(__name__)


@dataclass
class TachResult:
    """Result of Tach time calculation."""
    starting_hours: float
    increment_hours: float
    ending_hours: float
    recording_seconds: float  # Can be fractional in variable mode


class TachCalculator:
    """
    Calculates Tach (engine) time.

    Supports two modes:
    - variable: Time accrues at (RPM / reference_rpm) rate
      Example: At 2700 RPM with reference 2700: 1 hour flight = 1.0 tach hour
               At 2400 RPM with reference 2700: 1 hour flight = 0.89 tach hour
    - fixed: Time accrues at 1:1 when RPM > minimum_recording_rpm
    """

    def __init__(self, config: EngineTimeConfig):
        """
        Initialize Tach calculator.

        Args:
            config: Engine time configuration
        """
        self.config = config

    def calculate(self, flight_data: FlightData) -> TachResult:
        """
        Calculate Tach time for a flight.

        Args:
            flight_data: Parsed flight data

        Returns:
            TachResult with starting, increment, and ending hours
        """
        starting_hours = flight_data.metadata.engine_hours_start or 0.0
        recording_seconds = 0.0

        if self.config.mode == "fixed":
            # Fixed mode: 1 second = 1 second when RPM > threshold
            for data_point in flight_data.data_points:
                rpm = data_point.rpm or 0
                if rpm >= self.config.minimum_recording_rpm:
                    recording_seconds += 1.0

        else:  # variable mode
            # Variable mode: time accrues at (RPM / reference_rpm) rate
            for data_point in flight_data.data_points:
                rpm = data_point.rpm or 0
                if rpm >= self.config.minimum_recording_rpm:
                    rate = rpm / self.config.reference_rpm
                    recording_seconds += rate

        increment_hours = recording_seconds / 3600.0
        ending_hours = starting_hours + increment_hours

        logger.info(f"Tach ({self.config.mode}): {starting_hours:.2f} + {increment_hours:.2f} = "
                   f"{ending_hours:.2f} ({recording_seconds:.1f}s recorded)")

        return TachResult(
            starting_hours=starting_hours,
            increment_hours=increment_hours,
            ending_hours=ending_hours,
            recording_seconds=recording_seconds
        )
