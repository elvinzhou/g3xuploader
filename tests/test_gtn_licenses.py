"""GTN 6XX Xi navigation data is installed the way Garmin's GADM does it:
raw avtn_db.bin + nav_db2.bin, signed .sff licenses and grm_feat_key.zip from the
unlock response, and feat_unlk.dat slots 0 and 14."""

import json
from unittest import mock

import pytest
from click.testing import CliRunner

from avcardtool.cli import (
    _licensed_region_paths, _unlock_filenames, _unlock_for_install, cli,
)
from avcardtool.navdata.garmin.api import FlyGarminAPI
from avcardtool.navdata.garmin.auth import GarminAPIError
from avcardtool.navdata.garmin.feat_unlk import (
    FILENAME_TO_FEATURE, SLOT_SIZE, Feature, update_feat_unlk,
)
from test_taw_parser import build_taw

GTN = 0x0402
G600 = 0x01F2


def test_unlock_slots_match_our_feat_unlk_offsets():
    """unlockData's featureUnlockSlot * 913 is where we write: 0 = nav, 2 = terrain, 14 = nav_db2."""
    assert Feature.NAVIGATION.offset == 0 * SLOT_SIZE
    assert Feature.TERRAIN.offset == 2 * SLOT_SIZE
    assert Feature.NAV_DB2.offset == 14 * SLOT_SIZE


def test_nav_db2_slot_is_slot_14():
    assert Feature.NAV_DB2.offset == 14 * SLOT_SIZE == 12782
    assert FILENAME_TO_FEATURE["ldr_sys/nav_db2.bin"] is Feature.NAV_DB2
    # the other slots are unchanged
    assert Feature.NAVIGATION.offset == 0 and Feature.SECTIONALS.offset == 10956


def test_nav_db2_slot_written_with_file_crc_and_no_preview(tmp_path):
    update_feat_unlk(tmp_path, Feature.NAV_DB2, 0x12345678, GTN, 0x0BADF00D, 0x626F807E)
    data = (tmp_path / "feat_unlk.dat").read_bytes()
    assert len(data) == 12782 + SLOT_SIZE            # a GADM GTN card's feat_unlk.dat is 13,695 bytes
    slot = data[12782:]
    assert int.from_bytes(slot[2:4], "little") + 191 == GTN
    assert slot[8:12] == (1 << 10).to_bytes(4, "little")    # feature flag 0x0400
    assert slot[20:24] == (0x626F807E).to_bytes(4, "little")
    assert not any(slot[24:81])                      # no MAGIC3 / preview bytes


# --- unlock file download ---------------------------------------------------

def _api(responses):
    api = FlyGarminAPI(mock.Mock())
    api._session = mock.Mock()
    api._auth_headers = lambda: {"Authorization": "Bearer t"}
    seen = []

    def get(url, headers=None, timeout=None, **kw):
        seen.append((url, headers))
        r = mock.Mock()
        r.content = responses[url]
        r.raise_for_status = lambda: None
        return r

    api._session.get.side_effect = get
    return api, seen


def _unlock(*files):
    return {"files": [{"url": u, "destination": d, "sendAuthorization": True, "hidden": False}
                      for u, d in files]}


def test_download_unlock_files_saves_destinations_and_sends_token(tmp_path):
    url = "https://avdb.garmin.com/lic/a"
    api, seen = _api({url: b"sff", "https://avdb.garmin.com/lic/b": b"zip"})
    out = api.download_unlock_files(
        _unlock((url, "avtn_db.bin.sff"),
                ("https://avdb.garmin.com/lic/b", "ldr_sys/grm_feat_key.zip")),
        tmp_path)
    assert [(p.relative_to(tmp_path).as_posix(), d) for p, d in out] == [
        ("avtn_db.bin.sff", "avtn_db.bin.sff"),
        ("ldr_sys/grm_feat_key.zip", "ldr_sys/grm_feat_key.zip")]
    assert (tmp_path / "ldr_sys/grm_feat_key.zip").read_bytes() == b"zip"
    assert seen[0][1] == {"Authorization": "Bearer t"}


@pytest.mark.parametrize("url", ["https://evil.example.com/x", "http://avdb.garmin.com/x",
                                 "https://garmin.com.evil.net/x"])
def test_token_is_never_sent_to_non_garmin_hosts(tmp_path, url):
    api, seen = _api({url: b"x"})
    with pytest.raises(GarminAPIError):
        api.download_unlock_files(_unlock((url, "a.sff")), tmp_path)
    assert seen == []


