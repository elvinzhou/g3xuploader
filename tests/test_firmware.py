"""Tests for avionics firmware handling (no network, no real cards)."""

import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

from avcardtool.cli import cli
from avcardtool.core.config import Config, FirmwareConfig, NotificationsConfig, SystemConfig
from avcardtool.firmware import auto as fw_auto
from avcardtool.firmware import cards as fw_cards
from avcardtool.firmware import packages as fw_pkgs
from avcardtool.firmware.catalog import (
    FirmwareNetworkError,
    FirmwareRelease,
    compare_versions,
    is_newer,
    parse_release_page,
    validate_download_url,
)
from avcardtool.firmware.registry import PRODUCTS, FirmwareError
from avcardtool.firmware.state import FirmwareState
from avcardtool.navdata.sdcard import SDCardInfo
from avcardtool.notifications import events as ev

DATA = Path(__file__).parent / "test_data"
G3X = PRODUCTS["g3x_touch"]
GTN = PRODUCTS["gtn_xi"]

GTN_MARKER = b" -GTN Xi-\r\nGPN 006-B3191-17\r\nRev 0A"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("test tried to use the network")
    monkeypatch.setattr("requests.sessions.Session.request", refuse)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def release(key="g3x_touch", version="10.00"):
    return FirmwareRelease(key, version, "October 20, 2026",
                           "https://download.garmin.com/software/x.zip", "title", ["n1"], "https://www8.garmin.com/p")


def make_g3x_zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(f"Garmin/{name}", data)
        z.writestr("Instructions.txt", "copy the Garmin folder")
    return path


def make_g3x_pkg(tmp_path, version="10.00", files=None, name="zips") -> fw_pkgs.PreparedPackage:
    files = files or {"A.GCD": b"a" * 100, "B.GCD": b"b" * 200}
    zdir = tmp_path / name
    zdir.mkdir(exist_ok=True)
    zp = make_g3x_zip(zdir / f"g3x_{version}.zip", files)
    root = fw_pkgs.package_dir(tmp_path / "data", "g3x_touch", version)
    root.mkdir(parents=True)
    return fw_pkgs.prepare_package(zp, G3X, release("g3x_touch", version), root)


def make_gtn_pkg(tmp_path, version="21.12", marker=GTN_MARKER, extra=None) -> fw_pkgs.PreparedPackage:
    root = fw_pkgs.package_dir(tmp_path / "data", "gtn_xi", version)
    image = root / "image"
    tree = {"ldr_part_nmbr.txt": marker, "sys_files.gca": b"g" * 300, "sw/app.bin": b"s" * 500,
            "ldr_sys/grm_feat_key.zip": b"k" * 50}
    tree.update(extra or {})
    files = []
    import hashlib
    for rel, data in tree.items():
        p = image / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files.append(fw_pkgs.ImageFile(rel, len(data), hashlib.sha256(data).hexdigest()))
    pkg = fw_pkgs.PreparedPackage("gtn_xi", version, "July 17, 2026", "https://download.garmin.com/g.zip",
                                  "copy2card", True, files, None, root)
    (root / "package.json").write_text(json.dumps(pkg.to_json()))
    assert fw_pkgs.load_package(root)
    return pkg


def log_header(version="9.52", product="GDU 460"):
    return (f'#airframe_info,log_version="1.00",product="{product}",aircraft_ident="TEST",'
            f'software_version="{version}",unit="MFD1"\nDate,Time\n')


def make_card(tmp_path, name="card", log=None, extra_logs=()):
    card = tmp_path / name
    card.mkdir()
    if log is not None:
        d = card / "data_log"
        d.mkdir()
        for fname, text in extra_logs:
            (d / fname).write_text(text)
        (d / "log_20260301_120000_KXXX.csv").write_text(log)
    return card


def tree(card: Path):
    return {p.relative_to(card).as_posix(): p.read_bytes() for p in sorted(card.rglob("*")) if p.is_file()}


class FakeManager:
    def __init__(self, ok=True):
        self.events, self.ok, self.dry_run = [], ok, False

    def notify(self, event, force=False):
        self.events.append(event)
        return [SimpleNamespace(success=self.ok, backend="fake", message="")]


def cfg_for(tmp_path, **kw):
    return SimpleNamespace(system=SystemConfig(data_dir=str(tmp_path / "data"),
                                               dry_run=kw.pop("dry_run", False)),
                           firmware=FirmwareConfig(**kw))


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def test_parse_g3x_page():
    rel = parse_release_page((DATA / "garmin_g3x_touch_page.html").read_text(), G3X)
    assert rel.version == "10.00"
    assert rel.release_date == "October 20, 2026"
    assert rel.download_url == "https://download.garmin.com/software/GDU4xxCockpitDisplay_OtherOperatingSystem__1000.zip"
    assert "software version 10.00" in rel.title
    # only the newest "Changes made from 9.52 to 10.00" block
    assert len(rel.notes) == 8 and rel.notes[0].startswith("Added support for GCO 14")
    assert not any("turbine" in n for n in rel.notes)
    assert rel.page_url == G3X.page_url


