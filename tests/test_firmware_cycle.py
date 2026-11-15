"""One-card firmware cycle: GTN Xi on its own database card, confirmation link, navdata block."""

import http.client
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

from avcardtool import cli as cli_mod
from avcardtool.cli import cli
from avcardtool.core.config import Config
from avcardtool.firmware import auto as fw_auto
from avcardtool.firmware import cards as fw_cards
from avcardtool.firmware import confirm_server, cycle
from avcardtool.firmware.registry import PRODUCTS
from avcardtool.firmware.state import FirmwareState
from test_firmware import (  # noqa: F401  (fixtures and helpers shared with the main firmware tests)
    GTN_MARKER, FakeDetector, FakeManager, cfg_for, fake_env, make_card, make_g3x_pkg,
    make_gtn_pkg, no_network, release, sd, tree,
)

GTN = PRODUCTS["gtn_xi"]
SERIAL = "TEST-0001"          # synthetic
IDENTITY = {"avionics.txt": b"GTN 650 Xi\n", "feat_unlk.dat": b"F" * 913, ".evidf.dat": b"EVID",
            ".gadm.meta": b'{"gadmAvionicsName": "GTN 650 Xi"}'}
DATABASES = {"ldr_sys/avtn_db.bin": b"d" * 400, "ldr_sys/nav_db2.bin": b"n" * 300,
             ".navdata_cycles.json": b'{"cycles": {}}', "fc_tpc/fc_tpc.dat": b"c" * 100}
ONLY_GTN = {"products": {"g3x_touch": {"enabled": False}}}


def make_db_card(tmp_path, name="dbcard"):
    card = tmp_path / name
    card.mkdir()
    for rel, data in {**IDENTITY, **DATABASES}.items():
        (card / rel).parent.mkdir(parents=True, exist_ok=True)
        (card / rel).write_bytes(data)
    return card


@pytest.fixture
def env(tmp_path, fake_env, monkeypatch):
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    pkg = make_gtn_pkg(tmp_path)
    card = make_db_card(tmp_path)
    fake_env.cards = [sd("/dev/sda1", card, SERIAL)]
    return SimpleNamespace(tmp=tmp_path, pkg=pkg, card=card, det=fake_env)


def state_of(tmp_path):
    return FirmwareState(tmp_path / "data")


def run_auto(tmp_path, mgr=None, **kw):
    cfg = cfg_for(tmp_path, **{**ONLY_GTN, **kw})
    return fw_auto.run_firmware_auto_update(cfg, None, mock.Mock(), mgr)


def set_baseline(tmp_path, version="21.00"):
    st = state_of(tmp_path)
    st.set_installed(SERIAL, "gtn_xi", version)
    st.save()


# ---------------------------------------------------------------------------
# recognising the database card
# ---------------------------------------------------------------------------

def test_is_database_card(tmp_path):
    assert fw_cards.is_database_card(GTN, make_db_card(tmp_path))
    only_db = make_card(tmp_path, name="only")
    (only_db / "ldr_sys").mkdir()
    (only_db / "ldr_sys" / "nav_db2.bin").write_bytes(b"x")
    assert fw_cards.is_database_card(GTN, only_db)
    assert not fw_cards.is_database_card(GTN, make_card(tmp_path, name="blank"))
    # a G3X card is never taken for a GTN one, whatever else is on it
    g3x = make_db_card(tmp_path, "g3x")
    (g3x / "data_log").mkdir()
    (g3x / "data_log" / "log_1.csv").write_text('#airframe_info,product="GDU 460",software_version="9.52"\n')
    assert not fw_cards.is_database_card(GTN, g3x)
    assert not fw_cards.is_database_card(PRODUCTS["g3x_touch"], make_db_card(tmp_path, "x"))


# ---------------------------------------------------------------------------
# start gate
# ---------------------------------------------------------------------------

def test_no_baseline_never_wipes_and_says_how_to_set_it(env):
    before = tree(env.card)
    mgr = FakeManager()
    assert run_auto(env.tmp, mgr) == []
    assert tree(env.card) == before
    assert state_of(env.tmp).pending_items() == []
    body = next(e for e in mgr.events if e.event_type == "firmware_available").body
    assert "firmware set-installed gtn_xi" in body and "--erase" in body


