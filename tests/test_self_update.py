"""Tests for `avcardtool self-update`: only update when a newer release exists."""

from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

from avcardtool import cli as cli_mod
from avcardtool.cli import cli


def _release(tag):
    resp = mock.Mock()
    resp.json.return_value = {"tag_name": tag}
    resp.raise_for_status.return_value = None
    return resp


def _pip(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


@pytest.fixture
def env(monkeypatch):
    """Root, non-editable install at 1.9.3; records pip/sudo/system-file calls."""
    monkeypatch.setattr(cli_mod, "__version__", "1.9.3")
    monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cli_mod, "_editable_install_path", lambda: None)
    calls = SimpleNamespace(pip=[], sudo=None, system_files=[], relaunch=None)
    monkeypatch.setattr(cli_mod.sys, "argv", ["/usr/local/bin/avcardtool", "self-update"])

    def fake_run(cmd, **kwargs):
        calls.pip.append(cmd)
        return _pip("Successfully installed avcardtool-" + cmd[-1].rsplit("@v", 1)[-1])

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(cli_mod, "_update_system_files",
                        lambda ctx, v: calls.system_files.append(v))

    def fake_exec(prog, args):
        calls.sudo = args
        raise SystemExit(0)  # execvp never returns

    monkeypatch.setattr(cli_mod.os, "execvp", fake_exec)

    def fake_execv(prog, args):
        calls.relaunch = (prog, args)
        raise SystemExit(0)  # execv never returns

    monkeypatch.setattr(cli_mod.os, "execv", fake_execv)
    return calls


def run(*args):
    return CliRunner().invoke(cli, ["self-update", *args])


@pytest.mark.parametrize("tag", ["v1.9.3", "v1.9.2", "v1.8.10"])
def test_no_update_when_latest_is_not_newer(env, tag):
    with mock.patch("requests.get", return_value=_release(tag)):
        result = run()

    assert result.exit_code == 0, result.output
    assert "Already up to date (v1.9.3" in result.output
    assert env.pip == [] and env.system_files == [] and env.sudo is None


def test_updates_to_newer_release_tag(env):
    with mock.patch("requests.get", return_value=_release("v1.9.4")):
        result = run()

    assert result.exit_code == 0, result.output
    assert "New release available: v1.9.4" in result.output
    assert env.pip[0][-1] == "git+https://github.com/elvinzhou/g3xuploader.git@v1.9.4"
    # System files are refreshed by the NEW code: the new binary is re-launched
    assert env.system_files == []
    assert env.relaunch == (
        "/usr/local/bin/avcardtool",
        ["/usr/local/bin/avcardtool", "self-update", "--system-files-only", "--version", "1.9.4"],
    )


def test_versions_compare_numerically(env):
    """1.10.0 is newer than 1.9.3 even though '1.10.0' < '1.9.3' as text."""
    with mock.patch("requests.get", return_value=_release("v1.10.0")):
        run()
    assert env.pip[0][-1].endswith("@v1.10.0")


def test_release_check_failure_installs_nothing(env):
    with mock.patch("requests.get", side_effect=ConnectionError("no network")):
        result = run()

    assert result.exit_code == 1
    assert "Could not check for updates" in result.output
    assert env.pip == [] and env.system_files == []


def test_unexpected_tag_installs_nothing(env):
    with mock.patch("requests.get", return_value=_release("nightly")):
        result = run()
    assert result.exit_code == 1
    assert env.pip == []


def test_non_root_up_to_date_never_asks_for_sudo(env, monkeypatch):
    monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 1000)
    with mock.patch("requests.get", return_value=_release("v1.9.3")):
        result = run()
    assert result.exit_code == 0
    assert env.sudo is None


def test_non_root_newer_reruns_with_plain_sudo_self_update(env, monkeypatch):
    """No extra arguments: the sudoers rule matches them exactly."""
    monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 1000)
    with mock.patch("requests.get", return_value=_release("v1.9.4")):
        run()
    assert env.sudo == ["sudo", "/usr/local/bin/avcardtool", "self-update"]


def test_non_root_explicit_version_is_passed_through(env, monkeypatch):
    monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 1000)
    run("--version", "1.8.5")
    assert env.sudo == ["sudo", "/usr/local/bin/avcardtool", "self-update", "--version", "1.8.5"]


def test_explicit_version_installs_even_if_older(env):
    with mock.patch("requests.get", side_effect=AssertionError("no release check for --version")):
        result = run("--version", "1.8.5")
    assert result.exit_code == 0, result.output
    assert env.pip[0][-1].endswith("@v1.8.5")
    assert env.relaunch[1][-3:] == ["--system-files-only", "--version", "1.8.5"]


def test_dev_install_skips_release_check(env, monkeypatch):
    monkeypatch.setattr(cli_mod, "_editable_install_path", lambda: "/home/pi/g3xuploader")
    with mock.patch("requests.get", side_effect=AssertionError("no release check for dev")):
        result = run()
    assert result.exit_code == 0, result.output
    assert "Development install" in result.output
    assert env.pip == [] and env.system_files == [None]


def test_system_files_only_skips_pip_and_uses_running_code(env):
    result = run("--system-files-only", "--version", "1.9.4")
    assert result.exit_code == 0, result.output
    assert env.pip == [] and env.relaunch is None
    assert env.system_files == ["1.9.4"]


def test_falls_back_in_process_if_relaunch_fails(env, monkeypatch):
    def broken_execv(prog, args):
        raise OSError("exec format error")

    monkeypatch.setattr(cli_mod.os, "execv", broken_execv)
    with mock.patch("requests.get", return_value=_release("v1.9.4")):
        result = run()
    assert result.exit_code == 0, result.output
    assert "Could not re-launch" in result.output
    assert env.system_files == ["1.9.4"]


def test_sudoers_allows_the_sudo_rerun_without_a_password(env, monkeypatch):
    """sudoers matches arguments exactly: the re-run must match the rule."""
    import fnmatch
    from pathlib import Path

    monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 1000)
    with mock.patch("requests.get", return_value=_release("v1.9.4")):
        run()
    command = " ".join(env.sudo[1:])           # what sudo is asked to run

    sudoers = Path(__file__).parent.parent / "systemd" / "avcardtool-sudoers"
    rule = next(l for l in sudoers.read_text().splitlines() if "NOPASSWD:" in l)
    allowed = [c.strip() for c in rule.split("NOPASSWD:", 1)[1].split(",")]
    assert any(fnmatch.fnmatchcase(command, pattern) for pattern in allowed), (command, allowed)
