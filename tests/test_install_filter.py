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
