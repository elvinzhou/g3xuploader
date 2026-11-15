"""
Download, cache and unpack firmware packages.

Cache layout, per version:

    <data_dir>/firmware/<product>/<version>/
        package.json        written last; its presence marks the package complete
        image/              the exact tree to put at the card root
        <bulletin>.pdf      service bulletin shipped in the zip, if any

``image/`` is what card installs copy; nothing here is signed per unit.
"""

import hashlib
import json
import logging
import mmap
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional

import requests

from avcardtool.firmware.catalog import FirmwareNetworkError, FirmwareRelease, validate_download_url
from avcardtool.firmware.registry import (
    KIND_COPY2CARD,
    KIND_GCD_FOLDER,
    FirmwareError,
    Product,
)

logger = logging.getLogger(__name__)

SEVENZ_SIGNATURE = b"7z\xbc\xaf\x27\x1c"
SEVENZ_BINARIES = ("7zz", "7z", "7za", "7zr")
KEEP_VERSIONS = 2
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# Paths and the package manifest
# ---------------------------------------------------------------------------

def firmware_dir(data_dir) -> Path:
    return Path(data_dir) / "firmware"


def _safe_version(version: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z._-]+", version) or version in (".", ".."):
        raise FirmwareError(f"Unsafe version string: {version!r}")
    return version


def package_dir(data_dir, product_key: str, version: str) -> Path:
    return firmware_dir(data_dir) / product_key / _safe_version(version)


@dataclass
class ImageFile:
    path: str      # relative to image/, forward slashes
    size: int
    sha256: str


@dataclass
class PreparedPackage:
    product_key: str
    version: str
    release_date: str
    source_url: str
    kind: str
    erase_destination: bool
    files: List[ImageFile]
    bulletin: Optional[str]   # file name inside the package dir
    root: Path                # the package dir (not stored in package.json)

    @property
    def image_dir(self) -> Path:
        return self.root / "image"

    @property
    def bulletin_path(self) -> Optional[Path]:
        return self.root / self.bulletin if self.bulletin else None

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def file(self, rel: str) -> Optional[ImageFile]:
        return next((f for f in self.files if f.path == rel), None)

    def to_json(self) -> Dict[str, Any]:
        return {
            "product": self.product_key,
            "version": self.version,
            "release_date": self.release_date,
            "source_url": self.source_url,
            "kind": self.kind,
            "erase_destination": self.erase_destination,
            "files": [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in self.files],
            "bulletin": self.bulletin,
        }


def load_package(root: Path) -> Optional[PreparedPackage]:
    """The package in ``root`` if package.json is valid and every image file is present with its size."""
    try:
        data = json.loads((root / "package.json").read_text())
        pkg = PreparedPackage(
            product_key=data["product"],
            version=data["version"],
            release_date=data.get("release_date", ""),
            source_url=data.get("source_url", ""),
            kind=data["kind"],
            erase_destination=bool(data.get("erase_destination", False)),
            files=[ImageFile(f["path"], int(f["size"]), f["sha256"]) for f in data["files"]],
            bulletin=data.get("bulletin"),
            root=root,
        )
    except Exception:
        return None
    if not pkg.files:
        return None
    for f in pkg.files:
        p = pkg.image_dir / f.path
        try:
            if not p.is_file() or p.stat().st_size != f.size:
                return None
        except OSError:
            return None
    if pkg.bulletin and not (root / pkg.bulletin).is_file():
        return None
    return pkg


def list_cached(data_dir, product_key: str) -> List[PreparedPackage]:
    """Complete cached packages for a product, newest first."""
    base = firmware_dir(data_dir) / product_key
    found = []
    if base.is_dir():
        for d in base.iterdir():
            if d.is_dir():
                pkg = load_package(d)
                if pkg:
                    found.append(pkg)
    found.sort(key=lambda p: _version_key(p.version), reverse=True)
    return found


def _version_key(version: str):
    from avcardtool.firmware.catalog import parse_version
    try:
        return parse_version(version)
    except FirmwareError:
        return ()


