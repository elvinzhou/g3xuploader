"""navdata install only installs databases for the card's own avionics."""

import json

from click.testing import CliRunner

from avcardtool.cli import _filter_entries_for_avionics, cli
from avcardtool.navdata.garmin.taw_parser import TAW_REGION_PATHS
from test_taw_parser import build_taw

GTN = 0x0402     # the card's avionics (GTN 6XX Xi)
G600 = 0x01F2    # another family served by the same GTN subscription
X3 = 0x07EB      # a second device on the aircraft


def entry(local, avdb, db_type, device, removable=(), destination=None):
    return {
        "local_path": local, "destination": destination, "avdb_type": avdb,
        "avdb_type_id": 1, "series_id": 1, "issue_name": "2610", "device_id": device,
        "removable_paths": list(removable), "unlock_codes": [], "taw_database_type": db_type,
    }


def test_filter_keeps_matching_taw_and_same_device_charts():
    entries = [
        entry("t.taw", "Terrain", GTN, 1),
        entry("o.taw", "Obstacle", G600, 1),          # same device, other avionics
        entry("s.jnx", "Sectionals", None, 1),        # chart for the matching device
        entry("b.taw", "Basemap", X3, 2),             # other device
        entry("x.jnx", "Sectionals", None, 2),        # chart for the other device
    ]
    keep, skip = _filter_entries_for_avionics(entries, GTN)
    assert [e["local_path"] for e in keep] == ["t.taw", "s.jnx"]
    assert [e["local_path"] for e in skip] == ["o.taw", "b.taw", "x.jnx"]


def test_filter_nothing_matches():
    keep, skip = _filter_entries_for_avionics([entry("b.taw", "Basemap", X3, 2)], GTN)
    assert keep == [] and len(skip) == 1


def _region_file(region_id):
    return TAW_REGION_PATHS[region_id]


def test_install_end_to_end(tmp_path):
    dl = tmp_path / "download"
    dl.mkdir()
    (dl / "t.taw").write_bytes(build_taw(db_type=GTN, regions=[(0x23, b"terrain")]))
    (dl / "o.taw").write_bytes(build_taw(db_type=G600, regions=[(0x26, b"g600 obstacles")]))
    (dl / "b.taw").write_bytes(build_taw(db_type=X3, regions=[(0x27, b"basemap")]))
    (dl / "s.jnx").write_bytes(b"gtn chart")
    (dl / "x.jnx").write_bytes(b"other chart")
    (dl / "navdata_manifest.json").write_text(json.dumps({
        "card_serial": "0", "system_ids": [], "batch_id": None,
        "device_type_map": {"GTN 6XX Xi": GTN, "G600": G600, "X3": X3},
        "entries": [
            entry("t.taw", "Terrain", GTN, 1),
            entry("o.taw", "Obstacle", G600, 1, removable=["/keep_me.odb"]),
            entry("s.jnx", "Sectionals", None, 1, destination="rasters/SECT/S.jnx"),
            entry("b.taw", "Basemap", X3, 2, removable=["/also_keep.bin"]),
            entry("x.jnx", "Sectionals", None, 2, destination="rasters/SECT/X.jnx"),
        ],
    }))

    card = tmp_path / "card"
    card.mkdir()
    (card / "avionics.txt").write_text("GTN 6XX Xi")
    (card / "keep_me.odb").write_text("existing")      # listed only by a skipped entry
    (card / "also_keep.bin").write_text("existing")
    (card / "Garmin").mkdir()
    device_xml = (b"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Device xmlns=\"http://www.garmin.com/"
                  b"xmlschemas/GarminDevice/v2\"><UpdateFile><PartNumber>006-D0974-50</PartNumber>"
                  b"<Version><Major>26</Major><Minor>7</Minor></Version></UpdateFile></Device>")
    (card / "Garmin/GarminDevice.xml").write_bytes(device_xml)
    (card / ".gadm.meta").write_text(json.dumps({"id": "abc", "garminAccount": "Elvin Zhou",
                                                  "gadmAvionicsName": "G3X Touch"}))

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"),
                                          "log_file": str(tmp_path / "log.txt")}}))
    result = CliRunner().invoke(cli, [
        "--config", str(cfg), "navdata", "install", str(card), "--from", str(dl), "-y",
    ])
    assert result.exit_code == 0, result.output
    assert "Skipping 3 file(s) for other avionics" in result.output

    # The card's own databases are installed
    assert (card / _region_file(0x23)).read_bytes() == b"terrain"
    assert (card / "rasters/SECT/S.jnx").read_bytes() == b"gtn chart"
    # Other avionics' databases are not
    assert not (card / _region_file(0x26)).exists()
    assert not (card / _region_file(0x27)).exists()
    assert not (card / "rasters/SECT/X.jnx").exists()
    # ...and their cleanup lists don't delete anything from this card
    assert (card / "keep_me.odb").read_text() == "existing"
    assert (card / "also_keep.bin").read_text() == "existing"
    # GarminDevice.xml belongs to the avionics; GADM leaves it alone and so do we
    assert (card / "Garmin/GarminDevice.xml").read_bytes() == device_xml
    # .gadm.meta keeps GADM's account display name and card id
    meta = json.loads((card / ".gadm.meta").read_text())
    assert meta["garminAccount"] == "Elvin Zhou" and meta["id"] == "abc"
    assert meta["gadmLastInstalled"].endswith("GMT+0000 (Coordinated Universal Time)")


