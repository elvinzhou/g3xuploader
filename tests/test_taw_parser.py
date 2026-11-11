"""Tests for TAW parser.

These build fixtures in the *real* Garmin TAW binary format the parser accepts
(5-byte ``wAt.d`` / ``pWa.d`` magic, a fixed separator, ``F``/``R`` section
markers), reverse-engineered from jdmtool.  An earlier version of this suite
targeted a placeholder ``TAW\\0`` layout that the parser no longer supports.
"""

import pytest
from pathlib import Path
import struct
import io

from avcardtool.navdata.garmin.taw_parser import (
    TAWParser, TAWExtractor, TAWFile, TAWHeader, TAWRegion,
    TAWParseError, TAW_REGION_PATHS, TAWRegionType,
)


# ---------------------------------------------------------------------------
# Real-format TAW builder (mirrors taw_parser._parse_header/_parse_regions)
# ---------------------------------------------------------------------------

# 15-byte separator that follows the magic (taw_parser._TAW_SEPARATOR).
_SEPARATOR = b'\x00\x02\x00\x00\x00Dd\x00\x1b\x00\x00\x00A\xc8\x00'
_MAGIC2 = b'KpGrd'   # second magic embedded in the header


def build_taw(
    magic: bytes = b'wAt.d',
    db_type: int = 100,
    year: int = 24,
    cycle: int = 13,
    avionics: str = 'Test Avionics',
    coverage: str = 'US Coverage',
    db_type_name: str = 'NavData',
    regions=(),
) -> bytes:
    """
    Build a minimal, valid TAW file.

    Args:
        regions: iterable of ``(region_id, data_bytes)`` — data is stored raw
                 (the parser falls back to raw bytes when zlib fails).
    """
    text = (avionics.encode() + b'\x00'
            + coverage.encode() + b'\x00'
            + db_type_name.encode() + b'\x00')
    # metadata[2] != 0 selects the layout: year=metadata[4], cycle=metadata[6].
    metadata = (struct.pack('<H', db_type) + b'\x01\x00'
                + bytes([year]) + b'\x00' + bytes([cycle]) + b'\x00' + text)

    b = io.BytesIO()
    b.write(magic)                              # [5]  magic
    b.write(_SEPARATOR)                          # [15] separator
    b.write(b'\x00' * 25)                       # [25] sqa1 (ignored)
    b.write(struct.pack('<I', len(metadata)))    # [4]  meta_len
    b.write(b'F')                                # [1]  section marker
    b.write(metadata)                            # [N]  metadata
    b.write(b'\x00' * 4)                        # [4]  remaining (skipped)
    b.write(b'R')                                # [1]  section marker
    b.write(_MAGIC2)                             # [5]  second magic
    b.write(_SEPARATOR)                          # [15] separator again
    b.write(b'\x00' * 25)                       # [25] sqa2 (ignored)

    for region_id, data in regions:
        sect_size = 1 + 2 + 4 + 4 + len(data)    # informational; parser uses data_size
        b.write(struct.pack('<I', sect_size))    # [4]  sect_size
        b.write(b'R')                            # [1]  region marker
        b.write(struct.pack('<H', region_id))    # [2]  region id
        b.write(b'\x00' * 4)                     # [4]  unknown
        b.write(struct.pack('<I', len(data)))    # [4]  data_size
        b.write(data)                            # [N]  data

    b.write(struct.pack('<I', 1))                # stop section: sect_size
    b.write(b'S')                                # stop marker
    return b.getvalue()


class TestTAWParser:
    """Tests for TAWParser class"""

    def test_magic_validation(self, tmp_path):
        """Parser rejects files with invalid magic"""
        invalid_file = tmp_path / "invalid.taw"
        invalid_file.write_bytes(b"XXXX" + b"\x00" * 100)

        parser = TAWParser()
        with pytest.raises(TAWParseError) as exc:
            parser.parse(invalid_file)

        assert "Invalid magic" in str(exc.value)

    def test_taw_magic_accepted(self, tmp_path):
        """The 'wAt.d' magic is accepted and metadata decoded"""
        taw_file = tmp_path / "test.taw"
        taw_file.write_bytes(build_taw(magic=b'wAt.d'))

        result = TAWParser().parse(taw_file)

        assert result.header.magic == b'wAt.d'
        assert result.header.database_type == 100
        assert result.header.year == 24
        assert result.header.cycle == 13
        assert result.header.avionics == 'Test Avionics'
        assert result.header.coverage == 'US Coverage'
        assert result.header.db_type_name == 'NavData'
        assert result.header.cycle_string == '2413'

    def test_awp_magic_accepted(self, tmp_path):
        """The 'pWa.d' magic is accepted"""
        awp_file = tmp_path / "test.awp"
        awp_file.write_bytes(build_taw(magic=b'pWa.d', avionics='GNS 430W/530W'))

        result = TAWParser().parse(awp_file)

        assert result.header.magic == b'pWa.d'
        assert result.header.avionics == 'GNS 430W/530W'

    def test_regions_parsed(self, tmp_path):
        """Region sections are read up to the 'S' stop marker"""
        taw_file = tmp_path / "test.taw"
        taw_file.write_bytes(build_taw(regions=[
            (0x01, b'nav-data'),
            (0x22, b'terrain-data'),
        ]))

        result = TAWParser().parse(taw_file)

        assert [r.region_type for r in result.regions] == [0x01, 0x22]
        assert result.regions[0].dest_path == "ldr_sys/avtn_db.bin"
        assert result.regions[1].dest_path == "terrain_9as.tdb"


