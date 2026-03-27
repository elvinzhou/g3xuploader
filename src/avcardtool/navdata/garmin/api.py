#!/usr/bin/env python3
"""
FlyGarmin API client.

Wraps the fly.garmin.com REST API for listing aircraft/devices, available
database cycles, and downloading aviation database files.

Response structure (from observed API traffic):
    [ Aircraft ]
      └─ devices: [ Device ]
           └─ avdbTypes: [ AvdbType ]
                └─ series: [ Series ]
                     └─ installableIssues: [ Issue ]   ← what can be written to card
                        availableIssues:  [ Issue ]    ← what exists on server

Download flow:
    1. list_aircraft()                       → pick device + series + issue
    2. list_files(series_id, issue_name)     → get download URLs
    3. unlock(series_id, issue_name,         → authorise download for this card
              device_id, card_serial)
    4. download_file(db_file, output_dir)    → fetch bytes
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable
from urllib.parse import urlparse, parse_qs

import requests

from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError, FLYGARMIN_API

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    name: str                   # e.g. "2603"
    effective_at: str
    invalid_at: Optional[str]
    available_at: str
    critical: bool
    rev: int


@dataclass
class Series:
    series_id: int
    region_name: str
    installable_issues: List[Issue] = field(default_factory=list)
    available_issues: List[Issue]  = field(default_factory=list)
    issues_remaining: int = 0
    is_auto_renew: bool = False
    expected_end_date: Optional[str] = None


@dataclass
class AvdbType:
    type_id: int                # 1=NavData 2=Obstacle 3=Terrain 4=SafeTaxi …
    name: str
    status: str                 # "Latest" | "Expired" | "NotInstalled"
    days_per_cycle: Optional[int]
    series: List[Series] = field(default_factory=list)
    installed_issue_name: Optional[str] = None


@dataclass
class Device:
    device_id: int
    name: str
    system_id: str              # used as deviceID in unlock call
    display_serial: str
    avdb_status: str
    avdb_types: List[AvdbType] = field(default_factory=list)


@dataclass
class Aircraft:
    unique_id: int
    tail_number: str            # aircraft["id"] in response
    name: str
    avdb_status: str
    devices: List[Device] = field(default_factory=list)


@dataclass
class DatabaseFile:
    url: str
    file_size: int
    destination: Optional[str]          # subpath on SD card, or None for root
    subregion_id: Optional[int] = None

    @property
    def file_name(self) -> str:
        """Derive file name from the URL."""
        return self.url.split("/")[-1].split("?")[0]


@dataclass
class DatabaseIssueFiles:
    """Full response from list_files() for one series/issue."""
    issue_type: str                      # e.g. "TAW"
    total_file_size: int
    main_files: List[DatabaseFile]
    auxiliary_files: List[DatabaseFile]
    removable_paths: List[str]           # paths to delete on SD card before install


@dataclass
class BatchDatabase:
    """One database entry in a batch-update request."""
    series_id: int
    issue_name: str
    device_ids: List[int]
    subregion_ids: List[int] = field(default_factory=list)


@dataclass
class BatchUpdateEntry:
    """A single database entry returned inside a batch-update plan."""
    series_id: int
    issue_name: str
    avdb_type_name: str
    device_id: int
    card_serial: str


@dataclass
class BatchUpdate:
    """Response from POST /batch-updates/ or GET /batch-updates/{id}/."""
    batch_id: str                        # UUID from launchURL
    entries: List[BatchUpdateEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_issue(raw: dict) -> Issue:
    return Issue(
        name=raw.get("name", ""),
        effective_at=raw.get("effectiveAt", ""),
        invalid_at=raw.get("invalidAt"),
        available_at=raw.get("availableAt", ""),
        critical=raw.get("critical", False),
        rev=raw.get("rev", 1),
    )


def _parse_series(raw: dict) -> Series:
    region = raw.get("region") or {}
    return Series(
        series_id=raw.get("id", 0),
        region_name=region.get("name", ""),
        installable_issues=[_parse_issue(i) for i in raw.get("installableIssues", [])],
        available_issues=[_parse_issue(i) for i in raw.get("availableIssues", [])],
        issues_remaining=raw.get("issuesRemaining", 0),
        is_auto_renew=raw.get("isAutoRenew", False),
        expected_end_date=raw.get("expectedEndDate"),
    )


def _parse_avdb_type(raw: dict) -> AvdbType:
    installed = raw.get("installedIssue") or {}
    return AvdbType(
        type_id=raw.get("id", 0),
        name=raw.get("name", ""),
        status=raw.get("status", ""),
        days_per_cycle=raw.get("daysPerCycle"),
        series=[_parse_series(s) for s in raw.get("series", [])],
        installed_issue_name=installed.get("name"),
    )


def _parse_device(raw: dict) -> Device:
    return Device(
        device_id=raw.get("id", 0),
        name=raw.get("name", ""),
        system_id=str(raw.get("systemId", raw.get("serial", ""))),
        display_serial=raw.get("displaySerial", ""),
        avdb_status=raw.get("avdbStatus", ""),
        avdb_types=[_parse_avdb_type(a) for a in raw.get("avdbTypes", [])],
    )


def _parse_aircraft(raw: dict) -> Aircraft:
    return Aircraft(
        unique_id=raw.get("uniqueId", 0),
        tail_number=raw.get("id", ""),
        name=raw.get("name", ""),
        avdb_status=raw.get("avdbStatus", ""),
        devices=[_parse_device(d) for d in raw.get("devices", [])],
    )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class FlyGarminAPI:
    """
    Client for the flyGarmin aviation database API.

    Requires a valid GarminAuth instance with a non-expired access token.
    """

    def __init__(self, auth: GarminAuth):
        self.auth = auth
        self._session = requests.Session()

    def _auth_headers(self) -> dict:
        if not self.auth.ensure_authenticated():
            raise GarminAPIError("Not authenticated — run 'avcardtool navdata login' first")
        return self.auth.get_auth_headers()

    def list_aircraft(self) -> List[Aircraft]:
        """Return all aircraft registered to this account."""
        resp = self._session.get(
            f"{FLYGARMIN_API}/aircraft/",
            params={
                "withAvdbs": "true",
                "withJeppImported": "true",
                "withSharedAircraft": "true",
            },
            headers=self._auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        aircraft_list = [_parse_aircraft(a) for a in (data if isinstance(data, list) else [])]
        logger.debug(f"Found {len(aircraft_list)} aircraft")
        return aircraft_list

    def list_files(self, series_id: int, issue_name: str) -> DatabaseIssueFiles:
        """Return download info for a specific database series + issue."""
        resp = self._session.get(
            f"{FLYGARMIN_API}/avdb-series/{series_id}/{issue_name}/files/",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        def _parse_file(raw: dict) -> DatabaseFile:
            return DatabaseFile(
                url=raw.get("url", ""),
                file_size=raw.get("fileSize", 0),
                destination=raw.get("destination"),
                subregion_id=raw.get("subregionID"),
            )

        result = DatabaseIssueFiles(
            issue_type=data.get("issueType", ""),
            total_file_size=data.get("totalFileSize", 0),
            main_files=[_parse_file(f) for f in data.get("mainFiles", [])],
            auxiliary_files=[_parse_file(f) for f in data.get("auxiliaryFiles", [])],
            removable_paths=data.get("removablePaths", []),
        )
        logger.debug(
            f"series {series_id}/{issue_name}: {len(result.main_files)} main + "
            f"{len(result.auxiliary_files)} aux files, {result.total_file_size:,} bytes total"
        )
        return result

    def create_batch_update(self, databases: List[BatchDatabase]) -> str:
        """
        Create a batch-update session on the fly.garmin.com server.

        POSTs the list of databases to download; returns the batch UUID
        extracted from the launchURL in the response.

        Args:
            databases: list of BatchDatabase entries (one per series/issue)

        Returns:
            Batch UUID string (used in subsequent GET and unlock calls)
        """
        payload = {
            "garminDatabases": [
                {
                    "seriesID": db.series_id,
                    "issueName": db.issue_name,
                    "authorizedDeviceIDs": db.device_ids,
                    "subregionIDs": db.subregion_ids,
                }
                for db in databases
            ]
        }
        resp = self._session.post(
            f"{FLYGARMIN_API}/batch-updates/",
            json=payload,
            headers=self._auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # launchURL looks like: "...?id=<uuid>" or ".../<uuid>/"
        launch_url = data.get("launchURL", "")
        qs = parse_qs(urlparse(launch_url).query)
        if "id" in qs:
            batch_id = qs["id"][0]
        else:
            # fall back: last non-empty path segment
            batch_id = [p for p in urlparse(launch_url).path.split("/") if p][-1]

        logger.debug(f"Batch update created: id={batch_id}")
        return batch_id

    def get_batch_update(self, batch_id: str) -> dict:
        """
        Retrieve the full batch-update plan from the server.

        Uses the v5 Accept header that Garmin's own client sends.

        Args:
            batch_id: UUID returned by create_batch_update()

        Returns:
            Raw JSON dict of the batch-update plan
        """
        resp = self._session.get(
            f"{FLYGARMIN_API}/batch-updates/{batch_id}/",
            headers={
                **self._auth_headers(),
                "Accept": "application/vnd.garmin.fly.batchupdate+json;v=5",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"Batch update plan retrieved for id={batch_id}")
        return data

    def unlock(
        self,
        series_id: int,
        issue_name: str,
        device_id: int,
        card_serial: str,
        batch_id: Optional[str] = None,
    ) -> dict:
        """
        Authorise download of a database issue for a specific device + SD card.

        Args:
            series_id:   Series.series_id
            issue_name:  Issue.name  (e.g. "2603")
            device_id:   Device.device_id
            card_serial: SD card volume serial (from SDCardDetector)
            batch_id:    If provided, use BatchUpdate authorization instead of Bearer
        """
        if batch_id:
            auth_header = {"Authorization": f'BatchUpdate id="{batch_id}"'}
        else:
            auth_header = self._auth_headers()

        resp = self._session.get(
            f"{FLYGARMIN_API}/avdb-series/{series_id}/{issue_name}/unlock/",
            params={
                "deviceIDs": device_id,
                "cardSerial": card_serial,
            },
            headers=auth_header,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def download_file(
        self,
        db_file: DatabaseFile,
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Download a single DatabaseFile to output_dir."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / db_file.file_name

        logger.info(f"Downloading {db_file.file_name} ({db_file.file_size:,} bytes)")

        resp = self._session.get(db_file.url, stream=True, timeout=600)
        resp.raise_for_status()

        total = db_file.file_size or int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)

        logger.info(f"Saved to {dest}")
        return dest
