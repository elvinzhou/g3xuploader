"""
Aviation Tools - Unified flight data processing and navigation database management

A modular system for processing flight logs from various manufacturers and managing
aviation navigation databases.
"""

__version__ = "1.7.1"
__author__ = "Aviation Tools Contributors"

from avcardtool.core.config import Config
from avcardtool.core.utils import setup_logging

__all__ = ["Config", "setup_logging", "__version__"]