class TestTAWRegion:
    """Tests for TAWRegion class"""

    def test_type_name_known(self):
        """Known region types have names"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.type_name == "NAVIGATION"

    def test_type_name_unknown(self):
        """Unknown region types have fallback names"""
        region = TAWRegion(
            region_type=0xFF,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.type_name == "UNKNOWN_ff"

    def test_output_path_from_dest_path(self):
        """Output path uses dest_path when available"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path="custom/path/file.bin"
        )
        assert region.output_path == "custom/path/file.bin"

    def test_output_path_fallback(self):
        """Output path falls back to default mapping"""
        region = TAWRegion(
            region_type=0x01,
            offset=0,
            compressed_size=100,
            uncompressed_size=200,
            dest_path=""
        )
        assert region.output_path == TAW_REGION_PATHS[0x01]


class TestTAWExtractor:
    """Tests for TAWExtractor class"""

    def test_list_contents(self, tmp_path):
        """Listing TAW contents without extracting"""
        taw_file = tmp_path / "test.taw"
        taw_file.write_bytes(build_taw(regions=[(0x01, b'payload')]))

        extractor = TAWExtractor()
        result = extractor.list_contents(taw_file)

        assert isinstance(result, TAWFile)
        assert result.filepath == taw_file
        assert len(result.regions) == 1

    def test_extract_to_directory_roundtrips_raw_region(self, tmp_path):
        """Raw (uncompressed) region data is written out byte-for-byte"""
        taw_file = tmp_path / "test.taw"
        taw_file.write_bytes(build_taw(regions=[(0x01, b'Hello, World!')]))

        out_dir = tmp_path / "sdcard"
        extractor = TAWExtractor()
        written = extractor.extract_to_directory(taw_file, out_dir, overwrite=True)

        expected = out_dir / "ldr_sys" / "avtn_db.bin"
        assert expected in written
        assert expected.read_bytes() == b'Hello, World!'

    def test_extract_single_region(self, tmp_path):
        """A single region can be extracted by type"""
        taw_file = tmp_path / "test.taw"
        taw_file.write_bytes(build_taw(regions=[(0x01, b'nav-bytes')]))

        out_file = tmp_path / "out.bin"
        assert TAWExtractor().extract_single_region(taw_file, 0x01, out_file)
        assert out_file.read_bytes() == b'nav-bytes'


class TestTAWRegionPaths:
    """Tests for TAW region path mappings"""

    def test_navigation_path(self):
        """Navigation database path"""
        assert TAW_REGION_PATHS[0x01] == "ldr_sys/avtn_db.bin"

    def test_terrain_9as_path(self):
        """Terrain tile database path (matches jdmtool and real tg3xt TAW files)"""
        assert TAW_REGION_PATHS[0x22] == "terrain_9as.tdb"

    def test_fc_tpc_path(self):
        """FliteCharts TPC path"""
        assert TAW_REGION_PATHS[0x14] == "fc_tpc/fc_tpc.dat"

    def test_rasters_path(self):
        """Rasters path"""
        assert TAW_REGION_PATHS[0x1A] == "rasters/rasters.xml"


# Fixtures

@pytest.fixture
def sample_taw_file(tmp_path):
    """A valid single-region TAW file in the real Garmin format."""
    taw_file = tmp_path / "sample.taw"
    taw_file.write_bytes(build_taw(regions=[(0x01, b'Hello, World!')]))
    return taw_file


