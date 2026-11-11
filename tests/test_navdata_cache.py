"""Pruning of the shared navdata download cache."""

import json
import os
from pathlib import Path

from avcardtool import cli as cli_mod


def _make_issue(navdata: Path, series: str, issue: str, card: str = "auto_6636", size: int = 100):
    """cache/<series>/<issue>/file plus a hardlink in the per-card folder."""
    cache = navdata / "cache"
    f = cache / series / issue / "data.bin"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * size)
    link = navdata / card / series / issue / "data.bin"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.link(f, link)
    return f, link


def _state(cache: Path, entries: dict):
    (cache / cli_mod._DL_STATE_FILE).write_text(json.dumps(entries))


def test_keeps_newest_three_issues_per_series(tmp_path):
    navdata = tmp_path / "navdata"
    cache = navdata / "cache"
    entries = {}
    made = {}
    for issue in ["2608", "2609", "2610", "2611", "2612"]:
        made[issue] = _make_issue(navdata, "2054", issue)
        entries[f"2054/{issue}@06BF"] = {"status": "complete"}
    # A different series keeps its own three
    for issue in ["26S4", "26S5", "26S6"]:
        _make_issue(navdata, "2077", issue)
        entries[f"2077/{issue}"] = {"status": "complete"}
    _state(cache, entries)

    freed = cli_mod._prune_navdata_cache(cache, keep=3)

    for issue in ["2608", "2609"]:
        assert not made[issue][0].exists()
        assert not made[issue][1].exists()          # hardlink in the card folder too
        assert not (cache / "2054" / issue).exists()
    for issue in ["2610", "2611", "2612"]:
        assert made[issue][0].exists() and made[issue][1].exists()
    for issue in ["26S4", "26S5", "26S6"]:
        assert (cache / "2077" / issue / "data.bin").exists()

    state = json.loads((cache / cli_mod._DL_STATE_FILE).read_text())
    assert "2054/2608@06BF" not in state and "2054/2609@06BF" not in state
    assert "2054/2612@06BF" in state
    assert freed == 200  # two removed issues, hardlinks counted once


def test_year_rollover_sorts_correctly(tmp_path):
    navdata = tmp_path / "navdata"
    cache = navdata / "cache"
    for issue in ["2612", "2613", "2701", "2702"]:
        _make_issue(navdata, "2054", issue)
    _state(cache, {})

    cli_mod._prune_navdata_cache(cache, keep=3)

    assert not (cache / "2054" / "2612").exists()
    assert (cache / "2054" / "2613").exists()
    assert (cache / "2054" / "2702").exists()


def test_never_prunes_issue_being_downloaded(tmp_path):
    navdata = tmp_path / "navdata"
    cache = navdata / "cache"
    for issue in ["2608", "2609", "2610", "2611"]:
        _make_issue(navdata, "2054", issue)
    _state(cache, {"2054/2608": {"status": "downloading", "pid": os.getpid()}})

    cli_mod._prune_navdata_cache(cache, keep=3)

    assert (cache / "2054" / "2608").exists()       # live downloader: left alone


def test_missing_cache_dir_is_a_noop(tmp_path):
    assert cli_mod._prune_navdata_cache(tmp_path / "nope") == 0
