# GTN 6XX Xi navigation data: findings and plan

Working notes so a new session can pick this up with a real GTN SD card attached.
Nothing here contains account, tail-number, serial or device identifiers; read those
from the user's card / `avcardtool navdata list-databases` when needed.

## Status (as of release 1.15.0)

- **Released 1.15.0 contains a GTN install path that is probably wrong.** It installs the
  GTN's `jg600a-*.taw` and signs only `feat_unlk.dat`. Garmin's tool (GADM) now installs GTN
  navigation data with signed `.sff` licenses instead (see below).
- **Guard (unmerged at time of writing):** branch `claude/relaxed-shannon-2z5f9l`, commit
  "fix(navdata): refuse to install GTN navigation data until its signed licenses can be made".
  A TAW whose header avionics type differs from the card's is not downloaded/installed; the run
  records an ERROR (`navdata_update_failed` email) telling the user to use GADM. Merge it (→ 1.15.1)
  before relying on 1.15.0 on a GTN card.
- GTN **terrain** (`tgtn-*.taw`, header type == card type) still goes through the normal
  `feat_unlk.dat` path; the CRC pre-check skips it when the card already has that exact file.

## What Garmin serves

- File lists are per device: for each installable database Garmin lists exactly that device's
  files (identical with or without `?deviceIDs=`). G3X Touch: one `*g3x*` TAW per database.
  GTN 6XX Xi: Navigation Data = `jg600a-us-<cycle>.taw`, Terrain = `tgtn-amrn-20T1.taw`.
- TAW header (`database_type`) is the *format family*, not always the device model:
  `jg600a-*` header type `0x01F2` ("G600", "US Garmin Navigation Database") although the GTN's
  product code is `0x0402` (productID 1026). `tgtn-*` terrain header is `0x0402`.
- `jg600a-us-2611.taw` regions: `0x01` -> `ldr_sys/avtn_db.bin` (5,848,373 bytes, valid embedded
  checksum) and `0x02` (7,376,989 bytes), which our extractor names `basemap.bin` but the GTN
  licenses call **`nav_db2.bin`**.
- flyGarmin's own update page lists exactly `jg600a-us-2611.taw` for the GTN device, so the file
  choice is right; installing it is the unsolved part.

## Two install schemes on GTN cards

1. **Older GADM (e.g. the cycle before 2611):** `feat_unlk.dat` NAVIGATION slot (offset 0) signed
   with security_id = device product code `0x0402`, plus `ldr_sys/avtn_db.bin`. Verified: rebuilding
   that slot with `update_feat_unlk(..., security_id=0x0402, ...)` is byte-identical to the slot
   GADM wrote (read-only check against a real GTN card).
2. **Current GADM (cycle 2611, Nov 2026):** wrote these files (no big data files were captured):
   - `.evidf.dat` (4 bytes, encoded volume id, same as our writer), `.gadm.meta` (JSON: id,
     account, last-installed time, avionics name, tail number, ...)
   - `avtn_db.bin.sff` and `nav_db2.bin.sff` at the card root, ~1 KB each
   - `ldr_sys/grm_feat_key.zip` (static Garmin key file; zip member `grm_feat_key_000.bin`,
     5,660 bytes, dated 2020; starts `c9 7d c3 0d` then `006-D7264-K0`)

### `.sff` layout (decoded; 1039 bytes)

- 128-byte header: 4-byte word (differs per file), then `000-00000-xx\0`, `0.00.00\0`, description
  text (`Navigation Data United States 2230/26..`, fixed width, zero padded).
- DER from offset 0x80: `SEQ{ SEQ{INT 1, INT 1}, SEQ{SEQ{INT productID(0x0402), INT systemID}},
  ENUM 0, SEQ{ SEQ{ ENUM 0, SEQ{ENUM 0, INT 0, INT <file size>}, SEQ{OID 1.3.6.1, OCTETSTRING(4)=CRC32 LE},
  SEQ{OID sha256, OCTETSTRING(32)}, SEQ{OID sha512, OCTETSTRING(64)} } } }`
- then ~704 bytes of signature material. File size/CRC match the TAW regions exactly
  (`avtn_db.bin.sff` <-> region 0x01, `nav_db2.bin.sff` <-> region 0x02; CRC-32 = last 4 bytes of the region).
- The signature binds the data file to one unit (product code + system id). It cannot be generated
  locally; Garmin's server provides it.

## Where the licenses come from (unlock response)

`GET /fly-garmin/api/avdb-series/<series>/<issue>/unlock/?deviceIDs=<id>` (what
`FlyGarminAPI.unlock()` calls; our code only reads `unlockCodes`). Response shape for GTN nav data
(series 2230, current issue), from field names and string lengths:

- `files`: 3 entries `{url, destination, sendAuthorization: true, hidden: false}` ->
  two `.sff` licenses (destination 15 chars, i.e. `avtn_db.bin.sff` / `nav_db2.bin.sff`) and
  `ldr_sys/grm_feat_key.zip` (24 chars). `sendAuthorization: true` means the download needs the
  bearer token.
- `unlockCodes`: `[{deviceID, unlockCode (base64, 15 bytes), gma: null, gca: null}]`
- `unlockData`: 2 entries, `avdbType` "Navigation Data", `avdbTypeVersion` 1 and 2,
  `featureUnlockSlot` 0 and 14, `featureUnlockFlag` 0, `featureUnlockFilename` 19 chars each
  (probably `ldr_sys/avtn_db.bin` and `ldr_sys/nav_db2.bin`).

Not yet confirmed: the exact destinations/URLs, and whether feat_unlk slots 0/14 are also written.

## Next steps (needs the GTN card + a GADM-written card)

1. Print the unlock response's `files[].destination`, URL host+path (no query) and the full
   `unlockData` (`avcardtool` venv python; example script was used in the session: call
   `api.unlock(series, issue, device_id)` and print those fields only).
2. `ls -laR` a card GADM just wrote for the GTN (root, `ldr_sys`, `Garmin`): confirm file names and
   sizes (`nav_db2.bin`? `basemap.bin`?), and whether `feat_unlk.dat` exists/changed.
3. Implement: after `unlock`, download `files[]` (send the bearer token when `sendAuthorization`) to
   their destinations on the card; extract region 0x02 as the unlock data's filename (not
   `basemap.bin`); write `.evidf.dat`/`.gadm.meta` as already done; decide about feat_unlk slots from step 2.
4. Verify against the GADM-written card (byte-compare small files; compare data file sizes/CRCs with the TAW).
5. Only then remove the guard (`_taw_header_mismatches` in `src/avcardtool/cli.py`). Whether a GTN
   accepts an install made by this tool is still unproven: test on a spare card with GADM ready.

## Code map

- File choice/guard: `_taw_header_mismatches`, `_unsupported_install_message` (`cli.py`);
  `FlyGarminAPI.peek_taw_database_type`, `peek_taw_feature_crcs` (`navdata/garmin/api.py`).
- Card identity: `_resolve_target_db_type` (avionics.txt -> feat_unlk NAVIGATION slot -> single type).
- Signing: `navdata/garmin/feat_unlk.py` (`update_feat_unlk`, `feat_unlk_checksum` = zlib CRC-32 variant).
- Extraction: `navdata/garmin/taw_parser.py` (`TAW_REGION_PATHS`, large raw regions stream in chunks).
- Tests: `PYTHONPATH=src python -m pytest -q -o addopts="" tests` (needs `click`, `requests`, `pytest`).