def test_baseline_not_older_than_release_does_nothing(env):
    set_baseline(env.tmp, "21.12")
    before = tree(env.card)
    run_auto(env.tmp, FakeManager())
    assert tree(env.card) == before and state_of(env.tmp).pending_items() == []


def test_cycle_disabled_by_config(env):
    set_baseline(env.tmp)
    before = tree(env.card)
    run_auto(env.tmp, FakeManager(), database_card_cycle=False)
    assert tree(env.card) == before


def test_other_products_card_and_blank_serial_never_cycle(env):
    set_baseline(env.tmp)
    (env.card / "data_log").mkdir()
    (env.card / "data_log" / "l.csv").write_text('#airframe_info,product="GDU 460",software_version="9.0"\n')
    before = tree(env.card)
    run_auto(env.tmp, FakeManager())
    assert tree(env.card) == before
    (env.card / "data_log" / "l.csv").unlink()
    (env.card / "data_log").rmdir()
    env.det.cards = [sd("/dev/sda1", env.card, "")]
    before = tree(env.card)
    run_auto(env.tmp, FakeManager())
    assert tree(env.card) == before


# ---------------------------------------------------------------------------
# the cycle
# ---------------------------------------------------------------------------

def test_cycle_backs_up_wipes_writes_and_asks_for_confirmation(env):
    set_baseline(env.tmp)
    mgr = FakeManager()
    issues = run_auto(env.tmp, mgr, confirm_url="https://pi.example/")
    assert issues == []
    image = {f.path: (env.pkg.image_dir / f.path).read_bytes() for f in env.pkg.files}
    assert tree(env.card) == image                                    # databases and identity files gone
    backup = cycle.backup_dir_for(env.tmp / "data", SERIAL)
    for name, data in IDENTITY.items():
        assert (backup / name).read_bytes() == data
    assert not (backup / ".navdata_cycles.json").exists()

    pend = state_of(env.tmp).pending(SERIAL, "gtn_xi")
    assert pend["version"] == "21.12" and len(pend["token"]) >= 40 and pend["notified_at"]
    ev = next(e for e in mgr.events if e.event_type == "firmware_confirm_needed")
    assert ev.severity.value.lower() == "warning" and "confirm when installed" in ev.title
    assert f"https://pi.example/firmware/confirm/{pend['token']}" in ev.body
    assert SERIAL not in ev.body and SERIAL not in json.dumps(ev.data)

    blocked = cli_mod._navdata_block_reason(cfg_for(env.tmp), SERIAL)
    assert blocked and "firmware confirm" in blocked and "firmware cancel" in blocked
    assert cli_mod._navdata_block_reason(cfg_for(env.tmp), "OTHER-SERIAL") is None

    # the next run finds the card written and does nothing more (no second notification)
    before, n_events = tree(env.card), len(mgr.events)
    run_auto(env.tmp, mgr)
    assert tree(env.card) == before
    assert [e.event_type for e in mgr.events[n_events:]].count("firmware_confirm_needed") == 0


def test_without_link_the_commands_are_given(env):
    set_baseline(env.tmp)
    mgr = FakeManager()
    run_auto(env.tmp, mgr)
    body = next(e for e in mgr.events if e.event_type == "firmware_confirm_needed").body
    assert "avcardtool firmware confirm gtn_xi" in body and "avcardtool firmware cancel gtn_xi" in body
    assert "http" not in body


