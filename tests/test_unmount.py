"""udisksctl has no --mount-point option; unmount must go through -b <device>."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from avcardtool.core.utils import unmount_device


def _run_factory(calls, findmnt_out="/dev/sde1\n", udisks_rc=0, umount_rc=1):
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "findmnt":
            return SimpleNamespace(returncode=0 if findmnt_out else 1, stdout=findmnt_out, stderr="")
        if cmd[0] == "udisksctl":
            return SimpleNamespace(returncode=udisks_rc, stdout="", stderr="Error unmounting: busy")
        return SimpleNamespace(returncode=umount_rc, stdout="", stderr="umount: permission denied")
    return run


def test_unmount_uses_block_device_not_mount_point():
    calls = []
    with mock.patch("subprocess.run", side_effect=_run_factory(calls)):
        unmount_device(Path("/media/pilot/CARD"))

    assert ["udisksctl", "unmount", "--block-device", "/dev/sde1"] in calls
    assert not any("--mount-point" in c for c in calls)
    assert not any(c[0] == "umount" for c in calls)  # udisksctl succeeded


def test_unmount_failure_reports_udisksctl_error():
    calls = []
    with mock.patch("subprocess.run", side_effect=_run_factory(calls, udisks_rc=1)):
        with pytest.raises(RuntimeError) as exc:
            unmount_device(Path("/media/pilot/CARD"))

    msg = str(exc.value)
    assert "udisksctl: Error unmounting: busy" in msg
    assert "umount: permission denied" in msg


def test_unmount_card_wrapper_does_not_raise():
    from avcardtool.navdata.sdcard import SDCardDetector

    calls = []
    with mock.patch("subprocess.run", side_effect=_run_factory(calls, udisks_rc=1)):
        SDCardDetector().unmount_card("/media/pilot/CARD")  # logs a warning only
    assert ["udisksctl", "unmount", "--block-device", "/dev/sde1"] in calls


# ---------------------------------------------------------------------------
# findmnt prints each mount twice inside the sandboxed systemd units
# ---------------------------------------------------------------------------

def _findmnt(out):
    return lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=out, stderr="")


def test_get_mount_point_ignores_duplicated_findmnt_output():
    from avcardtool.core.utils import get_mount_point

    out = "/media/pilot/6636-6364\n/media/pilot/6636-6364\n"
    with mock.patch("subprocess.run", side_effect=_findmnt(out)):
        assert get_mount_point(Path("/dev/sdb1")) == Path("/media/pilot/6636-6364")


def test_is_mounted_readonly_with_duplicated_output():
    from avcardtool.core.utils import _is_mounted_readonly

    ro = "ro,nosuid,errors=remount-ro\nro,nosuid,errors=remount-ro\n"
    rw = "rw,nosuid,errors=remount-ro\nrw,nosuid,errors=remount-ro\n"
    with mock.patch("subprocess.run", side_effect=_findmnt(ro)):
        assert _is_mounted_readonly(Path("/dev/sdb1")) is True
    with mock.patch("subprocess.run", side_effect=_findmnt(rw)):
        assert _is_mounted_readonly(Path("/dev/sdb1")) is False


def test_unmount_with_duplicated_source_output():
    calls = []
    with mock.patch(
        "subprocess.run",
        side_effect=_run_factory(calls, findmnt_out="/dev/sde1\n/dev/sde1\n"),
    ):
        unmount_device(Path("/media/pilot/CARD"))
    assert ["udisksctl", "unmount", "--block-device", "/dev/sde1"] in calls