def test_parse_gtn_page():
    rel = parse_release_page((DATA / "garmin_gtn_xi_page.html").read_text(), GTN)
    assert rel.version == "21.12"
    assert rel.release_date == "July 17, 2026"
    assert rel.download_url.endswith("_2112.zip") and rel.download_url.startswith("https://download.garmin.com/")
    assert rel.notes[0].startswith("This software update is intended to be loaded on GTN 6XX Xi")
    assert any("Smart Glide" in n for n in rel.notes)
    assert all("<" not in n for n in rel.notes)


def test_parse_tolerates_whitespace_and_missing_parts():
    page = ('<h2>\n  Thing   software  version\n 3.10\n</h2>\n<p  class="quiet" >  as of  May 1, 2027 </p>'
            "<a\n href='https://dl.garmin.com/a.ZIP'>x</a>")
    rel = parse_release_page(page, G3X)
    assert (rel.version, rel.release_date, rel.notes) == ("3.10", "May 1, 2027", [])


@pytest.mark.parametrize("page,fragment", [
    ("<h2>Nothing here</h2>", "software version"),
    ("<h2>X software version 1.0</h2><p>no link</p>", ".zip"),
])
def test_parse_layout_change_raises(page, fragment):
    with pytest.raises(FirmwareError, match=fragment):
        parse_release_page(page, G3X)


@pytest.mark.parametrize("url", [
    "http://download.garmin.com/software/x.zip",
    "https://evil.example.com/garmin.com/x.zip",
    "https://garmin.com.evil.example/x.zip",
    "https://notgarmin.com/x.zip",
    "ftp://download.garmin.com/x.zip",
])
def test_download_url_rejected(url):
    with pytest.raises(FirmwareError):
        validate_download_url(url)
    page = f"<h2>X software version 1.0</h2><a href=\"{url}\">dl</a>"
    with pytest.raises(FirmwareError):
        parse_release_page(page, G3X)


@pytest.mark.parametrize("url", ["https://download.garmin.com/a.zip", "https://garmin.com/a.zip"])
def test_download_url_accepted(url):
    assert validate_download_url(url) == url


def test_version_comparison():
    assert is_newer("10.00", "9.52")
    assert not is_newer("9.52", "10.00")
    assert compare_versions("9.52", "9.52") == 0
    assert compare_versions("10", "10.00") == 0
    assert compare_versions("21.12", "21.9") == 1
    assert compare_versions("9.5", "9.10") == -1
    with pytest.raises(FirmwareError):
        compare_versions("abc", "1.0")


# ---------------------------------------------------------------------------
# state: notify once per version
# ---------------------------------------------------------------------------

def _refresh(tmp_path, manager, version="10.00"):
    cfg = cfg_for(tmp_path)
    pkg = make_g3x_pkg(tmp_path, version) if not fw_pkgs.load_package(
        fw_pkgs.package_dir(tmp_path / "data", "g3x_touch", version)) else fw_pkgs.load_package(
        fw_pkgs.package_dir(tmp_path / "data", "g3x_touch", version))
    state = FirmwareState(tmp_path / "data")
    with mock.patch.object(fw_auto, "fetch_release", return_value=release(version=version)):
        fw_auto.refresh_product(cfg, G3X, state, manager, mock.Mock(), [])
    return state


def test_new_version_notified_once(tmp_path):
    mgr = FakeManager()
    _refresh(tmp_path, mgr)
    _refresh(tmp_path, mgr)
    assert [e.event_type for e in mgr.events] == ["firmware_available"]
    assert "10.00" in mgr.events[0].title
    # persisted
    st = json.loads((tmp_path / "data" / "firmware" / "state.json").read_text())
    assert st["g3x_touch"]["latest_version"] == "10.00" and st["g3x_touch"]["notified"] == ["10.00"]
    # a newer version is announced again
    make_g3x_pkg(tmp_path, "10.10", name="zips2")
    _refresh(tmp_path, mgr, "10.10")
    assert len(mgr.events) == 2


def test_failed_notification_is_retried(tmp_path):
    bad = FakeManager(ok=False)
    _refresh(tmp_path, bad)
    assert not FirmwareState(tmp_path / "data").is_notified("g3x_touch", "10.00")
    good = FakeManager()
    _refresh(tmp_path, good)
    assert len(good.events) == 1


def test_state_atomic_write(tmp_path):
    st = FirmwareState(tmp_path)
    st.mark_notified("gtn_xi", "21.12")
    st.save()
    assert not list((tmp_path / "firmware").glob("*.tmp"))
    assert FirmwareState(tmp_path).is_notified("gtn_xi", "21.12")


# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------

