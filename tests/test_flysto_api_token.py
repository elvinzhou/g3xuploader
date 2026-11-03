"""Tests for FlySto static API-token authentication."""
from pathlib import Path
from unittest.mock import patch, MagicMock

from avcardtool.flight_data.base import FlightData
from avcardtool.flight_data.uploaders import FlyStoUploader


def _resp(status=200, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def _uploader(tmp_path, **extra):
    return FlyStoUploader({'enabled': True, 'data_dir': str(tmp_path), **extra})


def _flight(tmp_path):
    f = tmp_path / "log.csv"
    f.write_text("a,b\n1,2\n")
    fd = MagicMock(spec=FlightData)
    fd.file_path = Path(f)
    fd.data_points = []
    return fd


def test_token_is_stripped_and_authenticates_without_oauth(tmp_path):
    u = _uploader(tmp_path, api_token='  tok  ')
    assert u.api_token == 'tok'
    assert u.authenticate() is True


def test_missing_token_fails(tmp_path):
    assert _uploader(tmp_path).authenticate() is False


def test_upload_sends_bearer_token(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch('avcardtool.flight_data.uploaders.flysto.requests.post',
               return_value=_resp(200)) as post:
        result = u.upload_flight(_flight(tmp_path))
    assert result.success
    assert post.call_args[1]['headers']['Authorization'] == 'Bearer tok'
    assert post.call_args[0][0] == FlyStoUploader.UPLOAD_URL


def test_rejected_token_reports_clearly_and_does_not_retry(tmp_path):
    u = _uploader(tmp_path, api_token='bad')
    with patch('avcardtool.flight_data.uploaders.flysto.requests.post',
               return_value=_resp(401)) as post:
        result = u.upload_flight(_flight(tmp_path))
    assert not result.success and 'api_token' in result.message
    assert post.call_count == 1


# ---- skip flights not newer than FlySto's latest log ---------------------
from datetime import datetime, timedelta

GET = 'avcardtool.flight_data.uploaders.flysto.requests.get'
POST = 'avcardtool.flight_data.uploaders.flysto.requests.post'


def _flight_on(tmp_path, start):
    fd = _flight(tmp_path)
    fd.metadata = MagicMock(aircraft_ident="N345FS")
    fd.data_points = [MagicMock(timestamp=start), MagicMock(timestamp=start + timedelta(hours=1))]
    return fd


def _logs(*logs):
    r = _resp(200)
    r.json.return_value = {"logs": list(logs)}
    return r


def _log(takeoff="2026-10-12T14:22:15.000Z", **extra):
    return {"id": "abc", "url": "https://www.flysto.net/abc",
            "takeoffs": [{"time": takeoff}], **extra}


def test_same_or_earlier_day_is_skipped(tmp_path):
    for start in (datetime(2026, 10, 12, 9, 0), datetime(2026, 10, 1, 9, 0)):
        u = _uploader(tmp_path, api_token='tok')
        with patch(GET, return_value=_logs(_log())), patch(POST) as post:
            result = u.upload_flight(_flight_on(tmp_path, start))
        assert result.success and result.metadata['duplicate']
        post.assert_not_called()


def test_later_day_is_uploaded(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch(GET, return_value=_logs(_log())), patch(POST, return_value=_resp(200)) as post:
        result = u.upload_flight(_flight_on(tmp_path, datetime(2026, 10, 13, 8, 0)))
    assert result.success and not result.metadata.get('duplicate')
    post.assert_called_once()


def test_latest_is_max_date_regardless_of_order(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    resp = _logs(_log("2026-09-01T10:00:00.000Z"), _log("2026-10-12T10:00:00.000Z"), _log("2026-08-01T10:00:00.000Z"))
    with patch(GET, return_value=resp):
        day, _ = u.latest_uploaded()
    assert str(day) == "2026-10-12"


def test_falls_back_to_display_date(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch(GET, return_value=_logs({"id": "x", "date": "12 Oct 2026"})):
        day, _ = u.latest_uploaded()
    assert str(day) == "2026-10-12"


def test_empty_account_and_query_failure_upload(tmp_path):
    for resp in (_logs(), _resp(500)):
        u = _uploader(tmp_path, api_token='tok')
        with patch(GET, return_value=resp), patch(POST, return_value=_resp(200)) as post:
            assert u.upload_flight(_flight_on(tmp_path, datetime(2026, 10, 12, 9, 0))).success
        post.assert_called_once()


def test_query_made_once_and_can_be_disabled(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch(GET, return_value=_logs(_log())) as get:
        u.latest_uploaded(); u.latest_uploaded()
    assert get.call_count == 1

    u2 = _uploader(tmp_path, api_token='tok', check_duplicates=False)
    with patch(GET) as get, patch(POST, return_value=_resp(200)):
        u2.upload_flight(_flight_on(tmp_path, datetime(2026, 10, 1, 9, 0)))
    get.assert_not_called()
