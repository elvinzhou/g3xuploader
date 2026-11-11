"""
Tests for --dry-run: read everything, write nothing.

Every test patches requests.post to raise, so any network call made in a
dry run fails the test loudly.
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

from avcardtool.cli import cli
from avcardtool.core import Config, ProcessedFilesDatabase
from avcardtool.core.config import NotificationsConfig
from avcardtool.flight_data import GarminG3XProcessor
from avcardtool.flight_data.uploaders import (
    CarrydUploader,
    CloudAhoyUploader,
    FlyStoUploader,
    SavvyAviationUploader,
)
from avcardtool.notifications import NotificationManager
from avcardtool.notifications.base import NotificationEvent, NotificationResult, Severity

SAMPLE_FLIGHT = Path(__file__).parent / "test_data" / "sample_flight.csv"

NO_NETWORK = mock.patch(
    "requests.post", side_effect=AssertionError("network call made during dry run")
)


@pytest.fixture
def flight_data():
    return GarminG3XProcessor().parse_log(SAMPLE_FLIGHT)


# ---------------------------------------------------------------------------
# Config overrides
# ---------------------------------------------------------------------------

class TestConfigOverrides:
    def test_cli_override_is_not_persisted(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"system": {"data_dir": "/real/data"}}))
        cfg = Config(config_path=cfg_path)

        cfg.override_system(dry_run=True, data_dir="/scratch")

        assert cfg.system.dry_run is True
        assert cfg.system.data_dir == "/scratch"
        saved = cfg.to_dict()["system"]
        assert saved["dry_run"] is False
        assert saved["data_dir"] == "/real/data"

    def test_dry_run_from_config_file_is_kept(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"system": {"dry_run": True}}))
        cfg = Config(config_path=cfg_path)

        assert cfg.system.dry_run is True
        assert cfg.to_dict()["system"]["dry_run"] is True


# ---------------------------------------------------------------------------
# Processed files database
# ---------------------------------------------------------------------------

class TestReadOnlyProcessedDb:
    def test_new_db_is_not_created(self, tmp_path):
        db_path = tmp_path / "sub" / "processed_files.json"
        db = ProcessedFilesDatabase(db_path, read_only=True)
        db.mark_processed("abc", SAMPLE_FLIGHT, "N1", True, flight_fingerprint="fp1")

        assert not db_path.exists()
        assert not db_path.parent.exists()
        # Changes are still visible within the run so dedup behaves normally
        assert db.is_processed("abc")
        assert db.is_duplicate_flight("fp1")

    def test_existing_db_is_read_but_not_modified(self, tmp_path):
        db_path = tmp_path / "processed_files.json"
        ProcessedFilesDatabase(db_path).mark_processed("old", SAMPLE_FLIGHT, "N1", True)
        before = db_path.read_text()

        db = ProcessedFilesDatabase(db_path, read_only=True)
        assert db.is_processed("old")
        db.mark_processed("new", SAMPLE_FLIGHT, "N1", True)

        assert db.is_processed("new")
        assert db_path.read_text() == before


# ---------------------------------------------------------------------------
# Uploaders
# ---------------------------------------------------------------------------

class TestUploaderDryRun:
    def _base(self, tmp_path):
        return {"enabled": True, "dry_run": True, "data_dir": str(tmp_path)}

    def test_cloudahoy(self, tmp_path, flight_data):
        up = CloudAhoyUploader({**self._base(tmp_path), "api_token": "t"})
        with NO_NETWORK:
            result = up.upload_flight(flight_data)
        assert result.success and result.metadata["dry_run"]
        assert (tmp_path / "debug" / f"cloudahoy_{SAMPLE_FLIGHT.stem}.json").exists()

    def test_cloudahoy_still_reports_missing_token(self, tmp_path, flight_data):
        up = CloudAhoyUploader(self._base(tmp_path))
        with NO_NETWORK:
            result = up.upload_flight(flight_data)
        assert not result.success
        assert "token" in result.message

    def test_flysto_skips_token_refresh(self, tmp_path, flight_data):
        up = FlyStoUploader({
            **self._base(tmp_path),
            "client_id": "id", "client_secret": "secret", "refresh_token": "r",
        })
        with NO_NETWORK, mock.patch.object(
            up, "_ensure_valid_token", side_effect=AssertionError("token refresh")
        ):
            result = up.upload_flight(flight_data)
        assert result.success
        assert (tmp_path / "debug" / f"flysto_{SAMPLE_FLIGHT.stem}.zip").exists()

    def test_carryd(self, tmp_path, flight_data):
        up = CarrydUploader({**self._base(tmp_path), "api_key": "eal_x"})
        summary = {"aircraft_ident": "N1", "hobbs": {"ending_hours": 12.3}}
        with NO_NETWORK:
            result = up.upload_flight(flight_data, summary)
        assert result.success
        assert "12.3" in result.message
        assert (tmp_path / "debug" / f"carryd_{SAMPLE_FLIGHT.stem}.json").exists()

    def test_savvy_does_not_stage(self, tmp_path, flight_data):
        staging = tmp_path / "staging"
        up = SavvyAviationUploader({**self._base(tmp_path), "staging_dir": str(staging)})
        result = up.upload_flight(flight_data)
        assert result.success
        assert "would stage" in result.message
        assert not staging.exists()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class _RecordingBackend:
    name = "recording"

    def __init__(self):
        self.sent = []

    def send(self, event):
        self.sent.append(event)
        return NotificationResult(backend=self.name, success=True)


def _manager(tmp_path, **kwargs):
    nc = NotificationsConfig(enabled=True)
    mgr = NotificationManager(nc, tmp_path, **kwargs)
    backend = _RecordingBackend()
    mgr.backends = [backend]
    return mgr, backend


def _event(event_type="navdata_updated"):
    return NotificationEvent(
        event_type=event_type, severity=Severity.INFO,
        title="Navigation Data 2610 installed", body="Card 1234 — GDU 460",
    )


class TestNotificationDryRun:
    def test_prints_instead_of_sending(self, tmp_path, capsys):
        mgr, backend = _manager(tmp_path, dry_run=True)
        mgr.notify(_event())

        assert backend.sent == []
        out = capsys.readouterr().out
        assert "[DRY RUN] Notification: navdata_updated" in out
        assert "Would be sent via: recording" in out
        assert "Navigation Data 2610 installed" in out

    def test_reports_why_it_would_not_send(self, tmp_path, capsys):
        mgr, _ = _manager(tmp_path, dry_run=True)
        mgr.config.enabled = False
        mgr.notify(_event())
        assert "Would NOT be sent: notifications are disabled" in capsys.readouterr().out

    def test_send_flag_tags_subject_and_skips_rate_limit_state(self, tmp_path):
        mgr, backend = _manager(tmp_path, dry_run=True, dry_run_send=True)
        mgr.notify(_event("garmin_auth_expired"))

        assert [e.title for e in backend.sent] == ["[DRY RUN] Navigation Data 2610 installed"]
        assert not mgr.state_path.exists()


# ---------------------------------------------------------------------------
# auto-process end to end
# ---------------------------------------------------------------------------

def _write_config(tmp_path, data_dir, **system):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "flight_data": {"uploaders": {
            "cloudahoy": {"enabled": True, "api_token": "t"},
            "carryd": {"enabled": True, "api_key": "eal_x"},
        }},
        "system": {
            "data_dir": str(data_dir),
            "log_file": str(tmp_path / "avcardtool.log"),
            "mark_historical_on_first_run": True,
            **system,
        },
    }))
    return cfg_path


@pytest.fixture
def card(tmp_path):
    """A directory laid out like a G3X card's data_log folder."""
    data_log = tmp_path / "card" / "data_log"
    data_log.mkdir(parents=True)
    shutil.copy(SAMPLE_FLIGHT, data_log / "log_20260327_152821_KOAK.csv")
    return tmp_path / "card"