def test_g3x_package_image_and_idempotence(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    assert sorted(f.path for f in pkg.files) == ["Garmin/A.GCD", "Garmin/B.GCD"]
    assert (pkg.image_dir / "Garmin" / "A.GCD").read_bytes() == b"a" * 100
    assert not pkg.erase_destination
    assert fw_pkgs.load_package(pkg.root) is not None
    (pkg.image_dir / "Garmin" / "A.GCD").write_bytes(b"short")
    assert fw_pkgs.load_package(pkg.root) is None          # size mismatch -> incomplete


def test_zip_path_traversal_rejected(tmp_path):
    for bad in ("Garmin/../../evil.GCD", "/abs/evil.GCD", "C:/evil.GCD"):
        zp = tmp_path / "bad.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("Garmin/ok.GCD", b"x")
            z.writestr(bad, b"x")
        root = tmp_path / ("pkg" + str(abs(hash(bad))))
        root.mkdir()
        with pytest.raises(FirmwareError, match="Unsafe path"):
            fw_pkgs.prepare_package(zp, G3X, release(), root)
        assert not (root / "image").exists()
    assert not (tmp_path / "evil.GCD").exists()


def test_corrupt_zip_rejected(tmp_path):
    zp = tmp_path / "x.zip"
    zp.write_bytes(b"this is not a zip")
    root = tmp_path / "pkg"
    root.mkdir()
    with pytest.raises(FirmwareError):
        fw_pkgs.prepare_package(zp, G3X, release(), root)


def test_prune_keeps_newest_two(tmp_path):
    for v in ("9.40", "9.52", "10.00", "10.10"):
        fw_pkgs.package_dir(tmp_path, "g3x_touch", v).mkdir(parents=True)
    fw_pkgs.prune_cache(tmp_path, "g3x_touch")
    left = sorted(d.name for d in (tmp_path / "firmware" / "g3x_touch").iterdir())
    assert left == ["10.00", "10.10"]


class FakeResp:
    def __init__(self, body, length=None, url="https://download.garmin.com/x.zip"):
        self.body, self.headers, self.url = body, {"content-length": str(len(body) if length is None else length)}, url

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        yield self.body[: len(self.body) // 2]
        yield self.body[len(self.body) // 2:]


def test_download_zip_complete_incomplete_and_reuse(tmp_path):
    body = b"z" * 1000
    sess = mock.Mock()
    sess.head.return_value = SimpleNamespace(status_code=200, headers={"content-length": "1000"})
    sess.get.return_value = FakeResp(body)
    dest = fw_pkgs.download_zip("https://download.garmin.com/software/x.zip", tmp_path / "d", sess)
    assert dest.read_bytes() == body and not list((tmp_path / "d").glob("*.part"))
    fw_pkgs.download_zip("https://download.garmin.com/software/x.zip", tmp_path / "d", sess)
    assert sess.get.call_count == 1                        # reused, not downloaded again

    sess2 = mock.Mock()
    sess2.head.return_value = SimpleNamespace(status_code=200, headers={"content-length": "1000"})
    sess2.get.return_value = FakeResp(b"z" * 500, length=1000)
    with pytest.raises(FirmwareError, match="Incomplete"):
        fw_pkgs.download_zip("https://download.garmin.com/software/y.zip", tmp_path / "d2", sess2)
    assert not list((tmp_path / "d2").iterdir())

    with pytest.raises(FirmwareError):
        fw_pkgs.download_zip("https://example.com/x.zip", tmp_path / "d3", sess)


# --- copy2card SFX -----------------------------------------------------------

def make_sfx_zip(tmp_path, root_case="Root", erase="true"):
    py7zr = pytest.importorskip("py7zr")
    src = tmp_path / "sfxsrc"
    (src / "root" / "sw").mkdir(parents=True)
    (src / "root" / "ldr_part_nmbr.txt").write_bytes(GTN_MARKER)
    (src / "root" / "sw" / "app.bin").write_bytes(b"app" * 100)
    (src / "root" / "SYS_KRNL.bin").write_bytes(b"krnl" * 50)
    (src / "LICENSE").write_text("lic")
    (src / "updateinfo.xml").write_text(
        '<?xml version="1.0"?><UpdateInfo xmlns="http://www.garmin.com/xmlschemas/Copy2Card/v2">'
        f"<RootDirPath>{root_case}</RootDirPath><EraseDestination>{erase}</EraseDestination></UpdateInfo>")
    arc = tmp_path / "payload.7z"
    with py7zr.SevenZipFile(arc, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    stub = (b"MZ" + b"\0" * 200 + fw_pkgs.SEVENZ_SIGNATURE + b"decoy-not-an-archive" * 5
            + b';!@Install@!UTF-8!\nRunProgram="Copy2Card.exe"\n;!@InstallEnd@!')
    zp = tmp_path / "gtn.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("006-B3191-17_0A.exe", stub + arc.read_bytes())
        z.writestr("26100a.pdf", b"%PDF-1.4 bulletin")
    return zp


def test_copy2card_package_from_synthetic_sfx(tmp_path):
    zp = make_sfx_zip(tmp_path)
    root = tmp_path / "pkg"
    root.mkdir()
    pkg = fw_pkgs.prepare_package(zp, GTN, release("gtn_xi", "21.12"), root)
    assert sorted(f.path for f in pkg.files) == ["SYS_KRNL.bin", "ldr_part_nmbr.txt", "sw/app.bin"]
    assert pkg.erase_destination is True
    assert pkg.bulletin == "26100a.pdf" and pkg.bulletin_path.read_bytes().startswith(b"%PDF")
    assert (pkg.image_dir / "ldr_part_nmbr.txt").read_bytes() == GTN_MARKER
    assert not (root / ".work").exists() and not list(root.glob("*.exe"))
    assert fw_pkgs.load_package(root).total_size == pkg.total_size
    meta = json.loads((root / "package.json").read_text())
    assert meta["erase_destination"] is True and all(len(f["sha256"]) == 64 for f in meta["files"])


def test_copy2card_erase_flag_false(tmp_path):
    zp = make_sfx_zip(tmp_path, root_case="ROOT", erase="false")
    root = tmp_path / "pkg"
    root.mkdir()
    assert fw_pkgs.prepare_package(zp, GTN, release("gtn_xi"), root).erase_destination is False


def test_no_7z_extractor_available(tmp_path, monkeypatch):
    zp = make_sfx_zip(tmp_path)
    monkeypatch.setitem(sys.modules, "py7zr", None)          # import py7zr -> ImportError
    monkeypatch.setattr(fw_pkgs.shutil, "which", lambda name: None)
    root = tmp_path / "pkg"
    root.mkdir()
    with pytest.raises(FirmwareError, match="py7zr.*7zip"):
        fw_pkgs.prepare_package(zp, GTN, release("gtn_xi"), root)


def test_system_binary_fallback_is_used(tmp_path, monkeypatch):
    zp = make_sfx_zip(tmp_path)
    import py7zr
    calls = []

    def fake_binary(binary, archive, target):
        calls.append(binary)
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(path=target)

    monkeypatch.setitem(sys.modules, "py7zr", None)
    monkeypatch.setattr(fw_pkgs, "_find_7z_binary", lambda: "/usr/bin/7zz")
    monkeypatch.setattr(fw_pkgs, "_extract_with_binary", fake_binary)
    root = tmp_path / "pkg"
    root.mkdir()
    pkg = fw_pkgs.prepare_package(zp, GTN, release("gtn_xi"), root)
    assert calls and len(pkg.files) == 3


# ---------------------------------------------------------------------------
# G3X card decisions
# ---------------------------------------------------------------------------

def plan(pkg, card, **kw):
    return fw_cards.plan_for_card(G3X, pkg, card, **kw)


def test_g3x_older_log_gets_copy_and_second_run_is_idempotent(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"),
                     extra_logs=[("log_20250101_000000_KXXX.csv", log_header("9.10"))])
    (card / "Garmin").mkdir()
    (card / "Garmin" / "GarminDevice.xml").write_text("<dev/>")
    (card / "feat_unlk.dat").write_bytes(b"f")

    p = plan(pkg, card)
    assert (p.action, p.installed_version) == ("install", "9.52")     # newest log by filename wins
    assert fw_cards.apply_plan(p, pkg) == 2
    assert (card / "Garmin" / "A.GCD").read_bytes() == b"a" * 100
    assert (card / "Garmin" / "GarminDevice.xml").read_text() == "<dev/>"
    assert (card / "feat_unlk.dat").read_bytes() == b"f"
    assert not list(card.rglob("*.part"))

    again = plan(pkg, card)
    assert again.action == "current" and "already on card" in again.reason and not again.writes


def test_g3x_same_or_newer_log_does_not_copy(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    for v in ("10.00", "10.10", "11.0"):
        card = make_card(tmp_path, name=f"c{v}", log=log_header(v))
        p = plan(pkg, card, remove_after_install=False)
        assert p.action == "current" and not p.copy


def test_g3x_remove_after_install_only_removes_our_files(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    old = make_g3x_pkg(tmp_path, "9.52", {"C.GCD": b"c" * 50}, name="zips_old")
    card = make_card(tmp_path, log=log_header("10.00"))
    g = card / "Garmin"
    g.mkdir()
    (g / "A.GCD").write_bytes(b"a" * 100)
    (g / "B.GCD").write_bytes(b"b" * 200)
    (g / "C.GCD").write_bytes(b"c" * 50)              # from an older cached package
    (g / "Other.GCD").write_bytes(b"o" * 100)         # not ours
    (g / "B2.GCD").write_bytes(b"b" * 201)            # same size as nothing we ship
    (g / "GarminDevice.xml").write_text("<dev/>")
    cached = fw_pkgs.list_cached(tmp_path / "data", "g3x_touch")
    assert {c.version for c in cached} == {"10.00", "9.52"}

    p = plan(pkg, card, remove_after_install=True, cached=cached)
    assert (p.action, sorted(p.remove)) == ("cleanup", ["Garmin/A.GCD", "Garmin/B.GCD", "Garmin/C.GCD"])
    fw_cards.apply_plan(p, pkg)
    assert sorted(x.name for x in g.iterdir()) == ["B2.GCD", "GarminDevice.xml", "Other.GCD"]
    assert plan(pkg, card, remove_after_install=True, cached=cached).action == "current"

    # name matches but size differs -> untouched
    (g / "A.GCD").write_bytes(b"x" * 7)
    assert plan(pkg, card, remove_after_install=True, cached=cached).action == "current"

    # option off -> nothing removed
    (g / "A.GCD").write_bytes(b"a" * 100)
    assert plan(pkg, card, remove_after_install=False, cached=cached).action == "current"


def test_g3x_without_logs_or_other_product_is_skipped(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    assert plan(pkg, make_card(tmp_path, name="nolog")).action == "skip"
    other = make_card(tmp_path, name="other", log=log_header("9.00", product="G1000"))
    p = plan(pkg, other)
    assert p.action == "skip" and "G1000" in p.reason
    garbage = make_card(tmp_path, name="garbage", log="not a header\n")
    assert plan(pkg, garbage).action == "skip"


def test_g3x_manual_copies_regardless_of_version(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("10.00"))
    p = plan(pkg, card, manual=True)
    assert p.action == "install" and p.installed_version == "10.00" and len(p.copy) == 2
    fw_cards.apply_plan(p, pkg)
    assert len(list((card / "Garmin").iterdir())) == 2


def test_g3x_manual_refuses_loader_card(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path)
    (card / "ldr_part_nmbr.txt").write_bytes(GTN_MARKER)
    with pytest.raises(FirmwareError, match="loader card"):
        plan(pkg, card, manual=True)


def test_write_checks_free_space_and_verifies(tmp_path, monkeypatch):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    p = plan(pkg, card)
    monkeypatch.setattr(fw_cards.shutil, "disk_usage", lambda p_: SimpleNamespace(free=1000))
    with pytest.raises(FirmwareError, match="Not enough free space"):
        fw_cards.apply_plan(p, pkg)
    assert not (card / "Garmin").exists()
    monkeypatch.undo()

    # corrupt cache is detected before it reaches the card's final name
    (pkg.image_dir / "Garmin" / "A.GCD").write_bytes(b"X" * 100)   # same size, wrong hash
    with pytest.raises(FirmwareError, match="checksum"):
        fw_cards.apply_plan(plan(pkg, card), pkg)
    assert not (card / "Garmin" / "A.GCD").exists() and not list(card.rglob("*.part"))
    assert fw_pkgs.load_package(pkg.root) is None                  # invalidated, will be re-downloaded


def test_dry_run_apply_writes_nothing(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    before = tree(card)
    assert fw_cards.apply_plan(plan(pkg, card), pkg, dry_run=True) == 0
    assert tree(card) == before and not (card / "Garmin").exists()


# ---------------------------------------------------------------------------
# GTN loader card decisions
# ---------------------------------------------------------------------------

def gplan(pkg, card, **kw):
    return fw_cards.plan_for_card(GTN, pkg, card, **kw)


def test_gtn_auto_ignores_database_blank_and_other_cards(tmp_path):
    pkg = make_gtn_pkg(tmp_path)

    blank = make_card(tmp_path, name="blank")
    assert gplan(pkg, blank).action == "skip"

    dbcard = make_card(tmp_path, name="db", log=log_header())
    (dbcard / "feat_unlk.dat").write_bytes(b"f")
    (dbcard / "ldr_sys").mkdir()
    (dbcard / "ldr_sys" / "avtn_db.bin").write_bytes(b"d")
    before = tree(dbcard)
    assert gplan(pkg, dbcard).action == "skip"

    # even a card that carries the loader marker is refused when it also holds database files
    mixed = make_card(tmp_path, name="mixed")
    (mixed / "ldr_part_nmbr.txt").write_bytes(GTN_MARKER.replace(b"0A", b"0Z"))
    (mixed / ".gadm.meta").write_text("{}")
    p = gplan(pkg, mixed)
    assert p.action == "skip" and ".gadm.meta" in p.reason

    other = make_card(tmp_path, name="other")
    (other / "ldr_part_nmbr.txt").write_bytes(b" -GNS 430-\r\nGPN 006-B0000-00\r\nRev 0A")
    assert gplan(pkg, other).action == "skip"
    assert tree(dbcard) == before


def test_gtn_auto_rewrites_outdated_loader_card(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="loader")
    (card / "ldr_part_nmbr.txt").write_bytes(b" -GTN Xi-\r\nGPN 006-B3191-16\r\nRev 0A")
    (card / "old_only.bin").write_bytes(b"old")
    (card / "sw").mkdir()
    (card / "sw" / "app.bin").write_bytes(b"older")
    (card / "olddir").mkdir()
    (card / "olddir" / "x.bin").write_bytes(b"x")

    p = gplan(pkg, card)
    assert p.action == "install" and p.erase
    assert fw_cards.apply_plan(p, pkg) == 4

    expected = {f.path: (pkg.image_dir / f.path).read_bytes() for f in pkg.files}
    assert tree(card) == expected                      # old files gone, new tree present
    assert not (card / "olddir").exists()
    assert gplan(pkg, card).action == "current"


def test_gtn_auto_current_card_untouched_and_missing_file_rewrites(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="loader")
    for f in pkg.files:
        (card / f.path).parent.mkdir(parents=True, exist_ok=True)
        (card / f.path).write_bytes((pkg.image_dir / f.path).read_bytes())
    p = gplan(pkg, card)
    assert p.action == "current" and not p.writes
    (card / "sw" / "app.bin").unlink()
    assert gplan(pkg, card).action == "install"
    (card / "sw").mkdir(exist_ok=True)
    (card / "sw" / "app.bin").write_bytes(b"wrongsize")
    assert gplan(pkg, card).action == "install"


def test_gtn_auto_does_not_downgrade(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="newer")
    (card / "ldr_part_nmbr.txt").write_bytes(b" -GTN Xi-\r\nGPN 006-B3191-18\r\nRev 0A")
    assert gplan(pkg, card).action == "skip"


def test_gtn_loader_marker_written_last(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    order = [f.path for f in fw_cards._write_order(pkg.files)]
    assert order[-1] == "ldr_part_nmbr.txt"


def test_gtn_manual_refuses_database_card_without_erase(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="db", log=log_header())
    (card / "feat_unlk.dat").write_bytes(b"f")
    before = tree(card)
    with pytest.raises(FirmwareError, match="--erase"):
        gplan(pkg, card, manual=True)
    assert tree(card) == before

    p = gplan(pkg, card, manual=True, erase_ok=True)
    assert p.action == "install" and p.erase
    fw_cards.apply_plan(p, pkg)
    assert "feat_unlk.dat" not in tree(card) and "data_log/log_20260301_120000_KXXX.csv" not in tree(card)
    assert (card / "ldr_part_nmbr.txt").read_bytes() == GTN_MARKER


def test_gtn_manual_blank_card_ok_and_random_card_needs_erase(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    blank = make_card(tmp_path, name="blank")
    (blank / "System Volume Information").mkdir()
    assert gplan(pkg, blank, manual=True).action == "install"
    random_card = make_card(tmp_path, name="photos")
    (random_card / "holiday.jpg").write_bytes(b"jpg")
    with pytest.raises(FirmwareError):
        gplan(pkg, random_card, manual=True)


def test_gtn_erase_space_counts_freed_space(tmp_path, monkeypatch):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="loader")
    (card / "ldr_part_nmbr.txt").write_bytes(b" -GTN Xi-\r\nGPN 006-B3191-16\r\nRev 0A")
    (card / "big.bin").write_bytes(b"b" * 5_000_000)
    monkeypatch.setattr(fw_cards.shutil, "disk_usage", lambda p_: SimpleNamespace(free=1000))
    # 1000 free + ~5 MB freed by erasing is enough for the small image
    assert fw_cards.apply_plan(gplan(pkg, card), pkg) == 4


# ---------------------------------------------------------------------------
# CLI: firmware install
# ---------------------------------------------------------------------------

def _cli_cfg(tmp_path, extra=None):
    cfg = {"system": {"data_dir": str(tmp_path / "data"), "log_file": str(tmp_path / "log.txt")}}
    cfg.update(extra or {})
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def test_cli_install_gtn_refuses_database_card_then_erases(tmp_path):
    pkg = make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, name="db", log=log_header())
    (card / "feat_unlk.dat").write_bytes(b"f")
    cfg = _cli_cfg(tmp_path)
    with mock.patch("avcardtool.firmware.catalog.fetch_release", return_value=release("gtn_xi", "21.12")), \
            mock.patch("avcardtool.firmware.packages.ensure_package", return_value=pkg):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "firmware", "install", "gtn_xi", str(card), "--yes"])
        assert r.exit_code != 0 and "--erase" in r.output
        assert (card / "feat_unlk.dat").exists()

        r = CliRunner().invoke(cli, ["--config", str(cfg), "firmware", "install", "gtn_xi", str(card),
                                     "--erase"], input="n\n")
        assert r.exit_code != 0 and (card / "feat_unlk.dat").exists()      # declined at the prompt

        r = CliRunner().invoke(cli, ["--config", str(cfg), "firmware", "install", "gtn_xi", str(card),
                                     "--erase", "--yes"])
        assert r.exit_code == 0, r.output
        assert (card / "ldr_part_nmbr.txt").exists() and not (card / "feat_unlk.dat").exists()
        assert "not the database card" in r.output


def test_cli_install_dry_run_writes_nothing(tmp_path):
    pkg = make_g3x_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    cfg = _cli_cfg(tmp_path)
    before = tree(card)
    with mock.patch("avcardtool.firmware.catalog.fetch_release", return_value=release()), \
            mock.patch("avcardtool.firmware.packages.ensure_package") as ensure:
        r = CliRunner().invoke(cli, ["--config", str(cfg), "--dry-run", "firmware", "install", "g3x_touch",
                                     str(card), "--yes"])
    assert r.exit_code == 0, r.output
    assert "DRY RUN" in r.output and tree(card) == before
    ensure.assert_not_called()


# ---------------------------------------------------------------------------
# auto run
# ---------------------------------------------------------------------------

class FakeDetector:
    cards = []
    unmounted = []

    def scan_for_cards(self):
        return list(type(self).cards)

    def unmount_card(self, mount_point):
        type(self).unmounted.append(mount_point)


def sd(dev, mount, volume="ABCD-1234"):
    return SDCardInfo(device_path=dev, mount_point=str(mount) if mount else None, label="", filesystem="vfat",
                      size_bytes=8 << 30, free_bytes=4 << 30, volume_id=volume, is_garmin=False)


@pytest.fixture
def fake_env(monkeypatch):
    FakeDetector.cards, FakeDetector.unmounted = [], []
    monkeypatch.setattr(fw_auto, "SDCardDetector", FakeDetector)
    monkeypatch.setattr(fw_auto, "fetch_release",
                        lambda product, url=None: release(product.key, "10.00" if product.key == "g3x_touch" else "21.12"))
    return FakeDetector


def test_auto_updates_g3x_card_and_notifies(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sda1", card)]
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    mgr = FakeManager()
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), mgr)
    assert issues == []
    assert (card / "Garmin" / "A.GCD").exists()
    types = [e.event_type for e in mgr.events]
    assert types.count("firmware_available") == 2 and types.count("firmware_card_written") == 1
    written = next(e for e in mgr.events if e.event_type == "firmware_card_written")
    assert "confirm" in written.body and "ABCD-1234" in written.body
    assert fake_env.unmounted == []                    # was mounted rw by the system: left alone


def test_auto_unmounted_card_is_inspected_ro_remounted_rw_and_unmounted(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sdb1", None)]
    calls = []

    def resolve(dev, readonly=True):
        calls.append(readonly)
        return card

    monkeypatch.setattr(fw_auto, "resolve_device_mount_point", resolve)
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: True)
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), FakeManager())
    assert issues == [] and calls == [True, False]
    assert (card / "Garmin" / "B.GCD").exists()
    assert fake_env.unmounted == [str(card)]


def test_auto_nothing_to_write_does_not_remount_rw(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("10.00"))
    fake_env.cards = [sd("/dev/sdb1", None)]
    calls = []
    monkeypatch.setattr(fw_auto, "resolve_device_mount_point",
                        lambda dev, readonly=True: calls.append(readonly) or card)
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: True)
    fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), None)
    assert calls == [True]
    assert fake_env.unmounted == [str(card)]


