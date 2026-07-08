"""Tests for the notifications system: email backend, manager, event builders, config."""

import json
import time
from unittest import mock

import pytest

from avcardtool.core.config import Config, NotificationsConfig
from avcardtool.notifications import BACKENDS, NotificationManager
from avcardtool.notifications import events as ev
from avcardtool.notifications.backends.email_smtp import EmailBackend
from avcardtool.notifications.base import (
    NotificationBackend,
    NotificationEvent,
    NotificationResult,
    Severity,
)


def email_config(**overrides):
    cfg = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "use_tls": True,
        "username": "user@example.com",
        "password": "secret",
        "from_addr": "user@example.com",
        "to_addrs": ["dest@example.com"],
    }
    cfg.update(overrides)
    return cfg


def make_event(severity=Severity.INFO, event_type="test"):
    return NotificationEvent(
        event_type=event_type,
        severity=severity,
        title="Unit test",
        body="Test body",
    )


# ============================================================================
# EmailBackend
# ============================================================================

class TestEmailBackend:

    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP")
    def test_starttls_send(self, smtp_cls):
        smtp = smtp_cls.return_value
        backend = EmailBackend(email_config())

        result = backend.send(make_event())

        assert result.success
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("user@example.com", "secret")
        smtp.send_message.assert_called_once()
        smtp.quit.assert_called_once()

        msg = smtp.send_message.call_args[0][0]
        assert msg["Subject"] == "[AVCardTool] Unit test"
        assert msg["From"] == "user@example.com"
        assert msg["To"] == "dest@example.com"
        assert "Test body" in msg.get_content()

    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP_SSL")
    def test_implicit_tls_on_port_465(self, smtp_ssl_cls):
        backend = EmailBackend(email_config(smtp_port=465))

        result = backend.send(make_event())

        assert result.success
        assert smtp_ssl_cls.called
        smtp_ssl_cls.return_value.starttls.assert_not_called()

    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP")
    def test_plaintext_allowed_for_localhost(self, smtp_cls):
        backend = EmailBackend(email_config(
            smtp_host="localhost", smtp_port=25, use_tls=False, username=""
        ))

        result = backend.send(make_event())

        assert result.success
        smtp_cls.return_value.starttls.assert_not_called()
        smtp_cls.return_value.login.assert_not_called()

    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP")
    def test_plaintext_refused_for_remote_host(self, smtp_cls):
        backend = EmailBackend(email_config(use_tls=False))

        result = backend.send(make_event())

        assert not result.success
        assert "localhost" in result.message
        smtp_cls.assert_not_called()

    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP")
    def test_smtp_failure_returns_result_not_exception(self, smtp_cls):
        smtp_cls.side_effect = ConnectionRefusedError("connection refused")
        backend = EmailBackend(email_config())

        result = backend.send(make_event())

        assert not result.success
        assert "connection refused" in result.message

    @pytest.mark.parametrize("severity,prefix", [
        (Severity.INFO, "[AVCardTool]"),
        (Severity.WARNING, "[AVCardTool WARNING]"),
        (Severity.ERROR, "[AVCardTool ERROR]"),
    ])
    @mock.patch("avcardtool.notifications.backends.email_smtp.smtplib.SMTP")
    def test_subject_prefix_by_severity(self, smtp_cls, severity, prefix):
        backend = EmailBackend(email_config())

        backend.send(make_event(severity=severity))

        msg = smtp_cls.return_value.send_message.call_args[0][0]
        assert msg["Subject"].startswith(prefix + " ")

    def test_validate_config_reports_missing_fields(self):
        backend = EmailBackend({"enabled": True})
        errors = backend.validate_config()
        joined = "; ".join(errors)
        assert "smtp_host" in joined
        assert "from_addr" in joined
        assert "to_addrs" in joined

    def test_single_recipient_string_accepted(self):
        backend = EmailBackend(email_config(to_addrs="dest@example.com"))
        assert backend.to_addrs == ["dest@example.com"]


# ============================================================================
# NotificationManager
# ============================================================================