class TestAutoProcessDryRun:
    def test_force_processes_and_writes_nothing(self, tmp_path, card):
        data_dir = tmp_path / "data"
        cfg_path = _write_config(tmp_path, data_dir)
        card_before = {p: p.read_bytes() for p in card.rglob("*") if p.is_file()}

        with NO_NETWORK:
            result = CliRunner().invoke(
                cli, ["--config", str(cfg_path), "--dry-run", "auto-process", str(card), "--force"]
            )

        assert result.exit_code == 0, result.output
        assert "Processing Summary (DRY RUN)" in result.output
        assert "✓ cloudahoy" in result.output
        assert "Carryd: ✓ Dry run" in result.output
        assert "carryd_state.json not updated" in result.output
        assert not (data_dir / "processed_files.json").exists()
        assert not (data_dir / "carryd_state.json").exists()
        assert (data_dir / "debug" / "cloudahoy_log_20260327_152821_KOAK.json").exists()
        # Card untouched
        assert {p: p.read_bytes() for p in card.rglob("*") if p.is_file()} == card_before

    def test_first_run_is_simulated_without_saving(self, tmp_path, card):
        data_dir = tmp_path / "data"
        cfg_path = _write_config(tmp_path, data_dir)

        result = CliRunner().invoke(
            cli, ["--config", str(cfg_path), "--dry-run", "auto-process", str(card)]
        )

        assert result.exit_code == 0, result.output
        assert "marking 1 existing file(s) as historical" in result.output
        assert "dry run: nothing was saved" in result.output
        assert not (data_dir / "processed_files.json").exists()

    def test_dry_run_from_config_file(self, tmp_path, card):
        data_dir = tmp_path / "data"
        cfg_path = _write_config(tmp_path, data_dir, dry_run=True)

        with NO_NETWORK:
            result = CliRunner().invoke(
                cli, ["--config", str(cfg_path), "auto-process", str(card), "--force"]
            )

        assert result.exit_code == 0, result.output
        assert not (data_dir / "processed_files.json").exists()

    def test_data_dir_override(self, tmp_path, card):
        cfg_path = _write_config(tmp_path, tmp_path / "prod")
        scratch = tmp_path / "scratch"

        with NO_NETWORK:
            result = CliRunner().invoke(cli, [
                "--config", str(cfg_path), "--dry-run", "--data-dir", str(scratch),
                "auto-process", str(card), "--force",
            ])

        assert result.exit_code == 0, result.output
        assert (scratch / "debug").is_dir()
        assert not (tmp_path / "prod").exists()
        # The override never lands in the config file
        assert json.loads(cfg_path.read_text())["system"]["data_dir"] == str(tmp_path / "prod")