def test_unsafe_destination_is_rejected(tmp_path):
    api, _ = _api({"https://avdb.garmin.com/x": b"x"})
    with pytest.raises(GarminAPIError):
        api.download_unlock_files(_unlock(("https://avdb.garmin.com/x", "../evil.sff")), tmp_path)


# What Garmin's unlock endpoint returned for a G3X Touch (any database) and for a
# GTN 6XX Xi's navigation data / terrain, captured 2026-11 (URLs and codes omitted).
G3X_UNLOCK = {"files": [], "unlockCodes": [{"deviceID": 5, "unlockCode": "x"}], "unlockData": []}
GTN_NAV_UNLOCK = {
    "files": [{"destination": "avtn_db.bin.sff"}, {"destination": "nav_db2.bin.sff"},
              {"destination": "ldr_sys/grm_feat_key.zip"}],
    "unlockCodes": [{"deviceID": 5}],
    "unlockData": [
        {"avdbTypeVersion": 1, "featureUnlockSlot": 0, "featureUnlockFlag": 0,
         "featureUnlockFilename": "ldr_sys/avtn_db.bin"},
        {"avdbTypeVersion": 2, "featureUnlockSlot": 14, "featureUnlockFlag": 0,
         "featureUnlockFilename": "ldr_sys/nav_db2.bin"}],
}
GTN_TERRAIN_UNLOCK = {
    "files": [{"destination": "trn.dat.sff"}, {"destination": "ldr_sys/grm_feat_key.zip"}],
    "unlockCodes": [{"deviceID": 5}],
    "unlockData": [{"avdbTypeVersion": 2, "featureUnlockSlot": 2, "featureUnlockFlag": 1,
                    "featureUnlockFilename": "trn.dat"}],
}


def test_unlock_without_license_files_changes_nothing(tmp_path):
    """A G3X unlock (no files, no unlockData) gives no licenses and no overrides."""
    api = mock.Mock()
    api.unlock.return_value = G3X_UNLOCK
    api.download_unlock_files.return_value = []
    entries, unlock_files, codes = _unlock_for_install(api, 1, "2611", 5, tmp_path / "lic", tmp_path)
    assert entries == [] and unlock_files == {} and codes == G3X_UNLOCK["unlockCodes"]


def test_unlock_with_licenses_returns_card_relative_entries(tmp_path):
    api = mock.Mock()
    lic = tmp_path / "lic"
    api.unlock.return_value = GTN_NAV_UNLOCK
    api.download_unlock_files.return_value = [(lic / "avtn_db.bin.sff", "avtn_db.bin.sff")]
    entries, unlock_files, _ = _unlock_for_install(api, 1, "2611", 5, lic, tmp_path)
    assert entries == [{"cache_path": "lic/avtn_db.bin.sff", "destination": "avtn_db.bin.sff",
                        "is_license": True,
                        "unlock_slots": {"ldr_sys/avtn_db.bin": 0, "ldr_sys/nav_db2.bin": 14}}]
    assert unlock_files == {1: "ldr_sys/avtn_db.bin", 2: "ldr_sys/nav_db2.bin"}


def test_unlock_filenames_reject_unsafe_entries():
    assert _unlock_filenames({"unlockData": [
        {"avdbTypeVersion": 2, "featureUnlockFilename": "../evil"},
        {"avdbTypeVersion": "x", "featureUnlockFilename": "a"}, {}]}) == {}


def test_nav_region_2_is_nav_db2_but_terrain_needs_no_override(tmp_path):
    nav = tmp_path / "jg600a.taw"
    nav.write_bytes(build_taw(db_type=G600, regions=[(0x01, b"a"), (0x02, b"b")]))
    assert _licensed_region_paths(nav, {1: "ldr_sys/avtn_db.bin", 2: "ldr_sys/nav_db2.bin"}) == {
        "2": "ldr_sys/nav_db2.bin"}
    terrain = tmp_path / "tgtn.taw"
    terrain.write_bytes(build_taw(db_type=GTN, regions=[(0x23, b"t")]))   # 0x23 -> trn.dat
    assert _licensed_region_paths(terrain, {2: "trn.dat"}) == {}
    assert _licensed_region_paths(nav, {}) == {}                         # G3X: nothing to do


# --- install ----------------------------------------------------------------