class FakeBackend(NotificationBackend):
    """Succeeds after config['fail_times'] failures; records sent events."""
    name = "fake"

    def __init__(self, config):
        super().__init__(config)
        self.sent = []
        self.calls = 0
        self.fail_times = config.get("fail_times", 0)
        self.raise_exc = config.get("raise_exc", False)

    def send(self, event):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("backend blew up")
        if self.calls <= self.fail_times:
            return NotificationResult(self.name, False, "transient failure")
        self.sent.append(event)
        return NotificationResult(self.name, True, "ok")


@pytest.fixture
def fake_backend_registry(monkeypatch):
    monkeypatch.setitem(BACKENDS, "fake", FakeBackend)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("avcardtool.notifications.manager.time.sleep", lambda s: None)


def make_manager(tmp_path, backend_cfg=None, enabled=True, events=None):
    cfg = NotificationsConfig(
        enabled=enabled,
        backends={"fake": backend_cfg or {"enabled": True}},
    )
    if events is not None:
        cfg.events = events
    return NotificationManager(cfg, tmp_path)


class TestNotificationManager:

    def test_delivers_to_backend(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path)
        results = manager.notify(make_event())
        assert [r.success for r in results] == [True]
        assert len(manager.backends[0].sent) == 1

    def test_disabled_sends_nothing(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path, enabled=False)
        assert manager.notify(make_event()) == []
        assert not manager.active

    def test_disabled_backend_not_built(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path, backend_cfg={"enabled": False})
        assert manager.backends == []
        assert not manager.active

    def test_event_filter(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path, events={"flights_processed": False})
        assert manager.notify(make_event(event_type="flights_processed")) == []
        # Unknown event types default to enabled
        assert manager.notify(make_event(event_type="brand_new_event")) != []

    def test_force_bypasses_disabled_and_filter(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path, enabled=False,
                               events={"flights_processed": False})
        results = manager.notify(make_event(event_type="flights_processed"), force=True)
        assert [r.success for r in results] == [True]

    def test_retries_until_success(self, tmp_path, fake_backend_registry, no_sleep):
        manager = make_manager(tmp_path, backend_cfg={"enabled": True, "fail_times": 2})
        results = manager.notify(make_event())
        assert results[0].success
        assert manager.backends[0].calls == 3

    def test_gives_up_after_retries(self, tmp_path, fake_backend_registry, no_sleep):
        manager = make_manager(tmp_path, backend_cfg={"enabled": True, "fail_times": 99})
        results = manager.notify(make_event())
        assert not results[0].success
        assert manager.backends[0].calls == 3

    def test_never_raises_on_backend_exception(self, tmp_path, fake_backend_registry, no_sleep):
        manager = make_manager(tmp_path, backend_cfg={"enabled": True, "raise_exc": True})
        results = manager.notify(make_event())
        assert not results[0].success
        assert "backend blew up" in results[0].message

    def test_unknown_backend_skipped(self, tmp_path):
        cfg = NotificationsConfig(enabled=True, backends={"bogus": {"enabled": True}})
        manager = NotificationManager(cfg, tmp_path)
        assert manager.backends == []

    def test_rate_limit_auth_expired(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path)
        event = make_event(event_type="garmin_auth_expired")

        first = manager.notify(event)
        assert first[0].success
        assert manager.state_path.exists()

        # Second notification inside the 24h window is suppressed
        assert manager.notify(make_event(event_type="garmin_auth_expired")) == []

        # ...but goes through once the window has passed
        state = json.loads(manager.state_path.read_text())
        state["last_sent"]["garmin_auth_expired"] = time.time() - 90000
        manager.state_path.write_text(json.dumps(state))
        third = manager.notify(make_event(event_type="garmin_auth_expired"))
        assert third[0].success

    def test_failed_send_not_recorded_for_rate_limit(self, tmp_path, fake_backend_registry, no_sleep):
        manager = make_manager(tmp_path, backend_cfg={"enabled": True, "fail_times": 99})
        manager.notify(make_event(event_type="garmin_auth_expired"))
        assert not manager.state_path.exists()
        # The next run must attempt again rather than being rate limited
        assert not manager._rate_limited("garmin_auth_expired")

    def test_timestamp_filled_in(self, tmp_path, fake_backend_registry):
        manager = make_manager(tmp_path)
        event = make_event()
        assert event.timestamp == ""
        manager.notify(event)
        assert event.timestamp != ""