def test_navdata_install_refuses_in_dry_run(tmp_path):
    cfg_path = _write_config(tmp_path, tmp_path / "data")
    result = CliRunner().invoke(
        cli, ["--config", str(cfg_path), "--dry-run", "navdata", "install", str(tmp_path), "-y"]
    )
    assert result.exit_code == 1
    assert "refusing" in result.output


# ---------------------------------------------------------------------------
# navdata auto-update planning
# ---------------------------------------------------------------------------

def test_navdata_dry_run_report_skips_crc_matches(tmp_path, capsys):
    from avcardtool import cli as cli_mod

    nav = SimpleNamespace(name="NavData")
    obst = SimpleNamespace(name="Obstacle")
    series = SimpleNamespace(series_id=2239)
    nav_issue = SimpleNamespace(name="2610", effective_at="2026-10-02T00:00:00Z")
    obst_issue = SimpleNamespace(name="2610", effective_at="2026-10-02T00:00:00Z")
    plan = [
        (nav, series, nav_issue, "2609", False),
        (obst, series, obst_issue, "2609", False),
    ]

    # Obstacle 2610 is already downloaded and its CRC matches the card
    obst_feat = cli_mod._AVDB_TO_FEAT_UNLK["Obstacle"]
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / cli_mod._DL_STATE_FILE).write_text(json.dumps({
        "2239/2610": {"status": "complete", "feature_crcs": {obst_feat: 0xABCD}},
    }))

    mgr, backend = _manager(tmp_path, dry_run=True)
    log = mock.Mock()
    with mock.patch.object(cli_mod, "_read_feat_unlk_crcs", return_value={obst_feat: 0xABCD}):
        cli_mod._report_navdata_dry_run(
            log, mgr, plan, [], tmp_path, cache_dir, "1234", "GDU 460", "N662EZ",
        )

    assert backend.sent == []
    out = capsys.readouterr().out
    assert "NavData: 2609 → 2610" in out
    assert "Obstacle" not in out.split("Subject:")[1]
    logged = " ".join(str(c.args[0]) for c in log.info.call_args_list)
    assert "Obstacle/2610: feat_unlk CRC matches cache — would skip" in logged


