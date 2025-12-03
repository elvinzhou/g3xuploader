"""
Main flight analyzer that orchestrates all analysis modules.
"""

from dataclasses import dataclass
from typing import Optional
import logging

from avcardtool.flight_data.base import FlightData
from avcardtool.flight_data.analyzers import (
    FlightDetector,
    FlightDetectionResult,
    HobbsCalculator,
    HobbsResult,
    TachCalculator,
    TachResult,
    OOOIDetector,
    OOOIResult,
)
from avcardtool.core.config import FlightDataConfig

logger = logging.getLogger(__name__)


@dataclass
class FlightAnalysis:
    """Complete flight analysis results."""
    # Flight detection
    detection: FlightDetectionResult

    # Time calculations (None if not a flight)
    hobbs: Optional[HobbsResult] = None
    tach: Optional[TachResult] = None
    oooi: Optional[OOOIResult] = None

    # Metadata
    aircraft_ident: Optional[str] = None
    date: Optional[str] = None
    file_path: Optional[str] = None
    file_hash: Optional[str] = None


class FlightDataAnalyzer:
    """
    Main analyzer that orchestrates all flight data analysis.

    Coordinates:
    - Flight detection (is this an actual flight?)
    - Hobbs time calculation
    - Tach time calculation
    - OOOI event detection
    """

    def __init__(self, config: FlightDataConfig):
        """
        Initialize flight data analyzer.

        Args:
            config: Flight data configuration
        """
        self.config = config
        self.flight_detector = FlightDetector(config.flight_detection)
        self.hobbs_calculator = HobbsCalculator(config.airframe_time)
        self.tach_calculator = TachCalculator(config.engine_time)
        self.oooi_detector = OOOIDetector(config.oooi)

    def analyze(self, flight_data: FlightData) -> FlightAnalysis:
        """
        Perform complete analysis of flight data.

        Args:
            flight_data: Parsed flight data

        Returns:
            FlightAnalysis with all results
        """
        logger.info(f"Analyzing flight data from {flight_data.file_path}")

        # First, determine if this is an actual flight
        detection = self.flight_detector.analyze(flight_data)

        # Create base analysis
        analysis = FlightAnalysis(
            detection=detection,
            aircraft_ident=flight_data.metadata.aircraft_ident,
            date=flight_data.metadata.date,
            file_path=str(flight_data.file_path),
            file_hash=flight_data.file_hash
        )

        # If not a flight, return early
        if not detection.is_flight:
            logger.info(f"Not a flight: {detection.rejection_reason}")
            return analysis

        # Calculate times and detect events
        logger.info("Flight detected, calculating times...")

        analysis.hobbs = self.hobbs_calculator.calculate(flight_data)
        analysis.tach = self.tach_calculator.calculate(flight_data)
        analysis.oooi = self.oooi_detector.detect(flight_data)

        logger.info(f"Analysis complete: Hobbs +{analysis.hobbs.increment_hours:.2f}, "
                   f"Tach +{analysis.tach.increment_hours:.2f}")

        return analysis

    def analyze_summary(self, flight_data: FlightData) -> dict:
        """
        Analyze flight and return summary as dictionary.

        Useful for JSON output and API responses.

        Args:
            flight_data: Parsed flight data

        Returns:
            Dictionary with analysis summary
        """
        analysis = self.analyze(flight_data)

        summary = {
            "aircraft_ident": analysis.aircraft_ident,
            "date": analysis.date,
            "file_path": analysis.file_path,
            "file_hash": analysis.file_hash,
            "is_flight": analysis.detection.is_flight,
            "rejection_reason": analysis.detection.rejection_reason,
            "metrics": {
                "data_points": analysis.detection.data_points,
                "airborne_time_minutes": analysis.detection.airborne_time_minutes,
                "max_ground_speed_kts": analysis.detection.max_ground_speed_kts,
                "altitude_change_ft": analysis.detection.altitude_change_ft,
            }
        }

        if analysis.hobbs:
            summary["hobbs"] = {
                "starting_hours": analysis.hobbs.starting_hours,
                "increment_hours": analysis.hobbs.increment_hours,
                "ending_hours": analysis.hobbs.ending_hours,
            }

        if analysis.tach:
            summary["tach"] = {
                "starting_hours": analysis.tach.starting_hours,
                "increment_hours": analysis.tach.increment_hours,
                "ending_hours": analysis.tach.ending_hours,
            }

        if analysis.oooi:
            summary["oooi"] = {
                "out_time": analysis.oooi.out_time.isoformat() if analysis.oooi.out_time else None,
                "off_time": analysis.oooi.off_time.isoformat() if analysis.oooi.off_time else None,
                "on_time": analysis.oooi.on_time.isoformat() if analysis.oooi.on_time else None,
                "in_time": analysis.oooi.in_time.isoformat() if analysis.oooi.in_time else None,
                "block_time_minutes": analysis.oooi.block_time_minutes,
                "flight_time_minutes": analysis.oooi.flight_time_minutes,
            }

        return summary