# ============================================================================
# Event builders
# ============================================================================

CANNED_SUMMARY = {
    "aircraft_ident": "N12345",
    "date": "2026-07-08",
    "hobbs": {"starting_hours": 1233.1, "increment_hours": 1.4, "ending_hours": 1234.5},
    "tach": {"starting_hours": 1100.0, "increment_hours": 1.21, "ending_hours": 1101.21},
    "oooi": {
        "out_time": "2026-07-08T09:05:00",
        "off_time": "2026-07-08T09:15:00",
        "on_time": "2026-07-08T10:39:00",
        "in_time": "2026-07-08T10:45:00",
        "block_time_minutes": 100.0,
        "flight_time_minutes": 84.0,
    },
}


class TestEventBuilders:

    def test_flight_entry_from_summary(self):
        entry = ev.flight_entry_from_summary(
            "log_20260708_091502.csv", CANNED_SUMMARY,
            {"cloudahoy": {"success": True, "message": "", "url": None}},
        )
        assert entry["aircraft"] == "N12345"
        assert entry["hobbs_ending"] == 1234.5
        assert entry["tach_increment"] == 1.21
        assert entry["off_time"] == "2026-07-08T09:15:00"
        assert entry["uploads"]["cloudahoy"]["success"]

    def test_flights_processed_body(self):
        stats = {"total": 17, "already_processed": 14, "flights": 2,
                 "non_flights": 1, "upload_success": 4, "upload_failed": 0}
        flights = [
            ev.flight_entry_from_summary("log_a.csv", CANNED_SUMMARY,
                                         {"cloudahoy": {"success": True},
                                          "flysto": {"success": True}}),
            ev.flight_entry_from_summary("log_b.csv", CANNED_SUMMARY,
                                         {"cloudahoy": {"success": True},
                                          "flysto": {"success": True}}),
        ]
        event = ev.build_flights_processed_event(stats, flights)

        assert event.event_type == "flights_processed"
        assert event.severity == Severity.INFO
        assert event.title == "N12345: 2 flights processed"
        assert "Hobbs +1.40 → 1234.50" in event.body
        assert "Tach +1.21 → 1101.21" in event.body
        assert "off 09:15" in event.body
        assert "flight 1.4h" in event.body
        assert "cloudahoy ✓" in event.body
        assert "14 already processed" in event.body
        assert "1 non-flight" in event.body
        assert "4 succeeded, 0 failed" in event.body

    def test_flights_processed_upload_failure_is_warning(self):
        stats = {"total": 1, "already_processed": 0, "flights": 1,
                 "non_flights": 0, "upload_success": 0, "upload_failed": 1}
        flights = [ev.flight_entry_from_summary(
            "log_a.csv", CANNED_SUMMARY,
            {"flysto": {"success": False, "message": "401 unauthorized"}},
        )]
        event = ev.build_flights_processed_event(stats, flights)
        assert event.severity == Severity.WARNING
        assert "flysto ✗ (401 unauthorized)" in event.body

    def test_flights_processed_with_errors_and_notes(self):
        stats = {"total": 2, "already_processed": 0, "flights": 1,
                 "non_flights": 0, "upload_success": 1, "upload_failed": 0}
        flights = [ev.flight_entry_from_summary("log_a.csv", CANNED_SUMMARY)]
        event = ev.build_flights_processed_event(
            stats, flights,
            notes=["Carryd: ✓ updated to 1234.50h"],
            errors=["log_c.csv: invalid header"],
        )
        assert event.severity == Severity.WARNING
        assert "Carryd: ✓ updated to 1234.50h" in event.body
        assert "log_c.csv: invalid header" in event.body

    def test_flights_processed_uploads_skipped(self):
        stats = {"total": 1, "already_processed": 0, "flights": 1,
                 "non_flights": 0, "upload_success": 0, "upload_failed": 0}
        flights = [ev.flight_entry_from_summary("log_a.csv", CANNED_SUMMARY)]
        event = ev.build_flights_processed_event(stats, flights, uploads_skipped=True)
        assert "Uploads: skipped" in event.body

    def test_navdata_updated_event(self):
        event = ev.build_navdata_updated_event(
            card_serial="3F2A-11C0",
            avionics="G3X Touch",
            aircraft="N12345",
            installed=[
                {"database": "Navigation Data", "old_issue": "2606",
                 "new_issue": "2607", "effective_at": "2026-07-16T00:00:00Z",
                 "upcoming": False},
                {"database": "Charts", "old_issue": "2607",
                 "new_issue": "2608", "effective_at": "2026-08-13T00:00:00Z",
                 "upcoming": True},
            ],
            already_current=[{"database": "Terrain", "issue": "26D2"}],
            file_count=38,
        )
        assert event.event_type == "navdata_updated"
        assert event.severity == Severity.INFO
        assert event.title == "2 databases installed — N12345"
        assert "Card 3F2A-11C0 — G3X Touch on N12345" in event.body
        assert "Navigation Data: 2606 → 2607" in event.body
        assert "(effective 2026-07-16)" in event.body
        assert "Pre-loaded upcoming cycle:" in event.body
        assert "Charts: 2608" in event.body
        assert "Terrain (26D2)" in event.body
        assert "38 file(s) written" in event.body

    def test_navdata_updated_single_database_title(self):
        event = ev.build_navdata_updated_event(
            card_serial="ABCD-1234", avionics="G3X Touch", aircraft=None,
            installed=[{"database": "NavData", "old_issue": None,
                        "new_issue": "2607", "effective_at": None,
                        "upcoming": False}],
        )
        assert event.title == "NavData 2607 installed"
        assert "none → 2607" in event.body

    def test_navdata_update_failed_event(self):
        event = ev.build_navdata_update_failed_event([
            ("ERROR", "Download failed for dg3xt-us-26D2.taw: timeout"),
            ("WARNING", "Batch session failed — continuing without batch auth"),
        ])
        assert event.severity == Severity.ERROR
        assert "1 error" in event.title
        assert "ERROR: Download failed" in event.body
        assert "WARNING: Batch session failed" in event.body

    def test_garmin_auth_expired_event(self):
        event = ev.build_garmin_auth_expired_event()
        assert event.severity == Severity.ERROR
        assert "avcardtool navdata login" in event.body

    def test_processing_error_event(self):
        event = ev.build_processing_error_event("Could not mount /dev/sda1")
        assert event.severity == Severity.ERROR
        assert "Could not mount /dev/sda1" in event.body


