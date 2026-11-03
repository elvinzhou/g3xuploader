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
