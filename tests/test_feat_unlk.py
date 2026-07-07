"""Tests for feat_unlk.dat system-ID handling.

The G3X rejects a card with "System ID not correct" unless feat_unlk.dat stores
truncate_system_id(<avionics hardware ID>).  Garmin reports that hardware ID as a
hexadecimal value — the same one the avionics unit writes to its CSV logs
(system_id="60002CA61BD97") and that the flyGarmin API returns as device.systemId.
"""

from avcardtool.navdata.garmin.feat_unlk import parse_system_id, truncate_system_id
from avcardtool.navdata.garmin.api import _parse_device


# Ground truth taken from a real G3X CSV: system_id="60002CA61BD97"
SYSTEM_ID_STR = "60002CA61BD97"
SYSTEM_ID_INT = 0x60002CA61BD97


class TestParseSystemId:
    def test_hex_string_from_api(self):
        """A hex string (the natural flyGarmin form) parses as base-16."""
        assert parse_system_id(SYSTEM_ID_STR) == SYSTEM_ID_INT

    def test_hex_string_with_0x_prefix(self):
        assert parse_system_id("0x" + SYSTEM_ID_STR) == SYSTEM_ID_INT

    def test_surrounding_whitespace(self):
        assert parse_system_id(f"  {SYSTEM_ID_STR} ") == SYSTEM_ID_INT

    def test_int_passthrough(self):
        """If the API returns a JSON number it is already the hardware ID."""
        assert parse_system_id(SYSTEM_ID_INT) == SYSTEM_ID_INT

    def test_none_and_empty(self):
        assert parse_system_id(None) is None
        assert parse_system_id("") is None
        assert parse_system_id("   ") is None

    def test_zero_is_treated_as_missing(self):
        """'0' is a placeholder, never a real System ID — must not sign with 0."""
        assert parse_system_id("0") is None

    def test_bool_is_not_a_system_id(self):
        assert parse_system_id(True) is None

    def test_unparseable_returns_none(self):
        assert parse_system_id("not-a-number") is None


class TestTruncateSystemId:
    def test_matches_low_plus_high_halves(self):
        expected = ((SYSTEM_ID_INT & 0xFFFFFFFF) + (SYSTEM_ID_INT >> 32)) & 0xFFFFFFFF
        assert truncate_system_id(SYSTEM_ID_INT) == expected

    def test_result_fits_in_four_bytes(self):
        """CONTENT2 stores the value as 4 little-endian bytes."""
        value = truncate_system_id(SYSTEM_ID_INT)
        assert 0 <= value <= 0xFFFFFFFF
        value.to_bytes(4, "little")  # must not raise


class TestDeviceParsing:
    def test_string_system_id_is_captured(self):
        """Regression: a string systemId used to drop to None (signed with 0)."""
        dev = _parse_device({"id": 1, "name": "GDU 460", "systemId": SYSTEM_ID_STR})
        assert dev.system_id == SYSTEM_ID_STR
        assert dev.system_id_raw == SYSTEM_ID_INT

    def test_int_system_id_still_works(self):
        dev = _parse_device({"id": 1, "name": "GDU 460", "systemId": SYSTEM_ID_INT})
        assert dev.system_id_raw == SYSTEM_ID_INT

    def test_missing_system_id(self):
        dev = _parse_device({"id": 1, "name": "GDU 460"})
        assert dev.system_id_raw is None
