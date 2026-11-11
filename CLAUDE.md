# g3xuploader (avcardtool)

Processes Garmin G3X flight logs and keeps Garmin avionics SD cards' databases current.

- Run tests: `PYTHONPATH=src python -m pytest -q -o addopts="" tests` (needs `click`, `requests`, `pytest`).
- Releases are automatic: pushes to `main` with Conventional Commit subjects (`fix:`, `feat:`) bump the
  version and publish (`.github/workflows/release.yml`). Don't bump versions by hand.
- Navdata installs write to real avionics cards. Don't change signing/`feat_unlk.dat`/`.sff` behaviour
  without checking it against a card written by Garmin's GADM.
- Card install follows what Garmin's unlock endpoint returns (license files, database filenames, slots),
  not per-device rules; keep new code generic. How it works and the `feat_unlk.dat`/attribute tables:
  `ARCHITECTURE.md` ("Writing a Card the Way GADM Does"). GTN 6XX Xi evidence, constraints and TODOs:
  `GTN_NAVDATA_NOTES.md`. Probe a new avionics with `scripts/unlock_probe.py`.
- Compare installs against GADM-written cards before changing signing/attributes.
- Treat user/account identifiers (serials, device IDs, tail numbers, account names) as private; don't
  commit them or send them to outside services.
- Test Raspberry Pi access (SSH via Cloudflare tunnel, key, paths, ground rules): see `CLAUDE.local.md`
  (local only, not committed).
