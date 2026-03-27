# AVCardTool - Architecture

## Overview

AVCardTool merges two tools into one modular system:

1. **Flight Data Processor** — Processes flight logs and uploads to tracking services.
2. **Navigation Database Updater** — Downloads and installs Garmin aviation databases.

## Design Goals

1. **Modular Architecture**: Separate concerns to allow independent development.
2. **Manufacturer-Agnostic Flight Data**: Support multiple avionics manufacturers.
3. **Upstream Contribution**: Keep Garmin navdata code separate for potential upstreaming.
4. **Reliable Deployment**: Isolated Python environments for Linux/Raspberry Pi.
5. **Unified CLI**: Single command-line interface with subcommands.

## Project Structure

```
avcardtool/
├── src/avcardtool/
│   ├── core/                  # Config, logging, utilities
│   ├── flight_data/           # CSV parsing, analysis, uploaders
│   │   ├── analyzers/         # Hobbs, Tach, OOOI, flight detection
│   │   ├── processors/        # Garmin G3X CSV processor
│   │   └── uploaders/         # CloudAhoy, FlySto, SavvyAviation, etc.
│   └── navdata/
│       └── garmin/
│           ├── auth.py        # SSO + OAuth login flow
│           ├── api.py         # flyGarmin REST API client
│           ├── taw_parser.py  # TAW archive extraction
│           └── sdcard.py      # SD card detection and writing
├── systemd/                   # udev rules + systemd service template
├── install.sh                 # Install/upgrade script
├── package_deb.sh             # .deb packaging
└── .github/workflows/         # Release CI (wheel + .deb)
```

## Deployment

### Installation

AVCardTool installs into a per-user virtual environment at `/opt/avcardtool/venv`,
with a symlink at `/usr/local/bin/avcardtool`. Only two components require root:

- **udev rules** (`/etc/udev/rules.d/99-avcardtool-sdcard.rules`) — triggers on SD card insertion
- **systemd service** (`/lib/systemd/system/avcardtool-processor@.service`) — runs the processor

Everything else lives in user-space:

| Path | Purpose |
|------|---------|
| `~/.config/avcardtool/config.json` | User configuration |
| `~/.local/share/avcardtool/` | Data, logs, Garmin tokens |
| `/opt/avcardtool/venv/` | Python virtual environment |

### Updates

`avcardtool self-update` re-installs from the tagged GitHub release using pip's
`git+https://` syntax — no PyPI involvement. The version is embedded in
`src/avcardtool/__init__.py` and managed by `bump-my-version`. Pushing a version
bump to `main` triggers the release CI, which builds a wheel and a `.deb`.

## Data Flow

### SD Card Insertion (Automatic)

1. **udev** detects a `vfat` block device and starts a systemd service instance.
2. **systemd** executes `avcardtool auto-process /dev/sdX`.
3. **Flight Data**: deduplicates files, analyzes logs (Hobbs/Tach/OOOI), uploads.
4. **Navdata** (if `auto_download: true`): checks for newer cycles, downloads and writes to card.
5. Card is ready.

### Manual Navdata Update

```
avcardtool navdata login
avcardtool navdata list-databases
avcardtool navdata download
avcardtool navdata install <file.taw> [/media/sdcard]
```

## Garmin Navigation Database Download Flow

### Background — What Garmin's Own Client Does

Garmin Aviation Database Manager (the desktop app) communicates with
`https://fly.garmin.com/fly-garmin/api/`. Traffic observed via Fiddler shows
the following sequence:

#### 1. Authentication (SSO + OAuth)

```
GET  https://sso.garmin.com/sso/signin
     ?clientId=FLY_GARMIN_DESKTOP
     &embedWidget=true
     &gauthHost=https://sso.garmin.com/sso
     &service=https://fly.garmin.com
     ...
     → 200 HTML page with CSRF token in hidden input

POST https://sso.garmin.com/sso/signin  (same params)
     body: username=..., password=..., _csrf=...
     Origin: https://sso.garmin.com
     → 200 HTML "casEmbedSuccess" page containing:
         var response_url = "https:\/\/fly.garmin.com?ticket=ST-xxxxx";
         var service_url  = "https:\/\/fly.garmin.com";

POST https://services.garmin.com/api/oauth/token
     body: grant_type=service_ticket
           client_id=FLY_GARMIN_DESKTOP
           service_url=https://fly.garmin.com
           service_ticket=ST-xxxxx
     → { "access_token": "...", "refresh_token": "...", "expires_in": 3600 }
```

The Bearer token is then used for all subsequent flyGarmin API calls.
Tokens are refreshed with `grant_type=refresh_token` before expiry.

#### 2. Fetch Aircraft + Available Databases

```
GET /aircraft/
    ?withAvdbs=true
    &withJeppImported=true
    &withSharedAircraft=true
    Authorization: Bearer <token>
    → [ Aircraft ]
        └─ devices: [ Device ]
             device.id         (used in unlock call)
             device.systemId   (hardware identifier)
             └─ avdbTypes: [ AvdbType ]
                  avdbType.name   (e.g. "NavData", "Terrain", "Obstacle")
                  avdbType.status (Latest | Expired | NotInstalled)
                  └─ series: [ Series ]
                       series.id
                       series.region.name
                       series.installableIssues: [ Issue ]  ← entitled to write to card
                       series.availableIssues:   [ Issue ]  ← exists on server
```