def test_auto_device_argument_only_considers_that_device(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    a = make_card(tmp_path, name="a", log=log_header("9.52"))
    b = make_card(tmp_path, name="b", log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sda1", a), sd("/dev/sdb1", b)]
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    fw_auto.run_firmware_auto_update(cfg_for(tmp_path), Path("/dev/sdb1"), mock.Mock(), None)
    assert (b / "Garmin").exists() and not (a / "Garmin").exists()


def test_auto_never_touches_database_card(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("10.00"))
    (card / "feat_unlk.dat").write_bytes(b"f")
    (card / "ldr_sys").mkdir()
    (card / "ldr_sys" / "avtn_db.bin").write_bytes(b"d")
    fake_env.cards = [sd("/dev/sda1", card)]
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    before = tree(card)
    fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), FakeManager())
    assert tree(card) == before


def test_auto_auto_install_off_never_scans_cards(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    monkeypatch.setattr(fake_env, "scan_for_cards", mock.Mock(side_effect=AssertionError("must not scan")))
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path, auto_install=False), None, mock.Mock(),
                                              FakeManager())
    assert issues == []


def test_auto_disabled_product_and_disabled_feature(tmp_path, fake_env):
    fetched = []
    with mock.patch.object(fw_auto, "fetch_release", side_effect=lambda p, u=None: fetched.append(p.key) or release(p.key)):
        fw_auto.run_firmware_auto_update(cfg_for(tmp_path, enabled=False), None, mock.Mock(), None)
        assert fetched == []
        fw_auto.run_firmware_auto_update(
            cfg_for(tmp_path, auto_install=False, products={"gtn_xi": {"enabled": False}}),
            None, mock.Mock(), None)
    assert fetched == ["g3x_touch"]


