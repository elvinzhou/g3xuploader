"""Tests for FlyGarminAPI.download_file: interrupt-safe, size-verified downloads."""

from unittest import mock

import pytest

from avcardtool.navdata.garmin.api import DatabaseFile, FlyGarminAPI
from avcardtool.navdata.garmin.auth import GarminAPIError

URL = "https://avdb.garmin.com/files/nav2610.taw"
BODY = b"x" * 200_000  # several 64 KiB chunks


class FakeResponse:
    def __init__(self, body=b"", headers=None, status_code=200, fail_after=None):
        self.body = body
        self.headers = headers or {}
        self.status_code = status_code
        self.fail_after = fail_after  # raise mid-stream after this many bytes

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        sent = 0
        for i in range(0, len(self.body), chunk_size):
            if self.fail_after is not None and sent >= self.fail_after:
                raise ConnectionError("connection reset")
            chunk = self.body[i:i + chunk_size]
            sent += len(chunk)
            yield chunk


def make_api(head_size=len(BODY), get_response=None):
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    head_headers = {"content-length": str(head_size)} if head_size else {}
    api._session.head.return_value = FakeResponse(headers=head_headers)
    api._session.get.return_value = get_response or FakeResponse(
        BODY, {"content-length": str(len(BODY))}
    )
    return api


def db_file(size=len(BODY)):
    return DatabaseFile(url=URL, file_size=size, destination=None)


def test_downloads_to_final_name(tmp_path):
    api = make_api()
    dest = api.download_file(db_file(), tmp_path)

    assert dest == tmp_path / "nav2610.taw"
    assert dest.read_bytes() == BODY
    assert not (tmp_path / "nav2610.taw.part").exists()


def test_skips_file_already_complete(tmp_path):
    (tmp_path / "nav2610.taw").write_bytes(BODY)
    api = make_api()
    progress = mock.Mock()

    dest = api.download_file(db_file(), tmp_path, progress_callback=progress)

    assert dest.read_bytes() == BODY
    api._session.get.assert_not_called()
    progress.assert_called_once_with(len(BODY), len(BODY))


def test_redownloads_file_with_wrong_size(tmp_path):
    (tmp_path / "nav2610.taw").write_bytes(b"truncated")
    api = make_api()

    dest = api.download_file(db_file(), tmp_path)

    api._session.get.assert_called_once()
    assert dest.read_bytes() == BODY


def test_no_skip_without_server_size(tmp_path):
    """Without a HEAD size there's nothing trustworthy to compare against."""
    (tmp_path / "nav2610.taw").write_bytes(BODY)
    api = make_api(head_size=None)

    api.download_file(db_file(), tmp_path)

    api._session.get.assert_called_once()


def test_short_download_raises_and_leaves_nothing(tmp_path):
    # Server promises the full size but the stream ends early
    short = FakeResponse(BODY[:150_000], {"content-length": str(len(BODY))})
    api = make_api(get_response=short)

    with pytest.raises(GarminAPIError, match="Incomplete download.*150,000 of 200,000"):
        api.download_file(db_file(), tmp_path)

    assert not (tmp_path / "nav2610.taw").exists()
    assert not (tmp_path / "nav2610.taw.part").exists()


def test_connection_drop_keeps_previous_complete_file_untouched(tmp_path):
    """An interrupted re-download must never clobber or truncate the final file."""
    final = tmp_path / "nav2610.taw"
    final.write_bytes(b"older complete copy")
    drop = FakeResponse(BODY, {"content-length": str(len(BODY))}, fail_after=70_000)
    api = make_api(get_response=drop)

    with pytest.raises(ConnectionError):
        api.download_file(db_file(), tmp_path)

    assert final.read_bytes() == b"older complete copy"
    assert not (tmp_path / "nav2610.taw.part").exists()


def test_falls_back_to_head_size_without_content_length(tmp_path):
    short = FakeResponse(BODY[:1000], {})  # no content-length on GET
    api = make_api(get_response=short)

    with pytest.raises(GarminAPIError, match="1,000 of 200,000"):
        api.download_file(db_file(), tmp_path)


def test_compressed_transfer_skips_size_check(tmp_path):
    """With gzip transfer-encoding the decoded byte count legitimately differs."""
    gz = FakeResponse(BODY, {"content-encoding": "gzip", "content-length": "5000"})
    api = make_api(head_size=5000, get_response=gz)

    dest = api.download_file(db_file(), tmp_path)

    assert dest.read_bytes() == BODY


def test_no_size_known_at_all_still_downloads(tmp_path):
    api = make_api(head_size=None, get_response=FakeResponse(BODY, {}))
    dest = api.download_file(db_file(size=0), tmp_path)
    assert dest.read_bytes() == BODY


def test_unlock_does_not_send_card_serial():
    """flyGarmin rejected cardSerial with 400 for many series; it's never needed."""
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    ok = mock.Mock(status_code=200)
    ok.json.return_value = {"unlockCodes": []}
    api._session.get.return_value = ok

    api.unlock(1298, "20T1", 4244058, batch_id="abc")

    api._session.get.assert_called_once()
    _, kwargs = api._session.get.call_args
    assert kwargs["params"] == {"deviceIDs": 4244058}
    assert kwargs["headers"] == {"Authorization": 'BatchUpdate id="abc"'}


