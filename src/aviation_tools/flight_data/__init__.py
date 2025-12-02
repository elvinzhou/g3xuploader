"""
Flight data processing module.

Provides manufacturer-agnostic flight data processing including:
- Parsing log files from various avionics systems
- Calculating Hobbs/Tach times
- Detecting OOOI events
- Determining if logs contain actual flights
"""

from aviation_tools.flight_data.base import (
    FlightDataProcessor,
    FlightData,
    FlightMetadata,
    DataPoint,
)
from aviation_tools.flight_data.processors import GarminG3XProcessor, PROCESSORS
from aviation_tools.flight_data.analyzer import FlightDataAnalyzer, FlightAnalysis

__all__ = [
    "FlightDataProcessor",
    "FlightData",
    "FlightMetadata",
    "DataPoint",
    "GarminG3XProcessor",
    "PROCESSORS",
    "FlightDataAnalyzer",
    "FlightAnalysis",
]