def test_auto_failure_is_reported_once_and_never_raises(tmp_path, fake_env, monkeypatch):
    pkg = make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sda1", card)]
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    (pkg.image_dir / "Garmin" / "A.GCD").write_bytes(b"X" * 100)       # corrupt cache
    mgr = FakeManager()
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), mgr)
    assert any(sev == "ERROR" for sev, _ in issues)
    assert [e.event_type for e in mgr.events].count("firmware_update_failed") == 1

    with mock.patch.object(fw_auto, "_run", side_effect=RuntimeError("boom")):
        issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), mgr)
    assert issues and "boom" in issues[0][1]


def test_auto_network_failure_is_a_warning_and_cache_is_still_used(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sda1", card)]
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly", lambda d: False)
    monkeypatch.setattr(fw_auto, "fetch_release", mock.Mock(side_effect=FirmwareNetworkError("offline")))
    mgr = FakeManager()
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path), None, mock.Mock(), mgr)
    assert issues and all(sev == "WARNING" for sev, _ in issues)
    assert not any(e.event_type == "firmware_update_failed" for e in mgr.events)
    assert (card / "Garmin" / "A.GCD").exists()


def test_auto_dry_run_writes_nothing(tmp_path, fake_env, monkeypatch):
    make_g3x_pkg(tmp_path)
    make_gtn_pkg(tmp_path)
    card = make_card(tmp_path, log=log_header("9.52"))
    fake_env.cards = [sd("/dev/sdb1", None)]
    monkeypatch.setattr(fw_auto, "resolve_device_mount_point", lambda dev, readonly=True: card)
    monkeypatch.setattr(fw_auto, "_is_mounted_readonly",
                        lambda d: (_ for _ in ()).throw(AssertionError("no rw remount in dry run")))
    ensure = mock.Mock(side_effect=AssertionError("no download in dry run"))
    monkeypatch.setattr(fw_auto, "ensure_package", ensure)
    # a version that is not cached yet: dry run must only report it
    monkeypatch.setattr(fw_auto, "fetch_release", lambda p, u=None: release(p.key, "99.00"))
    mgr = FakeManager()
    snapshot = tree(tmp_path)
    issues = fw_auto.run_firmware_auto_update(cfg_for(tmp_path, dry_run=True), None, mock.Mock(), mgr)
    assert issues == [] and mgr.events == []
    assert tree(tmp_path) == snapshot                       # card, cache and state untouched
    assert not (tmp_path / "data" / "firmware" / "state.json").exists()