An `Issue` has a `name` (cycle identifier, e.g. `"2603"` or `"20T1"`),
`effectiveAt`, `invalidAt`, and `rev`.

#### 3. Create a Batch-Update Session

Rather than downloading databases one at a time, Garmin's client batches them:

```
POST /batch-updates/
     Authorization: Bearer <token>
     Content-Type: application/json
     body: {
       "garminDatabases": [
         {
           "seriesID": 1298,
           "issueName": "20T1",
           "authorizedDeviceIDs": [12345],
           "subregionIDs": []
         },
         ...
       ]
     }
     → { "launchURL": "https://fly.garmin.com/...?id=<batch-uuid>", ... }
```

The `batch-uuid` is the session identifier for all subsequent calls.

#### 4. Retrieve the Full Update Plan

```
GET /batch-updates/<batch-uuid>/
    Authorization: Bearer <token>
    Accept: application/vnd.garmin.fly.batchupdate+json;v=5
    → full plan: which databases go to which device/card,
                 file lists, removable paths, etc.
```

#### 5. Unlock Each Database

For each database in the batch, Garmin sends an unlock request using the **batch
session authorization** (not the Bearer token):

```
GET /avdb-series/<seriesID>/<issueName>/unlock/
    ?deviceIDs=<device.id>
    &cardSerial=<sd-card-volume-serial>
    Authorization: BatchUpdate id="<batch-uuid>"
    → { "unlockCode": "...", ... }
```

Note: `cardSerial` is the volume serial number of the SD card (obtainable on
Linux via `lsblk -o SERIAL` or by reading the FAT32 volume ID bytes).

#### 6. Fetch File List and Download

```
GET /avdb-series/<seriesID>/<issueName>/files/
    → {
        "issueType": "TAW",
        "totalFileSize": 123456789,
        "mainFiles": [
          { "url": "https://...", "fileSize": 123456, "destination": "Garmin/..." }
        ],
        "auxiliaryFiles": [...],
        "removablePaths": ["Garmin/terrain/..."]
      }
```

Each file is downloaded directly from the signed URL (no extra auth header needed).
Files are TAW archives (Transfer Archive for Windows) — Garmin's proprietary
container format.

### What AVCardTool Does

AVCardTool mirrors this flow exactly:

| Step | Garmin Client | AVCardTool |
|------|--------------|------------|
| Auth | SSO → service ticket → Bearer token | `GarminAuth.login()` in `auth.py` |
| Token storage | In-memory / app storage | `~/.local/share/avcardtool/garmin_tokens.json` |
| Token refresh | Automatic via refresh token | `GarminAuth._refresh_tokens()` |
| List aircraft | `GET /aircraft/?withAvdbs=true` | `FlyGarminAPI.list_aircraft()` |
| Batch session | `POST /batch-updates/` | `FlyGarminAPI.create_batch_update()` |
| Update plan | `GET /batch-updates/{id}/` + v5 Accept header | `FlyGarminAPI.get_batch_update()` |
| Unlock | `GET /unlock/` with `BatchUpdate id=` header | `FlyGarminAPI.unlock(batch_id=...)` |
| File list | `GET /files/` | `FlyGarminAPI.list_files()` |
| Download | Stream from signed URL | `FlyGarminAPI.download_file()` |
| Install | TAW extraction + SD card write | `taw_parser.py` + `sdcard.py` (in progress) |

**Fallback behavior**: If `POST /batch-updates/` fails (network error, API change),
`avcardtool navdata download` falls back to direct Bearer-token unlock
(`Authorization: Bearer <token>`) for each database individually.

### TAW Format

TAW (Transfer Archive for Windows) is Garmin's proprietary distribution format.
Files contain a header identifying the database type and region, followed by
compressed database content. `taw_parser.py` handles extraction; output is
written to the correct subdirectory on the SD card as specified by the
`destination` field in the files/ response.

## Authentication Details

Token storage:

```
~/.local/share/avcardtool/garmin_tokens.json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "Bearer",
  "expires_at": 1234567890.0,
  "display_name": "username"
}
```

File is created with `chmod 600`. The directory is `chmod 700`.

`GarminAuth.ensure_authenticated()` is called before every API request — it
returns the cached token if still valid, refreshes it if expired, or returns
`False` if no refresh token is available (requiring a fresh `navdata login`).

## Core Module Features

### Dynamic Column Mapping

The flight data module reads Garmin's CSV header row and maps internal field
names to actual column names at runtime. This avoids brittle hardcoded indices
and survives avionics firmware updates that reorder or rename columns.

### Lazy Config Path Evaluation

`SystemConfig` uses `field(default_factory=lambda: ...)` so default paths
(data dir, log file) are evaluated when the config object is constructed, not
when the module is imported. This means the paths always resolve correctly for
the current user even when the module is loaded by root during installation.

### File Logging Fallback

If the configured log file path is not writable (e.g. `/var/log/` on a system
where the user lacks write permission), setup_logging silently falls back to
console-only logging at DEBUG level rather than raising an error.