def _entry(local, avdb, db_type, destination=None, **extra):
    return {"local_path": local, "destination": destination, "avdb_type": avdb, "avdb_type_id": 1,
            "series_id": 2230, "issue_name": "2611", "device_id": 7, "removable_paths": [],
            "unlock_codes": [], "taw_database_type": db_type, **extra}


def _config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"),
                                          "log_file": str(tmp_path / "log.txt")}}))
    return cfg


def test_install_gtn_navigation_gadm_scheme(tmp_path):
    dl = tmp_path / "download"
    (dl / "licenses/ldr_sys").mkdir(parents=True)
    nav = b"nav-db-1" * 10
    nav2 = b"second-db" * 10
    # jg600a-style TAW: header G600, regions 0x01 (avtn_db.bin) and 0x02 (nav_db2.bin)
    (dl / "jg600a-us-2611.taw").write_bytes(
        build_taw(db_type=G600, regions=[(0x01, nav), (0x02, nav2)]))
    (dl / "licenses/avtn_db.bin.sff").write_bytes(b"sff-1")
    (dl / "licenses/nav_db2.bin.sff").write_bytes(b"sff-2")
    (dl / "licenses/ldr_sys/grm_feat_key.zip").write_bytes(b"key")
    (dl / "navdata_manifest.json").write_text(json.dumps({
        "card_serial": "1234-ABCD", "system_ids": [1234567890123],
        "device_type_map": {"GTN 6XX Xi": GTN},
        "entries": [
            _entry("jg600a-us-2611.taw", "Navigation Data", GTN,
                   region_paths={"2": "ldr_sys/nav_db2.bin"}),
            _entry("licenses/avtn_db.bin.sff", "Navigation Data", None,
                   "avtn_db.bin.sff", is_license=True),
            _entry("licenses/nav_db2.bin.sff", "Navigation Data", None,
                   "nav_db2.bin.sff", is_license=True),
            _entry("licenses/ldr_sys/grm_feat_key.zip", "Navigation Data", None,
                   "ldr_sys/grm_feat_key.zip", is_license=True),
        ],
    }))
    card = tmp_path / "card"
    card.mkdir()
    (card / "avionics.txt").write_text("GTN 6XX Xi")

    result = CliRunner().invoke(cli, ["--config", str(_config(tmp_path)), "navdata", "install",
                                      str(card), "--from", str(dl), "-y"])
    assert result.exit_code == 0, result.output

    assert (card / "ldr_sys/avtn_db.bin").read_bytes() == nav
    assert (card / "ldr_sys/nav_db2.bin").read_bytes() == nav2
    assert not (card / "basemap.bin").exists()
    assert (card / "avtn_db.bin.sff").read_bytes() == b"sff-1"
    assert (card / "nav_db2.bin.sff").read_bytes() == b"sff-2"
    assert (card / "ldr_sys/grm_feat_key.zip").read_bytes() == b"key"

    feat = (card / "feat_unlk.dat").read_bytes()
    assert len(feat) == 13695                          # slots 0 .. 14, as on a GADM card
    assert int.from_bytes(feat[2:4], "little") + 191 == GTN
    assert int.from_bytes(feat[12782 + 2:12782 + 4], "little") + 191 == GTN
    assert feat[12782 + 8:12782 + 12] == (1 << 10).to_bytes(4, "little")
    assert json.loads((card / ".gadm.meta").read_text())["gadmAvionicsName"] == "GTN 6XX Xi"


def test_install_refuses_license_outside_the_card(tmp_path):
    dl = tmp_path / "download"
    dl.mkdir()
    (dl / "x.sff").write_bytes(b"x")
    (dl / "t.taw").write_bytes(build_taw(db_type=GTN, regions=[(0x23, b"t")]))
    (dl / "navdata_manifest.json").write_text(json.dumps({
        "card_serial": "0", "device_type_map": {"GTN 6XX Xi": GTN},
        "entries": [_entry("t.taw", "Terrain", GTN),
                    _entry("x.sff", "Terrain", None, "../escaped.sff", is_license=True)],
    }))
    card = tmp_path / "card"
    card.mkdir()
    (card / "avionics.txt").write_text("GTN 6XX Xi")
    CliRunner().invoke(cli, ["--config", str(_config(tmp_path)), "navdata", "install",
                             str(card), "--from", str(dl), "-y"])
    assert not (tmp_path / "escaped.sff").exists()


# --- FAT attributes match GADM ------------------------------------------------

