#!/usr/bin/env python3
"""
Probe flyGarmin's unlock endpoint for every device/database on the account and
write a REDACTED description of each response, for comparing G3X vs GTN.

Run on the Raspberry Pi as the user avcardtool runs as (it needs that user's
stored Garmin login), e.g.:

    sudo -u <avcardtool user> /usr/local/bin/avcardtool-python scripts/unlock_probe.py
    # or, from a source checkout:  PYTHONPATH=src python3 scripts/unlock_probe.py

Options:
    --config FILE     avcardtool config (default: the usual lookup)
    --data-dir DIR    where garmin_tokens.json lives (default: the config's data_dir)
    --out FILE        write the report here too (default: ./unlock_probe.json)

Read-only: it only does GETs (aircraft list, file list, unlock). The unlock call
is the same one 'navdata auto-update' makes. Redacted output: key names, value
types and string lengths, `destination` paths, URL scheme+host+path (no query),
and the avdb type / unlockData filenames and versions. It never writes tokens,
serials, system IDs, tail numbers, device IDs or unlock-code values.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from avcardtool.core import Config
from avcardtool.navdata.garmin.api import FlyGarminAPI
from avcardtool.navdata.garmin.auth import GarminAuth

KEEP_VALUE = {"destination", "avdbType", "featureUnlockFilename", "avdbTypeVersion",
              "featureUnlockSlot", "featureUnlockFlag", "sendAuthorization", "hidden"}


def shape(value, key=""):
    """Structure of a JSON value with identifying content removed."""
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(v, key) for v in value]
    if key == "url" and isinstance(value, str):
        u = urlparse(value)
        return f"{u.scheme}://{u.netloc}{u.path}"
    if key in KEEP_VALUE:
        return value
    if isinstance(value, str):
        return f"<str len={len(value)}>"
    if value is None or isinstance(value, bool):
        return value
    return f"<{type(value).__name__}>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path)
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("--out", type=Path, default=Path("unlock_probe.json"))
    args = ap.parse_args()

    data_dir = args.data_dir
    if data_dir is None:
        try:
            data_dir = Path(Config(config_path=args.config).system.data_dir)
        except Exception as e:
            print(f"Could not read config ({e}); pass --data-dir", file=sys.stderr)
            return 2

    auth = GarminAuth(token_dir=data_dir)
    if not auth.is_authenticated():
        print(f"Not logged in (no valid tokens in {data_dir}). "
              f"Run 'avcardtool navdata login' as this user first.", file=sys.stderr)
        return 1
    api = FlyGarminAPI(auth)

    report = []
    for ac in api.list_aircraft():
        for dev in ac.devices:
            entry = {"device": dev.name, "productID": dev.product_id, "databases": []}
            report.append(entry)
            for avdb in dev.avdb_types:
                for s in avdb.series:
                    issue = (s.installable_issues or s.available_issues or [None])[0]
                    if issue is None:
                        continue
                    db = {"avdbType": avdb.name, "series": s.series_id, "issue": issue.name}
                    entry["databases"].append(db)
                    try:
                        f = api.list_files(s.series_id, issue.name)
                        db["files"] = [x.file_name for x in f.main_files + f.auxiliary_files]
                        db["removablePaths"] = f.removable_paths
                    except Exception as e:
                        db["listFilesError"] = str(e)[:200]
                    try:
                        db["unlock"] = shape(api.unlock(s.series_id, issue.name, dev.device_id))
                    except Exception as e:
                        db["unlockError"] = str(e)[:200]
                    print(f"{dev.name} / {avdb.name} {issue.name}: "
                          f"{'ok' if 'unlock' in db else db.get('unlockError')}", file=sys.stderr)

    text = json.dumps(report, indent=2)
    args.out.write_text(text)
    print(text)
    print(f"\nWrote {args.out.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