class TestFatattr:
    """FAT attributes can only be set on FAT; elsewhere skip quietly after the first try."""

    def _run(self, stderr, returncode=1):
        from types import SimpleNamespace
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=returncode, stderr=stderr.encode())
        return fake, calls

    def test_non_fat_filesystem_tried_once_then_skipped(self, tmp_path, monkeypatch, caplog):
        from avcardtool.navdata.garmin import taw_parser
        monkeypatch.setattr(taw_parser, "_NON_FAT_DEVICES", set())
        fake, calls = self._run("x.jnx: Inappropriate ioctl for device")
        monkeypatch.setattr("subprocess.run", fake)
        files = [tmp_path / f"f{i}.jnx" for i in range(5)]
        for f in files:
            f.write_bytes(b"x")

        with caplog.at_level("INFO"):
            for f in files:
                taw_parser._clear_readonly(f)
                taw_parser._set_hidden(f)

        assert len(calls) == 1
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
        assert sum("not on a FAT filesystem" in r.message for r in caplog.records) == 1

    def test_other_failures_still_warn_every_time(self, tmp_path, monkeypatch, caplog):
        from avcardtool.navdata.garmin import taw_parser
        monkeypatch.setattr(taw_parser, "_NON_FAT_DEVICES", set())
        fake, calls = self._run("Permission denied")
        monkeypatch.setattr("subprocess.run", fake)
        f = tmp_path / "f.jnx"
        f.write_bytes(b"x")

        taw_parser._set_hidden(f)
        taw_parser._set_hidden(f)

        assert len(calls) == 2
        assert sum(r.levelname == "WARNING" for r in caplog.records) == 2


# ---------------------------------------------------------------------------
# Large regions stream to the card; feat_unlk CRC matches the reference
# ---------------------------------------------------------------------------

import os  # noqa: E402
from unittest import mock  # noqa: E402
import zlib as _zlib  # noqa: E402

from avcardtool.navdata.garmin import taw_parser as _taw_mod  # noqa: E402
from avcardtool.navdata.garmin.taw_parser import TAWExtractor  # noqa: E402


def test_large_raw_region_is_streamed_not_loaded(tmp_path, monkeypatch):
    payload = os.urandom(300_000)
    taw = tmp_path / "x.taw"
    taw.write_bytes(build_taw(regions=[(0x22, payload)]))   # terrain_9as.tdb

    monkeypatch.setattr(_taw_mod, "STREAM_REGION_THRESHOLD", 1000)
    monkeypatch.setattr(_taw_mod, "STREAM_CHUNK_SIZE", 4096)
    ex = TAWExtractor()
    with mock.patch.object(ex.parser, "extract_region",
                           side_effect=AssertionError("whole region loaded")):
        out = tmp_path / "card"
        ex.extract_to_directory(taw, out)

    assert (out / "terrain_9as.tdb").read_bytes() == payload


def test_large_zlib_region_still_decompressed(tmp_path, monkeypatch):
    original = b"chart data " * 50_000
    taw = tmp_path / "x.taw"
    taw.write_bytes(build_taw(regions=[(0x22, _zlib.compress(original))]))

    monkeypatch.setattr(_taw_mod, "STREAM_REGION_THRESHOLD", 1000)
    out = tmp_path / "card"
    TAWExtractor().extract_to_directory(taw, out)

    assert (out / "terrain_9as.tdb").read_bytes() == original


def test_truncated_streamed_region_is_an_error(tmp_path, monkeypatch):
    raw = build_taw(regions=[(0x22, os.urandom(100_000))])
    taw = tmp_path / "x.taw"
    taw.write_bytes(raw[:-60_000])  # cut mid-region (stop marker lost too)

    monkeypatch.setattr(_taw_mod, "STREAM_REGION_THRESHOLD", 1000)
    out = tmp_path / "card"
    ex = TAWExtractor()
    ex.extract_to_directory(taw, out)   # logs the error; must not write a bogus full file
    assert not (out / "terrain_9as.tdb").exists() or \
        (out / "terrain_9as.tdb").stat().st_size < 100_000


def test_feat_unlk_checksum_matches_reference_implementation():
    from avcardtool.navdata.garmin.feat_unlk import (
        _feat_unlk_checksum_reference, feat_unlk_checksum,
    )
    for n in (0, 1, 85, 913, 32768, 250_000):
        data = os.urandom(n)
        for init in (0xFFFFFFFF, 0, 0x12345678):
            assert feat_unlk_checksum(data, init) == _feat_unlk_checksum_reference(data, init)
    # chunked use (as in the file verifier) equals one pass
    data = os.urandom(100_000)
    chk = 0xFFFFFFFF
    for i in range(0, len(data), 32768):
        chk = feat_unlk_checksum(data[i:i + 32768], chk)
    assert chk == feat_unlk_checksum(data)