def test_attributes_match_gadm(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from avcardtool.navdata.garmin import taw_parser

    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd)
                        or SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(taw_parser, "_NON_FAT_DEVICES", set())
    f = tmp_path / "f"
    f.write_bytes(b"x")
    taw_parser._set_garmin_data_attrs(f)
    taw_parser._set_garmin_meta_attrs(f)
    taw_parser._set_plain(f)
    assert [c[1:-1] for c in calls] == [["+h", "+s", "+r"], ["+h", "+r"], ["-h", "-s", "-r"]]


# --- cache hits and unseen-avionics checks -------------------------------------

from types import SimpleNamespace  # noqa: E402

from avcardtool.cli import _check_unlock_slots, _manifest_entries_from_cache, _unlock_slots  # noqa: E402


def _cached(tmp_path, header, regions):
    cache = tmp_path / "cache"
    (cache / "2230/2611").mkdir(parents=True)
    (cache / "2230/2611/x.taw").write_bytes(build_taw(db_type=header, regions=regions))
    state = {"files": [{"cache_path": "2230/2611/x.taw", "destination": None,
                        "taw_database_type": GTN}], "removable_paths": ["/old"]}
    return cache, tmp_path / "card_dir", state


def _call(api, cache, card_dir, state):
    avdb = SimpleNamespace(name="Navigation Data", type_id=1)
    series = SimpleNamespace(series_id=2230)
    issue = SimpleNamespace(name="2611")
    return _manifest_entries_from_cache(api, cache, card_dir, state, avdb, series, issue, 7, GTN, [])


def test_cache_hit_refetches_licenses_and_region_paths(tmp_path):
    """Even a cache written before licenses were known gets them for this unit."""
    cache, card_dir, state = _cached(tmp_path, G600, [(0x01, b"a"), (0x02, b"b")])
    api = mock.Mock()
    api.unlock.return_value = GTN_NAV_UNLOCK
    lic = card_dir / "2230" / "2611" / "licenses"
    api.download_unlock_files.return_value = [(lic / "avtn_db.bin.sff", "avtn_db.bin.sff")]
    entries, err = _call(api, cache, card_dir, state)
    assert err is None
    taw, license_ = entries
    assert taw["region_paths"] == {"2": "ldr_sys/nav_db2.bin"} and taw["removable_paths"] == ["/old"]
    assert license_["is_license"] and license_["unlock_slots"] == {
        "ldr_sys/avtn_db.bin": 0, "ldr_sys/nav_db2.bin": 14}


def test_cache_hit_foreign_family_without_licenses_is_an_error(tmp_path):
    cache, card_dir, state = _cached(tmp_path, G600, [(0x01, b"a")])
    api = mock.Mock()
    api.unlock.side_effect = RuntimeError("403")
    entries, err = _call(api, cache, card_dir, state)
    assert entries == [] and "needs license files" in err


def test_cache_hit_g3x_style_has_no_licenses_and_no_overrides(tmp_path):
    cache, card_dir, state = _cached(tmp_path, GTN, [(0x23, b"t")])
    api = mock.Mock()
    api.unlock.return_value = G3X_UNLOCK
    api.download_unlock_files.return_value = []
    entries, err = _call(api, cache, card_dir, state)
    assert err is None and len(entries) == 1 and "region_paths" not in entries[0]


def test_unlock_slots_parse():
    assert _unlock_slots(GTN_NAV_UNLOCK) == {"ldr_sys/avtn_db.bin": 0, "ldr_sys/nav_db2.bin": 14}
    assert _unlock_slots(G3X_UNLOCK) == {}


def test_unseen_database_file_is_warned_about(tmp_path):
    (tmp_path / "ldr_sys").mkdir()
    for f in ("ldr_sys/avtn_db.bin", "ldr_sys/nav_db2.bin", "ldr_sys/new_db.bin"):
        (tmp_path / f).write_bytes(b"x")
    entries = [{"unlock_slots": {"ldr_sys/avtn_db.bin": 0, "ldr_sys/nav_db2.bin": 14,
                                 "ldr_sys/new_db.bin": 20, "ldr_sys/absent.bin": 3}}]
    warnings = _check_unlock_slots(entries, tmp_path)
    assert len(warnings) == 1 and "new_db.bin" in warnings[0] and "slot 20" in warnings[0]
    # a known file under a different slot than we write is flagged too
    (tmp_path / "ldr_sys/nav_db2.bin").write_bytes(b"x")
    warnings = _check_unlock_slots([{"unlock_slots": {"ldr_sys/nav_db2.bin": 9}}], tmp_path)
    assert len(warnings) == 1 and "slot 9" in warnings[0]
