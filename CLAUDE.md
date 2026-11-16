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
- Avionics firmware (G3X Touch GCD folder, GTN Xi Copy2Card loader card): `src/avcardtool/firmware/`,
  described in `ARCHITECTURE.md` ("Firmware Updates"). The automatic path wipes a GTN database card for
  firmware only when a version newer than that card serial's baseline exists (the baseline is the
  release current when the card is first seen, assumed installed); navdata is blocked for that serial
  until confirm/cancel; never reformat (the volume serial must survive). It never writes loader files
  to any other database/log card.
- Treat user/account identifiers (serials, device IDs, tail numbers, account names) as private; don't
  commit them or send them to outside services.
- Test Raspberry Pi access (SSH via Cloudflare tunnel, key, paths, ground rules): see `CLAUDE.local.md`
  (local only, not committed).