# ---------------------------------------------------------------------------
# navdata auto-update runs the firmware step on every exit path
# ---------------------------------------------------------------------------

def test_navdata_auto_update_runs_firmware_step_on_early_exit(tmp_path):
    cfg = _cli_cfg(tmp_path)
    with mock.patch("avcardtool.navdata.garmin.auth.GarminAuth.ensure_authenticated", return_value=False), \
            mock.patch("avcardtool.cli._run_firmware_step") as step:
        r = CliRunner().invoke(cli, ["--config", str(cfg), "navdata", "auto-update", "/dev/sdz1"])
    assert r.exit_code == 1                                  # navdata's own exit code is preserved
    step.assert_called_once()


def test_navdata_auto_update_runs_firmware_step_on_normal_return(tmp_path):
    from avcardtool import cli as cli_mod
    with mock.patch.object(cli_mod, "_navdata_auto_update_impl") as impl, \
            mock.patch.object(cli_mod, "_run_firmware_step") as step:
        r = CliRunner().invoke(cli, ["--config", str(_cli_cfg(tmp_path)), "navdata", "auto-update"])
    assert r.exit_code == 0
    impl.assert_called_once()
    step.assert_called_once()


def test_run_firmware_step_swallows_everything(tmp_path):
    from avcardtool import cli as cli_mod
    ctx = SimpleNamespace(obj={"config": cfg_for(tmp_path)})
    with mock.patch("avcardtool.firmware.auto.run_firmware_auto_update", side_effect=RuntimeError("boom")):
        cli_mod._run_firmware_step(ctx, None)             # must not raise
    with mock.patch("avcardtool.firmware.auto.run_firmware_auto_update") as run:
        cli_mod._run_firmware_step(SimpleNamespace(obj={"config": cfg_for(tmp_path, enabled=False)}), None)
        run.assert_not_called()


