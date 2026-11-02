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