# ============================================================================
# Config integration
# ============================================================================

class TestNotificationsConfig:

    def test_defaults(self):
        cfg = NotificationsConfig()
        assert cfg.enabled is False
        assert cfg.events["flights_processed"] is True
        assert cfg.events["garmin_auth_expired"] is True
        assert cfg.backends == {}

    def test_load_and_roundtrip(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "notifications": {
                "enabled": True,
                "events": {"flights_processed": False},
                "backends": {
                    "email": {"enabled": True, "smtp_host": "smtp.example.com",
                              "from_addr": "a@b.c", "to_addrs": ["a@b.c"]}
                },
            }
        }))
        cfg = Config(config_path=config_path)
        assert cfg.notifications.enabled is True
        assert cfg.notifications.events["flights_processed"] is False
        # Unspecified events keep their defaults
        assert cfg.notifications.events["navdata_updated"] is True
        assert cfg.notifications.backends["email"]["smtp_host"] == "smtp.example.com"

        data = cfg.to_dict()
        assert data["notifications"]["enabled"] is True
        assert data["notifications"]["backends"]["email"]["from_addr"] == "a@b.c"

    def test_absent_section_uses_defaults(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"system": {"log_level": "INFO"}}))
        cfg = Config(config_path=config_path)
        assert cfg.notifications.enabled is False

    def test_validate_rejects_bad_email_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "notifications": {
                "enabled": True,
                "backends": {"email": {"enabled": True}},
            }
        }))
        cfg = Config(config_path=config_path)
        with pytest.raises(ValueError, match="smtp_host"):
            cfg.validate()

    def test_validate_ignores_disabled_backends(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "notifications": {
                "enabled": True,
                "backends": {"email": {"enabled": False}},
            }
        }))
        cfg = Config(config_path=config_path)
        assert cfg.validate() is True
