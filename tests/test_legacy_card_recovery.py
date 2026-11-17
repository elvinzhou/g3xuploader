"""A card written by an older version of this tool (GTN nav slot signed with the
TAW header's family code, terrain slot without a System ID, nav_db2 under the
wrong name) is recognised and its databases are installed again."""

import json

from avcardtool.cli import (
    _drop_missigned_cycles, _feat_unlk_missigned, _misnamed_copies,
    _read_feat_unlk_crcs, _resolve_target_db_type,
)
from avcardtool.navdata.garmin.feat_unlk import Feature, update_feat_unlk

GTN, G600, G3X = 0x0402, 0x01F2, 0x06BF
VOL, SYSID = 0x12345678, 0x60002CA61BD97
MANIFEST = {"device_type_map": {"G3X Touch": G3X, "GTN 6XX Xi": GTN}, "entries": []}


def _legacy_card(card):
    update_feat_unlk(card, Feature.NAVIGATION, VOL, G600, SYSID, 0x1111)
    update_feat_unlk(card, Feature.TERRAIN, VOL, GTN, 0, 0x2222)
    update_feat_unlk(card, Feature.NAV_DB2, VOL, GTN, SYSID, 0x3333)


def test_card_type_recovered_from_gadm_meta(tmp_path):
    _legacy_card(tmp_path)
    (tmp_path / ".gadm.meta").write_text(json.dumps({"gadmAvionicsName": "GTN 6XX Xi"}))
    assert _resolve_target_db_type(tmp_path, MANIFEST) == GTN


def test_card_type_recovered_from_other_slots(tmp_path):
    _legacy_card(tmp_path)
    assert _resolve_target_db_type(tmp_path, MANIFEST) == GTN


def test_known_nav_slot_still_wins(tmp_path):
    update_feat_unlk(tmp_path, Feature.NAVIGATION, VOL, G3X, SYSID, 0x1111)
    (tmp_path / ".gadm.meta").write_text(json.dumps({"gadmAvionicsName": "GTN 6XX Xi"}))
    assert _resolve_target_db_type(tmp_path, MANIFEST) == G3X


def test_unknown_nav_slot_without_other_evidence_is_returned_as_before(tmp_path):
    update_feat_unlk(tmp_path, Feature.NAVIGATION, VOL, G600, SYSID, 0x1111)
    (tmp_path / ".gadm.meta").write_text("not json")
    assert _resolve_target_db_type(tmp_path, MANIFEST) == G600


def test_missigned_slots_are_not_counted_as_installed(tmp_path):
    _legacy_card(tmp_path)
    bad = _feat_unlk_missigned(tmp_path, GTN, SYSID)
    assert set(bad) == {"NAVIGATION", "TERRAIN"}
    assert "0x01F2" in bad["NAVIGATION"] and "without a System ID" in bad["TERRAIN"]
    assert _read_feat_unlk_crcs(tmp_path, GTN, SYSID) == {"NAV_DB2": 0x3333}
    assert set(_read_feat_unlk_crcs(tmp_path)) == {"NAVIGATION", "TERRAIN", "NAV_DB2"}


def test_correctly_signed_card_has_nothing_missigned(tmp_path):
    for feature in (Feature.NAVIGATION, Feature.TERRAIN, Feature.NAV_DB2):
        update_feat_unlk(tmp_path, feature, VOL, GTN, SYSID, 0x1111)
    assert _feat_unlk_missigned(tmp_path, GTN, SYSID) == {}
    assert _feat_unlk_missigned(tmp_path, GTN, None) == {}      # System ID unknown: not checked
    assert _feat_unlk_missigned(tmp_path / "nope", GTN, SYSID) == {}


def test_missigned_database_is_planned_again():
    cycles = {"Navigation Data": {"issue": "2611"}, "SafeTaxi": {"issue": "26S6"}}
    lines = []
    out = _drop_missigned_cycles(cycles, {"NAVIGATION": "signed for product 0x01F2, not 0x0402"},
                                 lines.append)
    assert out == {"SafeTaxi": {"issue": "26S6"}} and len(lines) == 1
    assert "Navigation Data" in cycles                           # input untouched


def test_misnamed_copy_is_found_only_when_it_is_the_same_file(tmp_path):
    (tmp_path / "ldr_sys").mkdir()
    (tmp_path / "ldr_sys" / "nav_db2.bin").write_bytes(b"x" * 100 + b"\x01\x02\x03\x04")
    paths = {2: "ldr_sys/nav_db2.bin"}
    assert _misnamed_copies(tmp_path, paths) == []               # no basemap.bin
    (tmp_path / "basemap.bin").write_bytes(b"y" * 100 + b"\x09\x09\x09\x09")
    assert _misnamed_copies(tmp_path, paths) == []               # another file: left alone
    (tmp_path / "basemap.bin").write_bytes(b"x" * 100 + b"\x01\x02\x03\x04")
    assert _misnamed_copies(tmp_path, paths) == [tmp_path / "basemap.bin"]
    assert _misnamed_copies(tmp_path, {}) == []
