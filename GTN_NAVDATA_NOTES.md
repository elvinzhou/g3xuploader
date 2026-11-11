# GTN 6XX Xi navigation data: how GADM does it, what we do, what is unproven

Working notes for anyone touching navdata installs. The permanent description of the install
scheme is in [ARCHITECTURE.md](ARCHITECTURE.md#writing-a-card-the-way-gadm-does); this file keeps the
evidence, the open questions and the TODO list. It contains no account, tail-number, serial or
device identifiers; read those from the user's card / `avcardtool navdata list-databases`.

## Status

GTN navigation data and terrain now install the way Garmin's GADM does (signed `.sff` licenses from
the unlock response, `ldr_sys/nav_db2.bin`, `feat_unlk.dat` slots 0/2/14). This replaced the guard
that refused GTN nav data (commit d30da4d on the previous branch). **Not yet proven on a GTN display**: everything that can be
compared against a GADM-written card matches byte-for-byte (below), but no card written by this tool
has been put in a GTN. Test on a spare card with GADM at hand before trusting it.

## Verified against GADM-written cards (2026-11)

| Check | Result |
|---|---|
| `avtn_db.bin` / `nav_db2.bin` (TAW regions 0x01 / 0x02 of `jg600a-us-2611.taw`) vs GADM's | same sizes (5,848,373 / 7,376,989) and embedded CRCs |
| `feat_unlk.dat` slot 0 (NAVIGATION, security id 0x0402) and slot 14 (NAV_DB2) | rebuilt with our writer: byte-identical to GADM's |
| `feat_unlk.dat` G3X slots (obstacles, SafeTaxi, charts, airport directory, sectionals) | byte-identical when rebuilt with the G3X's System ID |
| `.evidf.dat` | identical (4 bytes, encoded volume serial) |
| `ldr_sys/grm_feat_key.zip` | identical on two different GTN cards (static) |
| `.sff` licenses | fetched from Garmin's unlock endpoint, never generated; they differ per card/cycle |
| File attributes | GADM: data files + `feat_unlk.dat` hidden/system/read-only; `.gadm.meta` hidden/read-only; `.evidf.dat`, `.sff`, key zip, `.jnx`, `.hif` plain. We match (applied with `fatattr`) |
| G3X install (`~/cards/test.sh` harness: 183 files from the real G3X TAWs into a copy of the pre-GADM card vs GADM's result) | every file identical (data files, `feat_unlk.dat`, `.evidf.dat`); only `.gadm.meta` differed (timestamp/timezone) |
| Live `navdata update` on a GTN card copy (nav from cache, terrain downloaded fresh) | `avtn_db.bin`, `nav_db2.bin`, both nav `.sff`, key zip byte-identical to GADM's files for the same cycle; `trn.dat` (2,064,107,073 B) and `trn.dat.sff` installed; slots 0/2/14 carry security id 0x0402, the unit's System ID and the files' CRCs; no warnings |
| Live `navdata update` on a G3X card copy | unlock returns no files; one file installed, `safetaxi.bin` identical to GADM's, G3X System ID used |

### What the unlock endpoint returns (probed 2026-11 with `scripts/unlock_probe.py`, redacted)

- **G3X Touch, every database:** `files: []`, `unlockData: []`, one `unlockCodes` entry.
- **GTN nav (series 2230):** `files` = `avtn_db.bin.sff` (`/unlock/sff/173/`), `nav_db2.bin.sff`
  (`/sff/174/`) on `fly.garmin.com`, `ldr_sys/grm_feat_key.zip` (`avdb.garmin.com/keys/grm_feat_key_1.17.zip`),
  all `sendAuthorization: true`. `unlockData`: v1 / slot 0 / `ldr_sys/avtn_db.bin`; v2 / slot 14 /
  `ldr_sys/nav_db2.bin`. `featureUnlockSlot * 913` is exactly our `feat_unlk.dat` offsets.
- **GTN terrain (series 1298, `tgtn-amrn-20T1.taw`)** is licensed too: `trn.dat.sff` (`/sff/200/`) + the
  same key zip; `unlockData`: v2 / slot 2 / flag 1 / `trn.dat`. `avdbTypeVersion` is **not** the TAW
  region id (trn.dat is region 0x23).
- Series the account doesn't own return 403.
- TAW region headers: the 4 skipped bytes are `00000000` (or a constant 5000 on a file's first region);
  they carry no version/slot. Region id -> filename pairing is therefore ours (`_licensed_region_paths`).

### `.sff` layout (decoded; 1039 bytes)

- 128-byte header: 4-byte word (differs per file), `000-00000-xx\0`, `0.00.00\0`, description text
  (`Navigation Data United States 2230/26..`, zero padded).
- DER from offset 0x80: `SEQ{ SEQ{INT 1, INT 1}, SEQ{SEQ{INT productID(0x0402), INT systemID}}, ENUM 0,
  SEQ{ SEQ{ ENUM 0, SEQ{ENUM 0, INT 0, INT <file size>}, SEQ{OID 1.3.6.1, OCTETSTRING(4)=CRC32 LE},
  SEQ{OID sha256, OCTETSTRING(32)}, SEQ{OID sha512, OCTETSTRING(64)} } } }`, then ~704 bytes of
  signature. Size/CRC match the TAW region; the signature binds the file to one unit (product code +
  system id), so only Garmin's server can produce it.

### TAW facts

- File lists are per device. G3X Touch: one `*g3x*` TAW per database. GTN: nav = `jg600a-us-<cycle>.taw`,
  terrain = `tgtn-amrn-20T1.taw`.
- TAW header `database_type` is the format family, not always the device model: `jg600a-*` is 0x01F2
  ("G600") although the GTN's product code is 0x0402 (productID 1026); `tgtn-*` is 0x0402. The card is
  signed with its own product code, so a header/card mismatch is accepted only when the unlock supplies
  licenses.

## Constraints (read before changing install code)

- Licenses are per unit and signed by Garmin: never cache them, never reuse one card's for another, and
  only send the bearer token to https `*.garmin.com`.
- The `feat_unlk.dat` slot layout and feature bits (`feat_unlk.Feature`) are hard-coded and were derived
  from GADM-written cards: the unlock's `featureUnlockFlag` is **not** the feature bit (nav_db2 has
  flag 0 but bit 10). A database outside that table installs with a warning (`_check_unlock_slots`).
- Region-id -> filename pairing is an inference (see above); it holds for GTN nav (0x01/0x02) and terrain
  (0x23). Re-check it with a TAW dump when Garmin adds a database.
- Never touch `Garmin/GarminDevice.xml`; leave other devices' `feat_unlk.dat` slots alone.
- A card's earlier mistakes are not auto-repaired: cards written by 1.14/1.15 of this tool can hold a stray
  `basemap.bin` (really the old nav_db2 under the wrong name) and a terrain slot signed with System ID 0.
  The next install rewrites the slots it owns; the stray file is left for the user to delete.

## TODO

1. Put a card written by this tool in a GTN (spare card, GADM at hand) and confirm it boots, accepts
   nav data and shows the right cycle. Until then GTN support is "matches GADM byte-for-byte".
2. Verify the GTN **terrain** slot (slot 2): the unlock says flag 1; we write feature bit 3 (as on G3X, and
   as on an old GTN card written earlier). Needs a GADM-written GTN terrain card (`trn.dat` + `trn.dat.sff`).
3. Derive `feat_unlk.Feature` slots/bits from data instead of the table once more cards are known
   (`featureUnlockSlot` gives the offset but not the bit). Add a GADM-written reference card per new
   avionics before declaring it supported.
4. Removable paths: GADM's list for GTN nav (`/ldr_sys/avtn_db.bin`, `/air_sport.gpi`, ...) does not
   mention `nav_db2.bin` or the `.sff` files; they are overwritten in place. Confirm GADM does not delete
   anything else, and consider offering cleanup of the stray `basemap.bin` left by older versions.
5. `.gadm.meta` `gadmLastInstalled`: GADM writes the PC's local time ("GMT-0800 (Pacific Standard Time)"); we write UTC ("GMT+0000 (Coordinated Universal Time)"). Cosmetic; use the Pi's timezone if it ever matters.
6. Decide whether `unlockCodes` (returned for every database, unused) matter for any avionics.
7. The unlock probe (`scripts/unlock_probe.py`) is the way to look at a new avionics: run it, compare the
   `files` / `unlockData` shape with the table in ARCHITECTURE.md.

## Code map

- Licenses and paths: `_unlock_for_install`, `_licensed_region_paths`, `_unlock_slots`,
  `_check_unlock_slots`, `_manifest_entries_from_cache`, `_missing_licenses_message` (`cli.py`);
  `FlyGarminAPI.download_unlock_files`, `peek_taw_database_type`, `peek_taw_feature_crcs`
  (`navdata/garmin/api.py`); `TAWExtractor.extract_to_directory(region_paths=...)` (`taw_parser.py`).
- Card identity: `_resolve_target_db_type` (avionics.txt -> feat_unlk NAVIGATION slot -> single type).
- Signing: `navdata/garmin/feat_unlk.py` (`Feature`, `update_feat_unlk`, `feat_unlk_checksum`).
- Attributes: `taw_parser._set_garmin_data_attrs` / `_set_garmin_meta_attrs` / `_set_plain`.
- Tests: `PYTHONPATH=src python -m pytest -q -o addopts="" tests` (needs `click`, `requests`, `pytest`);
  `tests/test_gtn_licenses.py` holds the GTN/unlock cases.