def test_install_refuses_when_nothing_matches(tmp_path):
    dl = tmp_path / "download"
    dl.mkdir()
    (dl / "b.taw").write_bytes(build_taw(db_type=X3, regions=[(0x27, b"basemap")]))
    (dl / "navdata_manifest.json").write_text(json.dumps({
        "card_serial": "0", "device_type_map": {"GTN 6XX Xi": GTN, "X3": X3},
        "entries": [entry("b.taw", "Basemap", X3, 2)],
    }))
    card = tmp_path / "card"
    card.mkdir()
    (card / "avionics.txt").write_text("GTN 6XX Xi")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"),
                                          "log_file": str(tmp_path / "log.txt")}}))

    result = CliRunner().invoke(cli, [
        "--config", str(cfg), "navdata", "install", str(card), "--from", str(dl), "-y",
    ])
    assert result.exit_code == 1
    assert "Nothing in the download is for this card's avionics" in result.output
    assert list(card.iterdir()) == [card / "avionics.txt"]


# --- Databases already on the display (Garmin/GarminDevice.xml) ---------------

from avcardtool.cli import _on_display, _system_id_for_card  # noqa: E402
from avcardtool.navdata.garmin.feat_unlk import (  # noqa: E402
    CONTENT1_LEN, FILENAME_TO_FEATURE, Feature,
)

# <Description>s from N662EZ's G3X Touch card
ON_DISPLAY = [
    "US SafeTaxi 26S4", "FliteCharts 2607", "USA-VFR Navigation Data 2607",
    "Worldwide-9 Terrain 20T1", "US/Canada Obstacles Obstacle 26B4",
    "AOPA US Directory Data 26D4",
]


def _e(avdb, issue):
    return {"avdb_type": avdb, "issue_name": issue}


def test_on_display_matches_type_and_cycle():
    assert _on_display(_e("Terrain", "20T1"), ON_DISPLAY)
    assert _on_display(_e("Navigation Data", "2607"), ON_DISPLAY)
    assert _on_display(_e("FliteCharts", "2607"), ON_DISPLAY)
    assert _on_display(_e("Obstacle", "26B4"), ON_DISPLAY)
    assert _on_display(_e("SafeTaxi", "26S4"), ON_DISPLAY)
    assert _on_display(_e("Airport Directory", "26D4"), ON_DISPLAY)


def test_on_display_newer_cycle_or_other_type_is_installed():
    # What GADM installed on that card: newer cycles than the display had
    for avdb, issue in [("Navigation Data", "2611"), ("FliteCharts", "2611"),
                        ("Obstacle", "26B6"), ("SafeTaxi", "26S6"),
                        ("Airport Directory", "26D6")]:
        assert not _on_display(_e(avdb, issue), ON_DISPLAY)
    # Same cycle as navigation data, but a database the display doesn't list
    assert not _on_display(_e("IFR/VFR Charts", "2607"), ON_DISPLAY)
    assert not _on_display(_e("Terrain", "20T1"), [])


