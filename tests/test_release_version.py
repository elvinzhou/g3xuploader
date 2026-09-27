"""Tests for scripts/release_version.py (commit-driven version bumps)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "release_version.py"
_spec = importlib.util.spec_from_file_location("release_version", _SCRIPT)
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)


def c(subject, body=""):
    return rv.Commit(sha="0123456789abcdef", subject=subject, body=body)


@pytest.mark.parametrize("subject,expected", [
    ("feat: add dry run", "minor"),
    ("feat(navdata): daily timer", "minor"),
    ("fix: carryd timestamp", "patch"),
    ("perf: faster CSV parse", "patch"),
    ("refactor(cli): split module", "patch"),
    ("build: bump requests", "patch"),
    ("docs: update README", None),
    ("chore: regenerate poetry.lock", None),
    ("ci: release workflow", None),
    ("tests: update TAW parser tests", None),
    ("style: black", None),
    ("feat!: drop python 3.9", "major"),
    ("fix(api)!: rename config key", "major"),
    # Older scope-style subjects still ship as a patch
    ("navdata: parse Garmin System ID as hex", "patch"),
    ("Fix Carryd uploader to match eablog API", "patch"),
    # Never counted
    ("Bump version: 1.8.4 → 1.8.5", None),
    ("Merge pull request #14 from elvinzhou/branch", None),
])
def test_classify(subject, expected):
    assert rv.classify(c(subject)) == expected


def test_breaking_change_footer_is_major():
    assert rv.classify(c("fix: rename key", "Details.\n\nBREAKING CHANGE: config key renamed")) == "major"
    assert rv.classify(c("docs: x", "BREAKING-CHANGE: y")) == "major"


def test_highest():
    assert rv.highest([None, "patch", "minor", None]) == "minor"
    assert rv.highest(["patch", "major"]) == "major"
    assert rv.highest([None, None]) is None
    assert rv.highest([]) is None


def test_release_notes_groups_and_omits_no_release():
    notes = rv.release_notes([
        c("feat: dry run"),
        c("fix(carryd): stale guard"),
        c("navdata: legacy subject"),
        c("docs: readme"),
        c("feat!: new config format"),
    ])
    assert notes.index("## Breaking changes") < notes.index("## Features") < notes.index("## Fixes")
    assert "- dry run (0123456)" in notes
    assert "- stale guard (0123456)" in notes
    assert "- navdata: legacy subject (0123456)" in notes
    assert "- new config format (0123456)" in notes
    assert "readme" not in notes


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "initial")
    git("tag", "v1.0.0")
    monkeypatch.chdir(tmp_path)
    return git


def test_main_against_git_history(repo, tmp_path, capsys):
    repo("commit", "-q", "--allow-empty", "-m", "docs: readme")
    repo("commit", "-q", "--allow-empty", "-m", "fix: bug")
    notes = tmp_path / "notes.md"

    assert rv.main(["--notes-file", str(notes)]) == 0
    assert capsys.readouterr().out.strip() == "part=patch"
    assert "- bug (" in notes.read_text()

    repo("commit", "-q", "--allow-empty", "-m", "feat: thing")
    rv.main([])
    assert capsys.readouterr().out.strip() == "part=minor"


def test_main_nothing_to_release(repo, capsys):
    repo("commit", "-q", "--allow-empty", "-m", "chore: tidy")
    rv.main([])
    assert capsys.readouterr().out.strip() == "part="


def test_main_override(repo, capsys):
    rv.main(["--override", "major"])
    assert capsys.readouterr().out.strip() == "part=major"
