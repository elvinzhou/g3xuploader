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


# ---- duplicate detection -------------------------------------------------
from datetime import datetime, timedelta


def _flight_with_points(tmp_path, ident="N345FS", start=datetime(2026, 10, 12, 14, 10)):
    fd = _flight(tmp_path)
    fd.metadata = MagicMock(aircraft_ident=ident)
    fd.data_points = [MagicMock(timestamp=start), MagicMock(timestamp=start + timedelta(hours=1))]
    return fd


def _logs_resp(logs, nxt=None):
    r = _resp(200)
    r.json.return_value = {"logs": logs, "next": nxt}
    return r


def _log(ident="N345FS", takeoff="2026-10-12T14:22:15.000Z"):
    return {"id": "abc", "url": "https://www.flysto.net/abc", "aircraft": ident,
            "takeoffs": [{"time": takeoff}], "approaches": []}


def test_duplicate_is_skipped_without_uploading(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get',
               return_value=_logs_resp([_log()])), \
         patch('avcardtool.flight_data.uploaders.flysto.requests.post') as post:
        result = u.upload_flight(_flight_with_points(tmp_path))
    assert result.success and result.metadata['duplicate']
    post.assert_not_called()


def test_other_aircraft_same_time_is_not_a_duplicate(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get',
               return_value=_logs_resp([_log(ident="N999ZZ")])), \
         patch('avcardtool.flight_data.uploaders.flysto.requests.post', return_value=_resp(200)) as post:
        result = u.upload_flight(_flight_with_points(tmp_path))
    assert result.success and not result.metadata.get('duplicate')
    post.assert_called_once()


def test_no_match_uploads_and_query_failure_fails_open(tmp_path):
    u = _uploader(tmp_path, api_token='tok')
    other = _log(takeoff="2026-10-12T20:00:00.000Z")
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get',
               return_value=_logs_resp([other])), \
         patch('avcardtool.flight_data.uploaders.flysto.requests.post', return_value=_resp(200)) as post:
        assert u.upload_flight(_flight_with_points(tmp_path)).success
    assert post.call_count == 1

    u2 = _uploader(tmp_path, api_token='tok')
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get', return_value=_resp(500)), \
         patch('avcardtool.flight_data.uploaders.flysto.requests.post', return_value=_resp(200)) as post:
        assert u2.upload_flight(_flight_with_points(tmp_path)).success
    assert post.call_count == 1


def test_check_can_be_disabled_and_pagination_followed(tmp_path):
    u = _uploader(tmp_path, api_token='tok', check_duplicates=False)
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get') as get, \
         patch('avcardtool.flight_data.uploaders.flysto.requests.post', return_value=_resp(200)):
        u.upload_flight(_flight_with_points(tmp_path))
    get.assert_not_called()

    u2 = _uploader(tmp_path, api_token='tok')
    pages = [_logs_resp([], nxt="https://www.flysto.net/public-api/logs?cursor=x"), _logs_resp([_log()])]
    with patch('avcardtool.flight_data.uploaders.flysto.requests.get', side_effect=pages) as get:
        assert u2.find_duplicate(_flight_with_points(tmp_path))['id'] == 'abc'
    assert get.call_count == 2