# ---------------------------------------------------------------------------
# config and notifications
# ---------------------------------------------------------------------------

def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config.generate_default(path)
    assert cfg.firmware.enabled and cfg.firmware.auto_install and cfg.firmware.remove_after_install
    cfg.firmware.auto_install = False
    cfg.firmware.remove_after_install = False
    cfg.firmware.products = {"gtn_xi": {"enabled": False},
                             "g3x_touch": {"enabled": True, "page_url": "https://www8.garmin.com/other"}}
    cfg.save(path)

    loaded = Config(config_path=path)
    assert loaded.firmware.auto_install is False and loaded.firmware.remove_after_install is False
    assert loaded.firmware.product_enabled("gtn_xi") is False
    assert loaded.firmware.product_page_url("g3x_touch") == "https://www8.garmin.com/other"
    assert loaded.firmware.product_page_url("gtn_xi") is None
    assert loaded.to_dict()["firmware"] == cfg.to_dict()["firmware"]
    for event in ("firmware_available", "firmware_card_written", "firmware_update_failed"):
        assert loaded.notifications.events[event] is True


def test_config_without_firmware_section_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"system": {"data_dir": str(tmp_path)}}))
    cfg = Config(config_path=path)
    assert cfg.firmware.enabled and cfg.firmware.auto_install and cfg.firmware.products == {}