def test_pending_is_saved_before_erase_and_a_failed_write_resumes(env, monkeypatch):
    set_baseline(env.tmp)
    real = fw_cards._copy_verified
    calls = []

    def flaky(src, dest, f):
        calls.append(f.path)
        if len(calls) == 2:
            raise fw_cards.FirmwareError("simulated write failure")
        return real(src, dest, f)

    monkeypatch.setattr(fw_cards, "_copy_verified", flaky)
    mgr = FakeManager()
    issues = run_auto(env.tmp, mgr)
    assert any(sev == "ERROR" for sev, _ in issues)
    st = state_of(env.tmp)
    assert st.pending(SERIAL, "gtn_xi") and st.navdata_blocked(SERIAL)
    assert not (env.card / "ldr_sys" / "avtn_db.bin").exists()            # wipe had happened
    assert not any(e.event_type == "firmware_confirm_needed" for e in mgr.events)
    backup = cycle.backup_dir_for(env.tmp / "data", SERIAL)
    assert (backup / "feat_unlk.dat").read_bytes() == IDENTITY["feat_unlk.dat"]
    token = st.pending(SERIAL, "gtn_xi")["token"]

    monkeypatch.setattr(fw_cards, "_copy_verified", real)
    assert run_auto(env.tmp, mgr) == []                                    # resumes: card has no loader marker
    image = {f.path: (env.pkg.image_dir / f.path).read_bytes() for f in env.pkg.files}
    assert tree(env.card) == image
    assert (backup / "feat_unlk.dat").read_bytes() == IDENTITY["feat_unlk.dat"]   # backup untouched by resume
    assert state_of(env.tmp).pending(SERIAL, "gtn_xi")["token"] == token
    assert any(e.event_type == "firmware_confirm_needed" for e in mgr.events)


def test_failed_confirmation_notice_is_retried_and_reminded_weekly(env):
    set_baseline(env.tmp)
    run_auto(env.tmp, FakeManager(ok=False))
    st = state_of(env.tmp)
    assert st.pending(SERIAL, "gtn_xi")["notified_at"] is None
    good = FakeManager()
    run_auto(env.tmp, good)
    assert [e.event_type for e in good.events].count("firmware_confirm_needed") == 1

    st = state_of(env.tmp)
    now = datetime.now()
    assert st.confirm_due(SERIAL, "gtn_xi", now + timedelta(days=6)) is None
    assert st.confirm_due(SERIAL, "gtn_xi", now + timedelta(days=7)) == "reminder"
    mgr = FakeManager()
    cfg = cfg_for(env.tmp)
    assert cycle.send_due_notifications(cfg, st, mgr, mock.Mock(), now=now + timedelta(days=8)) == 1
    assert mgr.events[0].title.startswith("Reminder")
    assert cycle.send_due_notifications(cfg, st, mgr, mock.Mock(), now=now + timedelta(days=9)) == 0


def test_dry_run_writes_nothing_anywhere(env):
    set_baseline(env.tmp)
    snapshot = tree(env.tmp)
    mgr = FakeManager()
    cfg = cfg_for(env.tmp, dry_run=True, **ONLY_GTN)
    assert fw_auto.run_firmware_auto_update(cfg, None, mock.Mock(), mgr) == []
    assert tree(env.tmp) == snapshot and mgr.events == []


# ---------------------------------------------------------------------------
# navdata block
# ---------------------------------------------------------------------------

def _pending(tmp_path):
    st = state_of(tmp_path)
    st.set_installed(SERIAL, "gtn_xi", "21.00")
    st.start_pending(SERIAL, "gtn_xi", "21.12", None)
    st.save()


def test_navdata_auto_update_skips_blocked_card(tmp_path):
    _pending(tmp_path)
    cfg = Config(config_path=None)
    cfg.system.data_dir = str(tmp_path / "data")
    card = SimpleNamespace(device_path="/dev/sda1", mount_point=str(tmp_path), volume_id=SERIAL)
    aircraft = SimpleNamespace(devices=[], tail_number="TEST")
    ctx = SimpleNamespace(obj={"config": cfg})
    with mock.patch("avcardtool.navdata.garmin.auth.GarminAuth.ensure_authenticated", return_value=True), \
            mock.patch("avcardtool.navdata.garmin.api.FlyGarminAPI.list_aircraft", return_value=[aircraft]), \
            mock.patch("avcardtool.navdata.sdcard.SDCardDetector.scan_for_cards", return_value=[card]), \
            mock.patch("avcardtool.core.utils._is_mounted_readonly", side_effect=AssertionError("touched")), \
            mock.patch.object(cli_mod, "_resolve_target_db_type", side_effect=AssertionError("not skipped")):
        cli_mod._navdata_auto_update_impl(ctx, None)


