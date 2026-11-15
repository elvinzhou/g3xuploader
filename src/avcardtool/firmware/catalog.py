"""Fetch and parse Garmin's public software download pages."""

import html as html_lib
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from avcardtool.firmware.registry import FirmwareError, Product, page_url_for


class FirmwareNetworkError(FirmwareError):
    """Garmin could not be reached (offline, DNS, timeout, HTTP error)."""

logger = logging.getLogger(__name__)

_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_VERSION_RE = re.compile(r"software\s+version\s+v?(\d+(?:\.\d+)*)", re.I)
_DATE_RE = re.compile(r'<p[^>]*class="quiet"[^>]*>\s*as\s+of\s+([^<]+?)\s*</p>', re.S | re.I)
_ZIP_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+?\.zip)["']""", re.I)
_CHANGES_RE = re.compile(
    r"<h4[^>]*>\s*Changes\s+made\s+from\s+version\s+(\d+(?:\.\d+)*)\s+to\s+(\d+(?:\.\d+)*)\s*:?\s*</h4>",
    re.S | re.I,
)
_NOTES_RE = re.compile(r"<h3[^>]*>\s*Notes:?\s*</h3>", re.S | re.I)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class FirmwareRelease:
    product_key: str
    version: str
    release_date: str
    download_url: str
    title: str
    notes: List[str] = field(default_factory=list)
    page_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(version: str) -> Tuple[int, ...]:
    """'10.00' -> (10, 0). Raises FirmwareError for anything not dotted-numeric."""
    parts = str(version).strip().lstrip("vV").split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise FirmwareError(f"Not a dotted numeric version: {version!r}")


def compare_versions(a: str, b: str) -> int:
    """-1, 0 or 1. Missing trailing components count as zero ('10' == '10.00')."""
    ta, tb = list(parse_version(a)), list(parse_version(b))
    n = max(len(ta), len(tb))
    ta += [0] * (n - len(ta))
    tb += [0] * (n - len(tb))
    return (ta > tb) - (ta < tb)


def is_newer(candidate: str, than: str) -> bool:
    return compare_versions(candidate, than) > 0


# ---------------------------------------------------------------------------
# Download URL policy
# ---------------------------------------------------------------------------

def validate_download_url(url: str) -> str:
    """Only https URLs on garmin.com (or a subdomain) are accepted."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise FirmwareError(f"Refusing non-https download URL: {url}")
    if host != "garmin.com" and not host.endswith(".garmin.com"):
        raise FirmwareError(f"Refusing download URL outside garmin.com: {url}")
    return url


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

def _text(fragment: str) -> str:
    """HTML fragment -> plain text with <br> kept as line breaks."""
    fragment = _BR_RE.sub("\n", fragment)
    fragment = _TAG_RE.sub("", fragment)
    return html_lib.unescape(fragment)


def _lines(fragment: str) -> List[str]:
    out = []
    for line in _text(fragment).splitlines():
        line = " ".join(line.split())
        if line:
            out.append(line)
    return out


def _list_items(fragment: str) -> List[str]:
    items: List[str] = []
    for m in _LI_RE.finditer(fragment):
        items.extend(_lines(m.group(1)))
    return items


def _extract_notes(rest: str, version: str) -> List[str]:
    """
    Release notes following the version heading.

    Pages with a change history list one "Changes made from version X to Y"
    block per release, newest first: take the one that ends at this version
    (else the first). Pages without one have a "Notes:" list instead.
    """
    blocks = list(_CHANGES_RE.finditer(rest))
    if blocks:
        chosen = blocks[0]
        for m in blocks:
            try:
                if compare_versions(m.group(2), version) == 0:
                    chosen = m
                    break
            except FirmwareError:
                continue
        end = rest.find("</ul>", chosen.end())
        return _list_items(rest[chosen.end(): end if end != -1 else len(rest)])

    m = _NOTES_RE.search(rest)
    if m:
        end = rest.find("</ul>", m.end())
        return _list_items(rest[m.end(): end if end != -1 else len(rest)])
    return []


def parse_release_page(page: str, product: Product, page_url: Optional[str] = None) -> FirmwareRelease:
    """Parse a Garmin download_details page. Raises FirmwareError if the layout changed."""
    url = page_url_for(product, page_url)

    title = version = None
    rest = ""
    for m in _H2_RE.finditer(page):
        heading = " ".join(_text(m.group(1)).split())
        vm = _VERSION_RE.search(heading)
        if vm:
            title, version, rest = heading, vm.group(1), page[m.end():]
            break
    if not version:
        raise FirmwareError(
            f"Could not find a 'software version' heading on {url} — Garmin's page layout may have changed"
        )

    dm = _DATE_RE.search(rest)
    release_date = " ".join(html_lib.unescape(dm.group(1)).split()) if dm else ""

    lm = _ZIP_HREF_RE.search(rest)
    if not lm:
        raise FirmwareError(f"Could not find a .zip download link on {url} — Garmin's page layout may have changed")
    download_url = validate_download_url(html_lib.unescape(lm.group(1)).strip())

    return FirmwareRelease(
        product_key=product.key,
        version=version,
        release_date=release_date,
        download_url=download_url,
        title=title,
        notes=_extract_notes(rest, version),
        page_url=url,
    )


def fetch_release(product: Product, page_url: Optional[str] = None,
                  session: Optional[requests.Session] = None) -> FirmwareRelease:
    """Fetch the product's version page and parse it."""
    url = page_url_for(product, page_url)
    getter = session or requests
    try:
        resp = getter.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FirmwareNetworkError(f"Could not fetch {url}: {e}")
    # Decode explicitly: requests guesses ISO-8859-1 for pages without a charset header.
    return parse_release_page(resp.content.decode("utf-8", errors="replace"), product, url)