# --- System ID for the card's own device ---------------------------------------

G3X_SID = 0x60002CA61BD97   # flyGarmin "1688861845601687" → feat_unlk 0xCA67BD99
GTN_SID = 0x3430060F2024F   # flyGarmin "918093835665999"


def test_system_id_comes_from_the_cards_device():
    manifest = {"system_ids": [GTN_SID, G3X_SID],
                "device_system_ids": {"4244038": G3X_SID, "4244058": GTN_SID}}
    entries = [{"device_id": 4244038}, {"device_id": 4244038}]
    assert _system_id_for_card(manifest, entries) == G3X_SID


def test_system_id_fallbacks():
    assert _system_id_for_card({"system_ids": [G3X_SID]}, [{"device_id": 1}]) == G3X_SID
    assert _system_id_for_card({"system_ids": []}, [{"device_id": 1}]) == 0


def test_charts_slots_signed_by_one_file_each():
    assert FILENAME_TO_FEATURE["fc_tpc/fc_tpc.dat"] is Feature.FLITE_CHARTS
    assert FILENAME_TO_FEATURE["rasters/rasters.xml"] is Feature.SECTIONALS
    assert "fc_tpc/fc_tpc.fca" not in FILENAME_TO_FEATURE
    assert "rasters/rasters.hif" not in FILENAME_TO_FEATURE


def test_install_signs_with_g3x_id_and_skips_what_display_has(tmp_path):
    dl = tmp_path / "download"
    dl.mkdir()
    (dl / "t.taw").write_bytes(build_taw(db_type=GTN, regions=[(0x23, b"terrain")]))
    (dl / "o.taw").write_bytes(build_taw(db_type=GTN, regions=[(0x27, b"obstacle!")]))
    (dl / "navdata_manifest.json").write_text(json.dumps({
        "card_serial": "6230-6664",
        "system_ids": [GTN_SID, G3X_SID],                       # wrong one first
        "device_system_ids": {"1": G3X_SID, "2": GTN_SID},
        "device_type_map": {"GTN 6XX Xi": GTN},
        "entries": [
            {**entry("t.taw", "Terrain", GTN, 1), "issue_name": "20T1"},
            {**entry("o.taw", "Obstacle", GTN, 1), "issue_name": "26B6"},
        ],
    }))
    card = tmp_path / "card"
    (card / "Garmin").mkdir(parents=True)
    (card / "avionics.txt").write_text("GTN 6XX Xi")
    (card / "Garmin/GarminDevice.xml").write_text(
        '<?xml version="1.0"?><Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">'
        "<UpdateFile><Description>Worldwide-9 Terrain 20T1</Description></UpdateFile>"
        "<UpdateFile><Description>US/Canada Obstacles Obstacle 26B4</Description></UpdateFile>"
        "</Device>")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"system": {"data_dir": str(tmp_path / "data"),
                                          "log_file": str(tmp_path / "log.txt")}}))

    result = CliRunner().invoke(cli, [
        "--config", str(cfg), "navdata", "install", str(card), "--from", str(dl), "-y",
    ])
    assert result.exit_code == 0, result.output
    assert "Already on the display" in result.output and "Terrain 20T1" in result.output

    # Terrain 20T1 is already on the display: not installed. Obstacle 26B6 is newer: installed.
    assert not (card / _region_file(0x23)).exists()
    assert (card / _region_file(0x27)).read_bytes() == b"obstacle!"

    # Its feat_unlk.dat slot is signed with the G3X's own System ID
    data = (card / "feat_unlk.dat").read_bytes()
    slot = data[Feature.OBSTACLE.offset:]
    assert int.from_bytes(slot[CONTENT1_LEN + 4:CONTENT1_LEN + 8], "little") == 0xCA67BD99
    assert not any(data[Feature.TERRAIN.offset:Feature.TERRAIN.offset + 913])
