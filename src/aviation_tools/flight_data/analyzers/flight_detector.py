"""
Flight detection analyzer.

Determines if a log file represents an actual flight or just a power-on cycle.
"""

from dataclasses import dataclass
from typing import Tuple, Optional
import logging

from aviation_tools.flight_data.base import FlightData
from aviation_tools.core.config import FlightDetectionConfig

logger = logging.getLogger(__name__)


@dataclass
class FlightDetectionResult:
    """Result of flight detection analysis."""
    is_flight: bool
    rejection_reason: Optional[str] = None
    airborne_time_minutes: float = 0.0
    max_ground_speed_kts: float = 0.0
    altitude_change_ft: float = 0.0
    data_points: int = 0


class FlightDetector:
    """
    Analyzes flight data to determine if it represents an actual flight.

    A log file may contain just a power-on cycle without actual flight.
    This analyzer applies configurable thresholds to determine if the
    aircraft actually flew.
    """

    def __init__(self, config: FlightDetectionConfig):
        """
        Initialize flight detector.

        Args:
            config: Flight detection configuration
        """
        self.config = config

    def analyze(self, flight_data: FlightData) -> FlightDetectionResult:
        """
        Analyze flight data to determine if it's an actual flight.

        Args:
            flight_data: Parsed flight data

        Returns:
            FlightDetectionResult with determination and metrics
        """
        data_points = flight_data.data_points

        # Count data points
        num_points = len(data_points)
        if num_points < self.config.minimum_data_points:
            return FlightDetectionResult(
                is_flight=False,
                rejection_reason=f"Too few data points ({num_points} < {self.config.minimum_data_points})",
                data_points=num_points
            )

        # Calculate airborne time (time above minimum speed threshold)
        airborne_seconds = sum(
            1 for dp in data_points
            if dp.ground_speed_kts and dp.ground_speed_kts > self.config.minimum_ground_speed_kts
        )
        airborne_minutes = airborne_seconds / 60.0

        if airborne_minutes < self.config.minimum_flight_time_minutes:
            return FlightDetectionResult(
                is_flight=False,
                rejection_reason=f"Airborne time too short ({airborne_minutes:.1f} < {self.config.minimum_flight_time_minutes} min)",
                airborne_time_minutes=airborne_minutes,
                data_points=num_points
            )

        # Find max ground speed
        max_speed = max(
            (dp.ground_speed_kts for dp in data_points if dp.ground_speed_kts is not None),
            default=0.0
        )

        if max_speed < self.config.minimum_ground_speed_kts:
            return FlightDetectionResult(
                is_flight=False,
                rejection_reason=f"Max ground speed too low ({max_speed:.1f} < {self.config.minimum_ground_speed_kts} kts)",
                airborne_time_minutes=airborne_minutes,
                max_ground_speed_kts=max_speed,
                data_points=num_points
            )

        # Find altitude change
        valid_altitudes = [
            dp.altitude_ft for dp in data_points
            if dp.altitude_ft is not None and dp.altitude_ft > -1000  # Filter invalid altitudes
        ]

        if not valid_altitudes:
            return FlightDetectionResult(
                is_flight=False,
                rejection_reason="No valid altitude data",
                airborne_time_minutes=airborne_minutes,
                max_ground_speed_kts=max_speed,
                data_points=num_points
            )

        min_altitude = min(valid_altitudes)
        max_altitude = max(valid_altitudes)
        altitude_change = max_altitude - min_altitude

        if altitude_change < self.config.minimum_altitude_change_ft:
            return FlightDetectionResult(
                is_flight=False,
                rejection_reason=f"Altitude change too small ({altitude_change:.0f} < {self.config.minimum_altitude_change_ft} ft)",
                airborne_time_minutes=airborne_minutes,
                max_ground_speed_kts=max_speed,
                altitude_change_ft=altitude_change,
                data_points=num_points
            )

        # All checks passed - this is a flight!
        logger.info(f"Flight detected: {airborne_minutes:.1f} min airborne, "
                   f"{max_speed:.1f} kts max, {altitude_change:.0f} ft climb")

        return FlightDetectionResult(
            is_flight=True,
            rejection_reason=None,
            airborne_time_minutes=airborne_minutes,
            max_ground_speed_kts=max_speed,
            altitude_change_ft=altitude_change,
            data_points=num_points
        )
