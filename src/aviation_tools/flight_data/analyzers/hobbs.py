"""
Hobbs (airframe time) calculator.

Calculates total airframe time based on configurable triggers.
"""

from dataclasses import dataclass
import logging

from aviation_tools.flight_data.base import FlightData
from aviation_tools.core.config import AirframeTimeConfig

logger = logging.getLogger(__name__)


@dataclass
class HobbsResult:
    """Result of Hobbs time calculation."""
    starting_hours: float
    increment_hours: float
    ending_hours: float
    recording_seconds: int


class HobbsCalculator:
    """
    Calculates Hobbs (total airframe) time.

    Supports multiple trigger modes:
    - rpm: Records when RPM > threshold
    - oil_pressure: Records when oil pressure > threshold
    - flight_time: Records when airborne (speed > threshold)
    """

    def __init__(self, config: AirframeTimeConfig):
        """
        Initialize Hobbs calculator.

        Args:
            config: Airframe time configuration
        """
        self.config = config

    def calculate(self, flight_data: FlightData) -> HobbsResult:
        """
        Calculate Hobbs time for a flight.

        Args:
            flight_data: Parsed flight data

        Returns:
            HobbsResult with starting, increment, and ending hours
        """
        starting_hours = flight_data.metadata.airframe_hours_start or 0.0
        recording_seconds = 0

        for data_point in flight_data.data_points:
            if self._should_record(data_point):
                recording_seconds += 1

        increment_hours = recording_seconds / 3600.0
        ending_hours = starting_hours + increment_hours

        logger.info(f"Hobbs: {starting_hours:.2f} + {increment_hours:.2f} = {ending_hours:.2f} "
                   f"({recording_seconds}s recorded)")

        return HobbsResult(
            starting_hours=starting_hours,
            increment_hours=increment_hours,
            ending_hours=ending_hours,
            recording_seconds=recording_seconds
        )

    def _should_record(self, data_point) -> bool:
        """
        Determine if Hobbs time should accrue for this data point.

        Args:
            data_point: Single data point

        Returns:
            True if Hobbs should record
        """
        trigger = self.config.trigger

        if trigger == "rpm":
            rpm = data_point.rpm or 0
            return rpm > self.config.rpm_threshold

        elif trigger == "oil_pressure":
            oil_press = data_point.oil_pressure or 0
            return oil_press > self.config.oil_pressure_threshold

        elif trigger == "flight_time":
            speed = data_point.ground_speed_kts or 0
            return speed > self.config.airborne_speed_threshold

        else:
            logger.warning(f"Unknown Hobbs trigger mode: {trigger}, defaulting to oil_pressure")
            oil_press = data_point.oil_pressure or 0
            return oil_press > self.config.oil_pressure_threshold