def test_manual_navdata_install_refuses_blocked_card(tmp_path):
    _pending(tmp_path)
    src = tmp_path / "dl"
    src.mkdir()
    (src / "navdata_manifest.json").write_text(json.dumps({"card_serial": SERIAL, "entries": [{"x": 1}]}))
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"), "log_file": str(tmp_path / "l")}}))
    r = CliRunner().invoke(cli, ["--config", str(cfgp), "navdata", "install", str(tmp_path), "--from", str(src), "--yes"])
    assert r.exit_code == 1 and "paused" in r.output and "firmware confirm" in r.output


# ---------------------------------------------------------------------------
# confirm / cancel / restore
# ---------------------------------------------------------------------------

def written_card(env):
    set_baseline(env.tmp)
    run_auto(env.tmp, FakeManager())
    assert state_of(env.tmp).pending(SERIAL, "gtn_xi")


def restore(env):
    cfg = cfg_for(env.tmp)
    tracker, restored = fw_auto.run_restore_pass(cfg, None, mock.Mock())
    return tracker, restored


def test_confirm_restores_identity_files_and_lifts_the_block(env):
    written_card(env)
    st = state_of(env.tmp)
    assert st.confirm(SERIAL, "gtn_xi") == "21.12"
    st.save()
    assert state_of(env.tmp).installed_version(SERIAL, "gtn_xi") == "21.12"
    assert state_of(env.tmp).navdata_blocked(SERIAL) is None
    assert cli_mod._navdata_block_reason(cfg_for(env.tmp), SERIAL) is None

    tracker, restored = restore(env)
    assert restored == {SERIAL}
    assert tree(env.card) == IDENTITY                      # loader files gone, identity files back, no databases
    assert state_of(env.tmp).restore_items() == []
    assert not cycle.backup_dir_for(env.tmp / "data", SERIAL).exists()
    assert not (env.card / ".navdata_cycles.json").exists()     # navdata will install everything

    # same run: the post-step must not start another cycle for this card
    st = state_of(env.tmp)
    d = cycle.decide(GTN, make_gtn_pkg(env.tmp, "21.20"), env.card, SERIAL, st, cfg_for(env.tmp).firmware, restored)
    assert d.action in ("skip", "current")
    st.set_installed(SERIAL, "gtn_xi", "21.00")
    assert cycle.decide(GTN, make_gtn_pkg(env.tmp, "21.20"), env.card, SERIAL, st,
                        cfg_for(env.tmp).firmware, restored).action == "skip"


def test_prestep_leaves_mounts_for_navdata_until_released(env, monkeypatch):
    written_card(env)
    st = state_of(env.tmp)
    st.confirm(SERIAL, "gtn_xi")
    st.save()
    env.det.cards = [sd("/dev/sdb1", None, SERIAL)]          # found unmounted
    monkeypatch.setattr(fw_auto, "resolve_device_mount_point", lambda dev, readonly=True: env.card)
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: True)
    tracker, restored = restore(env)
    assert restored == {SERIAL} and env.det.unmounted == []     # still mounted for navdata
    tracker.release()
    assert env.det.unmounted == [str(env.card)]


def test_cancel_declines_the_version_until_a_newer_one(env):
    written_card(env)
    st = state_of(env.tmp)
    assert st.cancel(SERIAL, "gtn_xi") == "21.12"
    st.save()
    st = state_of(env.tmp)
    assert st.installed_version(SERIAL, "gtn_xi") == "21.00" and st.is_declined(SERIAL, "gtn_xi", "21.12")
    _t, restored = restore(env)
    assert tree(env.card) == IDENTITY and restored == {SERIAL}

    before = tree(env.card)
    run_auto(env.tmp, FakeManager())                          # same version: not wiped again
    assert tree(env.card) == before
    newer = make_gtn_pkg(env.tmp, "21.13")
    d = cycle.decide(GTN, newer, env.card, SERIAL, state_of(env.tmp), cfg_for(env.tmp).firmware)
    assert d.action == "start"


def test_restore_never_erases_a_card_that_has_databases(env):
    set_baseline(env.tmp)
    st = state_of(env.tmp)
    st.start_pending(SERIAL, "gtn_xi", "21.12", str(cycle.backup_dir_for(env.tmp / "data", SERIAL)))
    st.confirm(SERIAL, "gtn_xi")
    st.save()
    before = tree(env.card)                                    # still a full database card
    _t, restored = restore(env)
    assert tree(env.card) == before and restored == set()
    assert state_of(env.tmp).restore_items() == []