def test_config_validation_of_firmware_section(tmp_path):
    cfg = Config(config_path=None)
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.firmware.products = {"nope": {}}
    with pytest.raises(ValueError, match="Unknown firmware product"):
        cfg.validate()
    cfg.firmware.products = {"gtn_xi": {"page_url": "http://insecure.example"}}
    with pytest.raises(ValueError, match="https"):
        cfg.validate()
    cfg.firmware.products = {"gtn_xi": {"enabled": True}}
    assert cfg.validate()


def test_notification_event_builders():
    e = ev.build_firmware_available_event("G3X Touch", "10.00", "October 20, 2026",
                                          [f"note {i}" for i in range(20)], "https://p", "/cache/x")
    assert e.event_type == "firmware_available" and e.title == "G3X Touch software 10.00 available"
    assert "note 14" in e.body and "note 15" not in e.body and "https://p" in e.body and "/cache/x" in e.body
    assert "non-certified" not in e.body

    g = ev.build_firmware_available_event("GTN Xi", "21.12", "July 17, 2026", ["n"], "https://p", "/c",
                                          bulletin_path="/c/26100a.pdf")
    assert "/c/26100a.pdf" in g.body and "non-certified" in g.body

    w = ev.build_firmware_card_written_event("G3X Touch", "10.00", "/media/x (volume 1)", 20, ["do this"], "9.52")
    assert w.event_type == "firmware_card_written" and "do this" in w.body and "9.52" in w.body

    f = ev.build_firmware_update_failed_event([("ERROR", "bad"), ("WARNING", "meh")])
    assert f.event_type == "firmware_update_failed" and f.severity.value.lower() == "error"
    assert "bad" in f.body and "meh" in f.body
    assert NotificationsConfig().events["firmware_update_failed"] is True