def test_config_built_without_init_can_be_saved(tmp_path):
    """The setup wizard builds Config via Config.__new__ (skipping __init__)
    and then saves it; that must not depend on __init__-only attributes."""
    from avcardtool.core.config import (
        FlightDataConfig, NavdataConfig, NotificationsConfig, SystemConfig,
    )
    cfg = Config.__new__(Config)
    cfg.config_path = tmp_path / "config.json"
    cfg.flight_data = FlightDataConfig()
    cfg.navdata = NavdataConfig()
    cfg.notifications = NotificationsConfig()
    cfg.system = SystemConfig()
    cfg.system.data_dir = str(tmp_path / "data")

    cfg.save(cfg.config_path)

    saved = json.loads(cfg.config_path.read_text())
    assert saved["system"]["data_dir"] == str(tmp_path / "data")
    assert saved["system"]["dry_run"] is False

    # Overrides still work (and still aren't saved) on such an object
    cfg.override_system(dry_run=True)
    assert cfg.system.dry_run is True
    assert cfg.to_dict()["system"]["dry_run"] is False


# ---------------------------------------------------------------------------
# Non-flight ending-hours override
# ---------------------------------------------------------------------------

def _make_override_card(tmp_path, with_processed_flight_between):
    """Old pending flight, optional processed flight, then a newer non-flight."""
    from avcardtool.core.processed_files import ProcessedFilesDatabase as DB
    from avcardtool.core import hash_file

    data_log = tmp_path / "card" / "data_log"
    data_log.mkdir(parents=True)
    lines = SAMPLE_FLIGHT.read_text().splitlines(keepends=True)

    def write(name, airframe, engine, rows=None):
        header = lines[0].replace('airframe_hours="94.6"', f'airframe_hours="{airframe}"') \
                         .replace('engine_hours="53.1"', f'engine_hours="{engine}"')
        body = lines[1:] if rows is None else lines[1:rows]
        path = data_log / name
        path.write_text(header + "".join(body))
        return path

    write("log_20251107_100757_KOAK.csv", "94.6", "53.1")
    middle = write("log_20260327_152821_KOAK.csv", "120.0", "80.0")
    # Ground power cycle: header + a few stationary rows, authoritative hours
    write("log_20260917_171437_KOAK.csv", "161.6", "167.5", rows=10)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = DB(data_dir / "processed_files.json")
    if with_processed_flight_between:
        db.mark_processed(hash_file(middle), middle, "N662EZ", True)
    else:
        middle.unlink()
        db.mark_processed("unrelated", middle, "N1", False)  # DB exists -> not a first run
    return tmp_path / "card", _write_config_no_uploaders(tmp_path, data_dir)


def _write_config_no_uploaders(tmp_path, data_dir):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"system": {
        "data_dir": str(data_dir),
        "log_file": str(tmp_path / "avcardtool.log"),
    }}))
    return cfg_path


class TestNonFlightOverride:
    def test_override_applied_to_immediately_preceding_flight(self, tmp_path):
        card, cfg_path = _make_override_card(tmp_path, with_processed_flight_between=False)
        result = CliRunner().invoke(cli, ["--config", str(cfg_path), "--dry-run", "auto-process", str(card)])
        assert result.exit_code == 0, result.output
        assert "log_20260917_171437_KOAK.csv" in result.output
        assert "Hobbs ending adjusted" in result.output

    def test_override_not_applied_across_processed_flight(self, tmp_path):
        card, cfg_path = _make_override_card(tmp_path, with_processed_flight_between=True)
        result = CliRunner().invoke(cli, ["--config", str(cfg_path), "--dry-run", "auto-process", str(card)])
        assert result.exit_code == 0, result.output
        assert "Flight detected" in result.output
        assert "ending adjusted" not in result.output
