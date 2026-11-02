"""Tests for the FlySto auth-relay (advanced) mode."""
from unittest.mock import patch, MagicMock

from avcardtool.flight_data.uploaders import FlyStoUploader


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = ""
    return r


def _uploader(tmp_path, **extra):
    return FlyStoUploader({'enabled': True, 'data_dir': str(tmp_path), **extra})


def test_relay_disabled_by_default(tmp_path):
    assert _uploader(tmp_path).auth_relay_url == ''


def test_relay_url_normalised_and_no_client_secret_needed(tmp_path):
    u = _uploader(tmp_path, auth_relay_url=' https://auth.example.com/ ', refresh_token='r')
    assert u.auth_relay_url == 'https://auth.example.com'
    with patch('avcardtool.flight_data.uploaders.flysto.requests.post',
               return_value=_resp(200, {'access_token': 'a', 'expires_in': 3600})) as post:
        assert u.authenticate() is True
    assert post.call_args[0][0] == 'https://auth.example.com/api/token/refresh'
    assert post.call_args[1]['json'] == {'refresh_token': 'r'}
    assert u.access_token == 'a'


def test_login_via_relay_saves_tokens(tmp_path):
    u = _uploader(tmp_path, auth_relay_url='https://auth.example.com')
    new = _resp(201, {'session_id': 's1', 'auth_url': 'https://auth.example.com/auth?session_id=s1'})
    polls = [_resp(200, {'status': 'pending'}),
             _resp(200, {'status': 'complete',
                         'tokens': {'access_token': 'a', 'refresh_token': 'r', 'expires_at': 9999999999}})]
    with patch('avcardtool.flight_data.uploaders.flysto.requests.post', return_value=new), \
         patch('avcardtool.flight_data.uploaders.flysto.requests.get', side_effect=polls), \
         patch('avcardtool.flight_data.uploaders.flysto.time.sleep'):
        ok, _ = u.login_via_relay(echo=lambda *_: None)
    assert ok and u.refresh_token == 'r'
    assert (tmp_path / 'flysto_tokens.json').exists()


def test_login_requires_relay_url(tmp_path):
    ok, msg = _uploader(tmp_path).login_via_relay()
    assert not ok and 'auth_relay_url' in msg