def test_restore_without_backup_writes_nothing_back(env):
    written_card(env)
    st = state_of(env.tmp)
    st.confirm(SERIAL, "gtn_xi")
    st.save()
    import shutil
    shutil.rmtree(cycle.backup_dir_for(env.tmp / "data", SERIAL))
    restore(env)
    assert tree(env.card) == {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cfg_file(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"), "log_file": str(tmp_path / "l")}}))
    return str(p)


def test_cli_set_installed_confirm_cancel_status(tmp_path):
    c = _cfg_file(tmp_path)
    run = lambda *a: CliRunner().invoke(cli, ["--config", c, "firmware", *a])  # noqa: E731
    assert run("set-installed", "gtn_xi", "21.00", "--card", SERIAL).exit_code == 0
    assert state_of(tmp_path).installed_version(SERIAL, "gtn_xi") == "21.00"
    assert run("set-installed", "gtn_xi", "x.y", "--card", SERIAL).exit_code != 0
    assert run("confirm").exit_code != 0                                    # nothing pending
    st = state_of(tmp_path)
    st.start_pending(SERIAL, "gtn_xi", "21.12", None)
    st.save()
    out = run("status")
    assert "21.00" in out.output and "awaiting confirmation" in out.output
    assert run("confirm", "--card", SERIAL).exit_code == 0
    assert state_of(tmp_path).installed_version(SERIAL, "gtn_xi") == "21.12"


def test_cli_install_erase_on_database_card_starts_the_cycle(tmp_path, env):
    cfgp = _cfg_file(tmp_path)
    with mock.patch("avcardtool.firmware.catalog.fetch_release", return_value=release("gtn_xi", "21.12")), \
            mock.patch("avcardtool.firmware.packages.ensure_package", return_value=env.pkg), \
            mock.patch("avcardtool.navdata.sdcard.SDCardDetector.scan_for_cards",
                       return_value=[sd("/dev/sda1", env.card, SERIAL)]):
        r = CliRunner().invoke(cli, ["--config", cfgp, "firmware", "install", "gtn_xi", str(env.card),
                                     "--erase", "--yes"])
    assert r.exit_code == 0, r.output
    assert state_of(tmp_path).pending(SERIAL, "gtn_xi")["version"] == "21.12"
    assert (env.card / "ldr_part_nmbr.txt").exists() and not (env.card / "ldr_sys" / "avtn_db.bin").exists()
    assert (cycle.backup_dir_for(tmp_path / "data", SERIAL) / "avionics.txt").exists()
    assert "paused" in r.output


def test_config_new_fields(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"firmware": {"confirm_url": "https://pi.example", "confirm_listen": "0.0.0.0:9000",
                                          "database_card_cycle": False}}))
    cfg = Config(config_path=p)
    assert (cfg.firmware.confirm_url, cfg.firmware.confirm_listen, cfg.firmware.database_card_cycle) == \
        ("https://pi.example", "0.0.0.0:9000", False)
    cfg.system.data_dir = str(tmp_path / "d")
    assert cfg.validate()
    cfg.firmware.confirm_url = "ftp://x"
    with pytest.raises(ValueError):
        cfg.validate()
    cfg.firmware.confirm_url = ""
    cfg.firmware.confirm_listen = "nonsense"
    with pytest.raises(ValueError):
        cfg.validate()
    assert Config(config_path=None).notifications.events["firmware_confirm_needed"] is True


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    st = state_of(tmp_path)
    token = st.start_pending(SERIAL, "gtn_xi", "21.12", None)
    st.save()
    launches = []
    srv = confirm_server.make_server(tmp_path / "data", "127.0.0.1", 0, lambda: launches.append(1))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield SimpleNamespace(port=srv.server_address[1], token=token, launches=launches, tmp=tmp_path)
    srv.shutdown()
    srv.server_close()