def prune_cache(data_dir, product_key: str, keep: int = KEEP_VERSIONS) -> List[str]:
    """Delete all but the newest ``keep`` version directories of a product."""
    base = firmware_dir(data_dir) / product_key
    if not base.is_dir():
        return []
    dirs = sorted((d for d in base.iterdir() if d.is_dir()),
                  key=lambda d: _version_key(d.name), reverse=True)
    removed = []
    for d in dirs[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    return removed


# ---------------------------------------------------------------------------
# Safe archive handling
# ---------------------------------------------------------------------------

def check_member_name(name: str) -> PurePosixPath:
    """Reject absolute paths, drive letters and '..' in an archive member name."""
    norm = name.replace("\\", "/")
    p = PurePosixPath(norm)
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in p.parts or "\x00" in norm:
        raise FirmwareError(f"Unsafe path in archive: {name!r}")
    return p


def _within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def verify_tree(base: Path) -> None:
    """Everything under ``base`` must be a plain file or directory that really lives inside it."""
    for dirpath, dirnames, filenames in os.walk(base):
        for n in dirnames + filenames:
            p = Path(dirpath) / n
            if p.is_symlink() or not _within(base, p):
                raise FirmwareError(f"Archive produced a link or an out-of-tree path: {p}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class CorruptPackage(FirmwareError):
    """The downloaded zip is damaged or unsafe; downloading it again is the remedy."""


def validate_zip(path: Path) -> zipfile.ZipFile:
    """Open a zip, check its CRCs and every member name. The caller closes it."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise CorruptPackage(f"{path.name} is not a valid zip file: {e}")
    try:
        bad = zf.testzip()
        if bad:
            raise CorruptPackage(f"{path.name}: corrupt member {bad}")
        for info in zf.infolist():
            try:
                check_member_name(info.filename)
            except FirmwareError as e:
                raise CorruptPackage(str(e))
    except BaseException:
        zf.close()
        raise
    return zf


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, _CHUNK)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_zip(url: str, dest_dir: Path, session: Optional[requests.Session] = None) -> Path:
    """
    Stream ``url`` to ``dest_dir/<file name>``.

    Mirrors FlyGarminAPI.download_file: HEAD first for the size, a complete file
    under the final name is reused, the body goes to "<name>.part" and is only
    renamed once the byte count matches.
    """
    validate_download_url(url)
    http = session or requests
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(requests.utils.unquote(url.split("?")[0].rsplit("/", 1)[-1])).name or "package.zip"
    name = re.sub(r"[^0-9A-Za-z._-]", "_", name)
    dest = dest_dir / name
    part = dest.with_name(dest.name + ".part")

    server_size = 0
    try:
        head = http.head(url, allow_redirects=True, timeout=30)
        if head.status_code < 400:
            server_size = int(head.headers.get("content-length", 0) or 0)
    except (requests.RequestException, ValueError):
        pass

    if server_size and dest.exists() and dest.stat().st_size == server_size:
        logger.info(f"Already downloaded: {dest.name} ({server_size:,} bytes)")
        return dest

    logger.info(f"Downloading {name}" + (f" ({server_size:,} bytes)" if server_size else ""))
    try:
        resp = http.get(url, stream=True, timeout=600)
        resp.raise_for_status()
        if not str(getattr(resp, "url", url)).startswith("https://"):
            raise FirmwareError(f"Download was redirected to a non-https URL: {resp.url}")
        encoded = resp.headers.get("content-encoding", "identity").lower() not in ("", "identity")
        expected = None if encoded else (int(resp.headers.get("content-length", 0) or 0) or server_size)
        downloaded = 0
        with open(part, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        if expected and downloaded != expected:
            raise FirmwareError(
                f"Incomplete download of {name}: got {downloaded:,} of {expected:,} bytes. Run it again to retry."
            )
        os.replace(part, dest)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise FirmwareNetworkError(f"Download of {url} failed: {e}")
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


# ---------------------------------------------------------------------------
# 7-Zip SFX handling (Copy2Card packages)
# ---------------------------------------------------------------------------

def find_7z_offsets(path: Path) -> List[int]:
    """Offsets of every 7z signature in ``path`` (an SFX is a PE stub followed by the archive)."""
    offsets = []
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if size == 0:
            return offsets
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            pos = mm.find(SEVENZ_SIGNATURE)
            while pos != -1:
                offsets.append(pos)
                pos = mm.find(SEVENZ_SIGNATURE, pos + 1)
    return offsets


def _carve(src: Path, offset: int, dest: Path) -> None:
    with open(src, "rb") as f, open(dest, "wb") as out:
        f.seek(offset)
        shutil.copyfileobj(f, out, _CHUNK)


def _extract_with_py7zr(archive: Path, target: Path) -> None:
    import py7zr
    with py7zr.SevenZipFile(archive, "r") as z:
        for name in z.getnames():
            check_member_name(name)
        for entry in z.files:
            if getattr(entry, "is_symlink", False) or getattr(entry, "is_junction", False):
                raise FirmwareError(f"Archive contains a link: {entry.filename}")
        z.extractall(path=target)


def _find_7z_binary() -> Optional[str]:
    for name in SEVENZ_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _extract_with_binary(binary: str, archive: Path, target: Path) -> None:
    proc = subprocess.run(
        [binary, "x", "-y", f"-o{target}", str(archive)],
        capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise FirmwareError(f"{Path(binary).name} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")


def extract_embedded_7z(exe: Path, target: Path, work: Path) -> None:
    """
    Extract the 7z archive embedded in a self-extractor into ``target``.

    The exe is never run: the archive is carved out at its signature offset and
    opened with py7zr, or with a system 7-Zip binary when py7zr is not installed.
    """
    try:
        import py7zr  # noqa: F401
        have_py7zr = True
    except ImportError:
        have_py7zr = False
    binary = None if have_py7zr else _find_7z_binary()
    if not have_py7zr and not binary:
        raise FirmwareError(
            "Cannot unpack the GTN package: install the 'py7zr' Python package "
            "(pip install py7zr) or the '7zip' / 'p7zip-full' system package."
        )

    offsets = find_7z_offsets(exe)
    if not offsets:
        raise FirmwareError(f"No 7z archive found inside {exe.name}")

    last_error: Optional[Exception] = None
    for off in offsets:
        carved = work / f"embedded_{off}.7z"
        attempt = work / f"extract_{off}"
        try:
            _carve(exe, off, carved)
            attempt.mkdir()
            if have_py7zr:
                _extract_with_py7zr(carved, attempt)
            else:
                _extract_with_binary(binary, carved, attempt)
            verify_tree(attempt)
            if not any(attempt.iterdir()):
                raise FirmwareError("archive is empty")
            target.mkdir(parents=True, exist_ok=True)
            for child in attempt.iterdir():
                shutil.move(str(child), str(target / child.name))
            return
        except Exception as e:  # try the next candidate signature
            logger.debug(f"7z candidate at offset {off} failed: {e}")
            last_error = e
        finally:
            carved.unlink(missing_ok=True)
            shutil.rmtree(attempt, ignore_errors=True)
    raise FirmwareError(f"Could not open the archive embedded in {exe.name}: {last_error}")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_update_info(path: Path) -> Dict[str, Any]:
    """RootDirPath and EraseDestination from Copy2Card's updateinfo.xml (namespace-agnostic)."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise FirmwareError(f"Cannot read {path.name}: {e}")
    values = {_local(el.tag): (el.text or "").strip() for el in root.iter()}
    root_dir = values.get("RootDirPath")
    if not root_dir:
        raise FirmwareError(f"{path.name} has no RootDirPath")
    return {
        "root_dir": root_dir,
        "erase_destination": values.get("EraseDestination", "true").lower() == "true",
    }


def _resolve_ci(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base`` matching each component case-insensitively."""
    current = base
    for part in check_member_name(rel).parts:
        if part in ("", "."):
            continue
        match = next((c for c in current.iterdir() if c.name.lower() == part.lower()), None) \
            if current.is_dir() else None
        if match is None:
            raise FirmwareError(f"RootDirPath '{rel}' not found in the package (missing '{part}')")
        current = match
    if not current.is_dir() or not _within(base, current):
        raise FirmwareError(f"RootDirPath '{rel}' is not a directory inside the package")
    return current


# ---------------------------------------------------------------------------
# Preparing images
# ---------------------------------------------------------------------------

def _build_gcd_image(zf: zipfile.ZipFile, image: Path) -> None:
    count = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        parts = check_member_name(info.filename).parts
        if len(parts) >= 2 and parts[0].lower() == "garmin":
            _extract_member(zf, info, image.joinpath("Garmin", *parts[1:]))
            count += 1
    if not count:
        raise FirmwareError("The package has no Garmin/ folder")


def _build_copy2card_image(zf: zipfile.ZipFile, image: Path, work: Path) -> bool:
    exes = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".exe")]
    if len(exes) != 1:
        raise FirmwareError(f"Expected one .exe in the package, found {len(exes)}")
    exe = work / "installer.exe"  # never executed
    _extract_member(zf, exes[0], exe)
    unpacked = work / "unpacked"
    extract_embedded_7z(exe, unpacked, work)
    exe.unlink(missing_ok=True)

    info_file = next((p for p in unpacked.iterdir() if p.name.lower() == "updateinfo.xml"), None)
    if info_file is None:
        raise FirmwareError("The installer has no updateinfo.xml")
    info = read_update_info(info_file)
    root_dir = _resolve_ci(unpacked, info["root_dir"])
    shutil.move(str(root_dir), str(image))
    return info["erase_destination"]


def _manifest_files(image: Path) -> List[ImageFile]:
    files = []
    for p in sorted(image.rglob("*")):
        if p.is_file():
            files.append(ImageFile(p.relative_to(image).as_posix(), p.stat().st_size, _sha256(p)))
    return files


def prepare_package(zip_path: Path, product: Product, release: FirmwareRelease, root: Path) -> PreparedPackage:
    """Turn a downloaded zip into ``root/image`` + ``root/package.json``."""
    work = root / ".work"
    image_tmp = root / "image.tmp"
    image = root / "image"
    for stale in (work, image_tmp, image):
        shutil.rmtree(stale, ignore_errors=True)
    (root / "package.json").unlink(missing_ok=True)
    work.mkdir(parents=True)

    zf = validate_zip(zip_path)
    try:
        if product.kind == KIND_GCD_FOLDER:
            _build_gcd_image(zf, image_tmp)
            erase = False  # a Garmin/ folder is added to the card; nothing is erased
        elif product.kind == KIND_COPY2CARD:
            erase = _build_copy2card_image(zf, image_tmp, work)
        else:
            raise FirmwareError(f"Unknown package kind {product.kind}")

        bulletin = None
        for info in zf.infolist():
            if not info.is_dir() and info.filename.lower().endswith(".pdf"):
                bulletin = Path(info.filename.replace("\\", "/")).name
                _extract_member(zf, info, root / bulletin)
                break
    finally:
        zf.close()
        shutil.rmtree(work, ignore_errors=True)

    verify_tree(image_tmp)
    files = _manifest_files(image_tmp)
    if not files:
        raise FirmwareError("The package produced an empty card image")
    os.replace(image_tmp, image)

    pkg = PreparedPackage(
        product_key=product.key, version=release.version, release_date=release.release_date,
        source_url=release.download_url, kind=product.kind, erase_destination=erase,
        files=files, bulletin=bulletin, root=root,
    )
    tmp = root / "package.json.tmp"
    tmp.write_text(json.dumps(pkg.to_json(), indent=2))
    os.replace(tmp, root / "package.json")
    return pkg


def ensure_package(release: FirmwareRelease, product: Product, data_dir,
                   session: Optional[requests.Session] = None,
                   force: bool = False) -> PreparedPackage:
    """
    Make sure ``release`` is downloaded and unpacked in the cache (idempotent).

    A complete package is returned as-is. The zip is kept only until the image
    is built; a failure leaves it so the next run resumes without downloading.
    """
    root = package_dir(data_dir, product.key, release.version)
    if not force:
        existing = load_package(root)
        if existing:
            return existing

    try:
        root.mkdir(parents=True, exist_ok=True)
        zip_path = download_zip(release.download_url, root, session)
        try:
            pkg = prepare_package(zip_path, product, release, root)
        except CorruptPackage:
            # A bad zip will not get better on its own; drop it so the next run re-downloads.
            # Other failures (e.g. no 7z extractor installed) keep the zip for the next run.
            zip_path.unlink(missing_ok=True)
            raise
        zip_path.unlink(missing_ok=True)
    except OSError as e:
        raise FirmwareError(f"Cannot write the firmware cache ({root}): {e}")
    prune_cache(data_dir, product.key)
    return pkg


def verify_package(pkg: PreparedPackage) -> None:
    """Re-hash every image file against package.json (before a card is wiped). Raises FirmwareError."""
    for f in pkg.files:
        p = pkg.image_dir / f.path
        if not p.is_file() or p.stat().st_size != f.size or _sha256(p) != f.sha256:
            invalidate_package(pkg)
            raise FirmwareError(f"Cached file {f.path} is damaged (cache corrupt); it will be downloaded again")


def invalidate_package(pkg: PreparedPackage) -> None:
    """Mark a package incomplete (e.g. its cached files no longer match their hashes)."""
    (pkg.root / "package.json").unlink(missing_ok=True)