# --- Skipping other avionics' TAWs before download -----------------------------

from test_taw_parser import build_taw  # noqa: E402
from avcardtool.cli import _dl_cache_key  # noqa: E402
from avcardtool.navdata.garmin.taw_parser import TAWParser, TAWParseError  # noqa: E402

G3X, G600, X3 = 0x06BF, 0x01F2, 0x07EB


def test_header_parses_from_first_bytes_only():
    taw = build_taw(db_type=G600, regions=[(0x26, b"x" * 100_000)])
    assert TAWParser().parse_header_bytes(taw[:4096]).database_type == G600


def test_header_rejects_non_taw():
    with pytest.raises(TAWParseError):
        TAWParser().parse_header_bytes(b"not a taw file at all")




def _taw_file(name):
    return DatabaseFile(url=f"https://avdb.garmin.com/{name}", file_size=0, destination=None)










def test_cache_key_is_per_avionics():
    assert _dl_cache_key(2260, "26B6", G3X) == "2260/26B6@06BF"
    assert _dl_cache_key(2260, "26B6", G600) != _dl_cache_key(2260, "26B6", G3X)
    assert _dl_cache_key(2260, "26B6", None) == "2260/26B6"


# --- Reading a TAW's database CRCs without downloading it ---------------------

class RangeServer:
    """Fake avdb server that honours Range and records what was asked for."""

    def __init__(self, body, honour_range=True):
        self.body, self.honour_range, self.ranges = body, honour_range, []

    def get(self, url, headers=None, stream=False, timeout=None):
        start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
        self.ranges.append((start, end))
        if not self.honour_range:
            return FakeResponse(self.body)  # status 200: ignores Range
        return FakeResponse(self.body[start:end + 1], status_code=206)


def _crc_api(server):
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    api._session.get.side_effect = server.get
    return api


def test_peek_feature_crcs_reads_region_tails_only():
    terrain_crc = 0x3DA470A3
    taw = build_taw(db_type=0x0402, regions=[
        (0x22, b"t" * 1_000_000 + terrain_crc.to_bytes(4, "little")),  # terrain_9as.tdb
    ])
    server = RangeServer(taw)
    crcs = _crc_api(server).peek_taw_feature_crcs(_taw_file("tgtn-amrn-20T1.taw"))

    assert crcs == {"TERRAIN": terrain_crc}
    # Header + one section header + the 4-byte CRC: never the 1 MB of data
    assert max(end - start + 1 for start, end in server.ranges) <= 4096
    assert sum(end - start + 1 for start, end in server.ranges) < 5000


def test_peek_feature_crcs_ignores_server_that_ignores_range():
    taw = build_taw(regions=[(0x22, b"t" * 100 + b"\x01\x02\x03\x04")])
    server = RangeServer(taw, honour_range=False)
    assert _crc_api(server).peek_taw_feature_crcs(_taw_file("x.taw")) == {}


def test_peek_feature_crcs_non_taw_and_errors():
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    assert api.peek_taw_feature_crcs(_taw_file("SECT_N36W078.jnx")) == {}
    api._session.get.assert_not_called()

    api._session.get.side_effect = ConnectionError("boom")
    assert api.peek_taw_feature_crcs(_taw_file("x.taw")) == {}











# --- Refuse files that need Garmin-signed .sff licenses -------------------------

from avcardtool.cli import _taw_header_mismatches  # noqa: E402

GTN = 0x0402


def _multi_api(bodies):
    """Fake API whose Range reads serve several files by URL."""
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()

    def get(url, headers=None, stream=False, timeout=None):
        return RangeServer(bodies[url]).get(url, headers=headers)

    api._session.get.side_effect = get
    return api


def test_peek_reads_the_avionics_type_with_one_small_range_request():
    server = RangeServer(build_taw(db_type=G600, regions=[(0x01, b"x" * 1_000_000)]))
    assert _crc_api(server).peek_taw_database_type(_taw_file("jg600a-us-2611.taw")) == G600
    assert server.ranges == [(0, 4095)]


def test_peek_non_taw_and_errors_give_none():
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    assert api.peek_taw_database_type(_taw_file("SECT_N36W078.jnx")) is None
    api._session.get.side_effect = ConnectionError("boom")
    assert api.peek_taw_database_type(_taw_file("x.taw")) is None


def test_gtn_navigation_file_is_flagged_as_needing_licenses():
    """jg600a-*.taw (header G600) for a GTN card is installed with signed .sff licenses."""
    api = _multi_api({"https://avdb.garmin.com/jg600a-us-2611.taw": build_taw(db_type=G600)})
    files = [_taw_file("jg600a-us-2611.taw")]

    mismatches = _taw_header_mismatches(api, files, GTN)

    assert [(f.file_name, t) for f, t in mismatches] == [("jg600a-us-2611.taw", G600)]


def test_files_matching_the_card_and_non_taw_files_are_fine():
    api = _multi_api({"https://avdb.garmin.com/tgtn-amrn-20T1.taw": build_taw(db_type=GTN)})
    files = [_taw_file("tgtn-amrn-20T1.taw"), _taw_file("SECT_N36W078.jnx")]
    assert _taw_header_mismatches(api, files, GTN) == []
    assert _taw_header_mismatches(api, files, None) == []   # card type unknown: no opinion