def http_req(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read().decode()
    conn.close()
    return r.status, data


def form(action):
    return f"action={action}", {"Content-Type": "application/x-www-form-urlencoded"}


def test_server_get_shows_page_and_changes_nothing(server):
    status, page = http_req(server, "GET", f"/firmware/confirm/{server.token}")
    assert status == 200 and "GTN Xi" in page and "21.12" in page and 'method="post"' in page
    assert SERIAL not in page
    assert state_of(server.tmp).pending(SERIAL, "gtn_xi") and state_of(server.tmp).installed_version(SERIAL, "gtn_xi") is None
    assert server.launches == []


def test_server_post_installed_once_then_404(server):
    body, hdr = form("installed")
    status, page = http_req(server, "POST", f"/firmware/confirm/{server.token}", body, hdr)
    assert status == 200
    st = state_of(server.tmp)
    assert st.installed_version(SERIAL, "gtn_xi") == "21.12" and st.pending(SERIAL, "gtn_xi") is None
    assert st.restore_items() and server.launches == [1]
    assert http_req(server, "POST", f"/firmware/confirm/{server.token}", body, hdr)[0] == 404
    assert http_req(server, "GET", f"/firmware/confirm/{server.token}")[0] == 404


def test_server_post_cancel(server):
    body, hdr = form("cancel")
    assert http_req(server, "POST", f"/firmware/confirm/{server.token}", body, hdr)[0] == 200
    st = state_of(server.tmp)
    assert st.installed_version(SERIAL, "gtn_xi") is None and st.is_declined(SERIAL, "gtn_xi", "21.12")


def test_server_rejects_bad_requests(server):
    body, hdr = form("installed")
    assert http_req(server, "GET", "/firmware/confirm/" + "A" * 43)[0] == 404          # unknown token
    assert http_req(server, "POST", "/firmware/confirm/" + "A" * 43, body, hdr)[0] == 404
    assert http_req(server, "GET", "/")[0] == 404
    assert http_req(server, "GET", "/firmware/")[0] == 404
    assert http_req(server, "GET", "/etc/passwd")[0] == 404
    assert http_req(server, "POST", f"/firmware/confirm/{server.token}", "action=explode", hdr)[0] == 400
    big, _ = form("x" * 10000)
    assert http_req(server, "POST", f"/firmware/confirm/{server.token}", big, hdr)[0] == 413
    assert state_of(server.tmp).pending(SERIAL, "gtn_xi") and server.launches == []


def test_server_escapes_html(server):
    st = state_of(server.tmp)
    st.pending(SERIAL, "gtn_xi")["version"] = "<script>alert(1)</script>"
    st.save()
    status, page = http_req(server, "GET", f"/firmware/confirm/{server.token}")
    assert status == 200 and "<script>alert" not in page and "&lt;script&gt;" in page


def test_launcher_starts_a_detached_navdata_run():
    cfg = SimpleNamespace(config_path=Path("/etc/x/config.json"))
    with mock.patch.object(confirm_server.subprocess, "Popen") as popen:
        confirm_server.launch_navdata_run(cfg)
    cmd = popen.call_args[0][0]
    assert cmd[-2:] == ["navdata", "auto-update"] and "--config" in cmd
    assert popen.call_args[1]["start_new_session"] is True


def test_confirm_from_another_process_survives_a_later_save(tmp_path):
    """A nightly run that read state before the pilot confirmed must not undo the confirm."""
    from avcardtool.firmware.state import FirmwareState

    first = FirmwareState(tmp_path)
    first.set_installed("CARD", "gtn_xi", "21.04")
    first.start_pending("CARD", "gtn_xi", "21.12", None)
    first.save()

    nightly = FirmwareState(tmp_path)            # long run starts, sees the cycle pending
    link = FirmwareState(tmp_path)               # pilot clicks the link meanwhile
    assert link.confirm("CARD", "gtn_xi") == "21.12"
    link.save()

    nightly.mark_confirm_sent("CARD", "gtn_xi")  # the run touches the stale entry, then saves
    nightly.record_seen("gtn_xi", "21.12")
    nightly.save()

    after = FirmwareState(tmp_path)
    assert after.pending("CARD", "gtn_xi") is None
    assert after.installed_version("CARD", "gtn_xi") == "21.12"
    assert after.restore_items("CARD")
    assert after.latest_seen("gtn_xi") == "21.12"
    assert nightly.navdata_blocked("CARD") is None
