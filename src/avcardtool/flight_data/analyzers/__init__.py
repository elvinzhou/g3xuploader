"""
Flight data analyzers.

Manufacturer-agnostic analyzers for calculating Hobbs/Tach times,
detecting OOOI events, and determining if logs contain actual flights.
"""

from avcardtool.flight_data.analyzers.hobbs import HobbsCalculator, HobbsResult
from avcardtool.flight_data.analyzers.tach import TachCalculator, TachResult
from avcardtool.flight_data.analyzers.oooi import OOOIDetector, OOOIResult
from avcardtool.flight_data.analyzers.flight_detector import FlightDetector, FlightDetectionResult

__all__ = [
    "HobbsCalculator",
    "HobbsResult",
    "TachCalculator",
    "TachResult",
    "OOOIDetector",
    "OOOIResult",
    "FlightDetector",
    "FlightDetectionResult",
]
