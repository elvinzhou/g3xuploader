"""
Avionics firmware (system software) handling.

Checks Garmin's public download pages for new versions, caches the packages,
and writes them to SD cards. See ARCHITECTURE.md ("Firmware Updates").
"""

from avcardtool.firmware.registry import PRODUCTS, Product, FirmwareError

__all__ = ["PRODUCTS", "Product", "FirmwareError"]
