# g3xuploader (avcardtool)

Processes Garmin G3X flight logs and keeps Garmin avionics SD cards' databases current.

- Run tests: `PYTHONPATH=src python -m pytest -q -o addopts="" tests` (needs `click`, `requests`, `pytest`).
- Releases are automatic: pushes to `main` with Conventional Commit subjects (`fix:`, `feat:`) bump the
  version and publish (`.github/workflows/release.yml`). Don't bump versions by hand.
- Navdata installs write to real avionics cards. Don't change signing/`feat_unlk.dat`/`.sff` behaviour
  without checking it against a card written by Garmin's GADM.
- In-progress GTN 6XX Xi navigation-data work (what's known, what's unproven, next steps):
  see `GTN_NAVDATA_NOTES.md`.
- Treat user/account identifiers (serials, device IDs, tail numbers, account names) as private; don't
  commit them or send them to outside services.
