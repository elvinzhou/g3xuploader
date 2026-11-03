# AVCardTool

**Unified flight data processing and navigation database management for general aviation**

AVCardTool combines two essential capabilities for general aviation into one streamlined system:

1. **Flight Data Processing** - Automatically process flight logs, calculate Hobbs/Tach times, detect OOOI events, and upload to tracking services.
2. **Navigation Database Management** - Download and install aviation databases (NavData, Terrain, Obstacles, Charts) to SD cards.

Built for reliability on Raspberry Pi and other Linux systems, AVCardTool is designed to run as a background service triggered by SD card insertions.

## Features

- **Automated Workflow**: Insert an SD card, and it handles everything from log uploads to database updates.
- **Poetry-Backed**: Strictly locked dependencies for reproducible environments.
- **Robust Garmin SSO**: Direct integration with flyGarmin (Aviation flow) with MFA support.
- **Automatic SD Card Detection**: Triggered by udev, processed by systemd.
- **Cards Stay Current**: A daily systemd timer re-checks flyGarmin so a card left in the reader picks up new cycles automatically.
- **Email Notifications**: A summary email after each processing run (final Hobbs/Tach times, upload results) and whenever new database cycles land on a card — plus alerts when something needs attention (e.g. an expired flyGarmin login).
- **Manufacturer-Agnostic**: Modular design to support various avionics (Garmin G3X, etc.).

## Installation

The recommended way to install AVCardTool is using the provided installation script, which sets up a dedicated virtual environment, system integration, and then automatically launches the setup wizard.

### One-Line Install

```bash
curl -sSL https://raw.githubusercontent.com/elvinzhou/g3xuploader/main/install.sh | sudo bash
```

The installer will:
1. Install system dependencies (`python3-venv`, `udev`, `fatattr`).
2. Create a virtual environment in `/opt/avcardtool/venv`.
3. Install `avcardtool` and its dependencies.
4. **Launch the interactive setup wizard** (new installs only).
5. Set up `udev` rules for automatic SD card detection.
6. Install the `systemd` services for background processing.

Re-running the installer on an existing installation upgrades the package and system files but skips the wizard to avoid overwriting your existing configuration.

## Setup Wizard

The setup wizard runs automatically at the end of a fresh install. You can also run it any time with:

```bash
avcardtool setup
```

The wizard covers five sections. Each major feature is opt-in — only its follow-up questions are shown if you enable it.

### System Settings

| Prompt | Default | Description |
|--------|---------|-------------|
| Data directory | `~/.local/share/avcardtool` | Where logs, tokens, and debug payloads are stored |
| Enable debug mode | No | Saves the exact payload sent to each service into `<data_dir>/debug/` before every upload, even if credentials aren't configured yet |

### Flight Log Processing

> **Enable automatic flight log processing?** *(default: Yes)*

If enabled, avcardtool will parse G3X flight logs, calculate Hobbs and Tach times, detect OOOI events, and upload to your chosen services whenever an SD card is inserted.

**Log processing settings** (only shown if enabled):

| Prompt | Options | Default | Description |
|--------|---------|---------|-------------|
| Hobbs time trigger | `oil_pressure`, `rpm`, `flight_time` | `oil_pressure` | What starts the Hobbs clock. `oil_pressure` / `rpm` count while the engine runs; `flight_time` counts only while airborne. **Match your avionics' Airframe Time setting** — see [Matching Hobbs and Tach to your avionics](#matching-hobbs-and-tach-to-your-avionics) |
| Tach time mode | `variable`, `fixed` | `variable` | The G3X's Engine Time *Record Mode*. `variable` accrues time at RPM ÷ Standard Recording RPM; `fixed` runs 1:1 with the clock whenever the engine is above the minimum RPM |
| Standard Recording RPM | integer | `2300` | `variable` mode only. The RPM at which 1 tach hour accrues per hour — copy it from the G3X's Engine Time setup. **Not redline** — see [Matching Hobbs and Tach to your avionics](#matching-hobbs-and-tach-to-your-avionics) |

> ⚠ **These settings must match your avionics' configuration** (on a G3X:
> Engine/Airframe configuration → *Airframe Time* and *Engine Time*). When they
> don't, avcardtool's Hobbs and Tach drift from the avionics' own numbers.

**Upload services** (only shown if flight processing is enabled):

You are asked whether to enable each service. Only enabled services will receive uploads when your SD card is inserted.

- **CloudAhoy** — API token (Bearer token from your CloudAhoy account)
- **FlySto** — an API token, created at <https://www.flysto.net/account/api> (sent as a Bearer token). Before uploading, FlySto's logs API is queried for its most recent log, and only flights dated (UTC) after that day are uploaded — this assumes the SD card is loaded about once a day. Set `check_duplicates: false` under the flysto uploader to turn this off. Assumptions and trade-offs are logged in [FLYSTO_UPLOAD_DESIGN.md](FLYSTO_UPLOAD_DESIGN.md). Legacy OAuth settings (client ID/secret, `flysto-auth`) still work if no token is set.
- **Savvy Aviation** — No credentials required. Files are staged locally at `<data_dir>/savvy_staging/` for manual upload via their website.
- **Carryd** — API key (format: `eal_...`, obtained from the Carryd dashboard under **Settings → API Keys**) and an optional comma-separated list of engine logbook UUIDs (found under **Logbook → Settings**). Omit logbook UUIDs to update total airframe time only.

**First-run behavior** (only shown if flight processing is enabled):

AVCardTool tracks every processed file by hash. New users typically have months of existing flight history on their SD card. Since services like FlySto do not deduplicate, uploading everything at once would flood your account with old flights.

> **Mark all existing files as already processed on first SD card use?** *(Recommended: Yes)*

- **Yes** — on the very first SD card insertion, all files already on the card are marked as historical and skipped. Only flights recorded *after* setup will be uploaded.
- **No** — all files on the card are processed and uploaded on first insertion.

This only affects the first insertion. If a processed-files database already exists (e.g. on a re-install), this setting has no effect.

### Navigation Database Auto-Update

> **Enable automatic navigation database updates?** *(default: Yes)*

If enabled, avcardtool will check for and install updated Garmin NavData, terrain, obstacle, and chart databases whenever an SD card is inserted.

If you say Yes, the wizard prompts for your flyGarmin credentials and logs in immediately:

| Prompt | Description |
|--------|-------------|
| flyGarmin email | Your Garmin SSO email |
| flyGarmin password | Used only to obtain a session token — the password itself is not stored |
| MFA code | If your account has multi-factor authentication enabled |

On success, a session token is saved to `<data_dir>/` and used for all future database downloads. You can re-authenticate at any time with `avcardtool navdata login`.

### Email Notifications

> **Enable email notifications?** *(default: No)*

If enabled, avcardtool emails you after each automatic run: flight-log
summaries with the final Hobbs/Tach times and per-service upload results,
navdata install reports (old cycle → new cycle, effective dates), and
alerts when something needs attention — most importantly when the
flyGarmin login expires and automatic updates would otherwise stop
silently.

| Prompt | Default | Description |
|--------|---------|-------------|
| SMTP server | `smtp.gmail.com` | Any SMTP provider works |
| SMTP port | `587` | 587 = STARTTLS, 465 = implicit TLS |
| SMTP username | — | Usually your email address; leave empty for localhost relays (no auth) |
| SMTP password | — | For Gmail accounts with 2FA, use an [App Password](https://myaccount.google.com/apppasswords) |
| From / To addresses | username | Recipients accept a comma-separated list |

The wizard offers to send a test email immediately so you know delivery
works before the first real notification. You can re-test any time with
`avcardtool notify-test`. Severity is encoded in the subject prefix
(`[AVCardTool]`, `[AVCardTool WARNING]`, `[AVCardTool ERROR]`) for easy
inbox filtering.

### Saving Configuration

The wizard confirms where to write the config file (default: `~/.config/avcardtool/config.json` for non-root, `/etc/avcardtool/config.json` when running as root) and warns before overwriting an existing file.

The installer then reads the saved config and installs only the systemd services and udev rules needed for the features you enabled — if you enabled neither automated feature, no system-level rules are installed.

## Matching Hobbs and Tach to your avionics

### Where do the Hobbs and Tach numbers come from?

Every G3X log starts with the avionics' own counters in its header:

```
#airframe_info,...,airframe_hours="161.6",engine_hours="167.5"
```

Those are authoritative, and avcardtool uses them whenever it has them: each
flight starts from the hours in its own log's header, and when the newest log
on the card is a power-up without a flight, its header replaces the newest
flight's calculated end-of-flight values outright.

avcardtool only **estimates** time for the part the avionics hasn't recorded
yet — typically the newest flight on a card that was pulled right after
landing. That estimate is where the Hobbs trigger and Standard Recording RPM
come in, so they should count time the same way the avionics does.

### What happens when the settings don't match?

The estimate drifts from what the avionics records. Examples from 31 single
flights on a real G3X Touch card (N662EZ), compared with the hours the G3X
itself wrote in the next log's header:

**Hobbs.** That G3X's Airframe Time counts only airborne time:

| Hobbs trigger | avcardtool total | G3X total | Difference | Flights within 0.1 h |
|---|---|---|---|---|
| `oil_pressure` (default) | 47.05 h | 40.4 h | **+6.65 h (+16%)** | 5 / 31 |
| `rpm` | 46.99 h | 40.4 h | +6.59 h | 5 / 31 |
| `flight_time` | 39.40 h | 40.4 h | −1.00 h (−2.5%) | 28 / 31 |

Counting from engine start adds taxi and run-up time the G3X doesn't count, so
Hobbs was **overestimated** on almost every flight (by up to 0.5 h).

**Tach.** The G3X accrues engine time at RPM ÷ *Standard Recording RPM*
(2350 on that aircraft); it is not redline:

| Standard Recording RPM | avcardtool total | G3X total | Difference | Flights within 0.1 h |
|---|---|---|---|---|
| 2700 (redline) | 36.82 h | 43.3 h | **−6.48 h (−15%)** | 13 / 31 |
| 2350 (the G3X's setting) | 42.30 h | 43.3 h | −1.00 h (−2.3%) | 28 / 31 |

Using redline **underestimated** tach by about 15%. With matching settings
both land within the G3X's 0.1 h display resolution on nearly every flight.

### Which settings does my G3X use?

On the G3X, look under Engine/Airframe configuration:

- **Airframe Time** — what starts the airframe (Hobbs) counter. If it counts
  flight time, use `flight_time`; if it counts while the engine runs, use
  `oil_pressure` or `rpm`.
- **Engine Time** — *Record Mode* (`variable` or `fixed`) and, for variable,
  the *Standard Recording RPM*.

To change them later, rerun `avcardtool setup`, or edit `flight_data.airframe_time.trigger`
and `flight_data.engine_time.reference_rpm` in the config file.

### What if the estimate ends up higher than the avionics' number?

It corrects itself. Each flight's values start from the hours in its own log's
header, so the next upload is the avionics' real number plus the new flight —
on top of the truth, not on top of the earlier estimate. Replaying the logs
above as if the card were read after every single flight, no upload ever went
backwards.

The one edge case: if the next flight is shorter than the earlier
over-estimate, its total would be *lower* than what Carryd already has. Carryd
refuses uploads that move times backwards, so it keeps the slightly high value
(avcardtool reports "Carryd already has newer times") and the following flight
corrects it. A slightly high value is the conservative direction for
maintenance — items come due a little early, never late. Matching settings
keep the over-estimate to about 0.1 h.

## Architecture

AVCardTool is designed with a modular, manufacturer-agnostic architecture. It keeps Garmin-specific logic separate to allow for easy upstream contributions to projects like `jdmtool`.

For deep technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Usage

### Command-Line Interface

```bash
# Post-install setup / re-configure
avcardtool setup                          # Interactive setup wizard

# Flight data commands
avcardtool flight analyze LOG_FILE        # Analyze single log
avcardtool flight upload LOG_FILE         # Upload a single log to enabled services
avcardtool flight flysto-auth <code>      # Legacy OAuth only (not needed with an API token)
avcardtool flight list-processors         # List supported formats
avcardtool flight list-uploaders          # Show configured upload services

# Navigation database commands
avcardtool navdata login                  # Authenticate with flyGarmin (MFA supported)
avcardtool navdata list-databases         # List available databases
avcardtool navdata download all           # Download subscribed databases
avcardtool navdata install DB.taw         # Install to SD card

# Configuration
avcardtool config show                    # Print current configuration
avcardtool config validate                # Validate configuration file

# Notifications
avcardtool notify-test                    # Send a test notification email

# Automatic processing (used by systemd)
avcardtool auto-process [DEVICE]          # Process both flight data and navdata
```

### Automatic Processing

When an SD card is inserted:
1. **Udev** detects the card.
2. **Systemd** starts `avcardtool-processor@.service` and `avcardtool-navdata@.service`.
3. **Flight logs** are parsed, analyzed (Hobbs/Tach/OOOI), and uploaded.
4. **Navdata updates** are automatically checked and installed.
5. **Notifications** (if enabled) email you the results.

Cards left in the reader stay current: `avcardtool-navdata-check.timer`
re-runs the navdata check daily at ~03:00 across all mounted cards
(`Persistent=true` catches up after a power-off), so a new cycle published
overnight is installed before a morning flight.

View logs:
```bash
journalctl -u 'avcardtool-*' -f
```

### Testing with a Real Card (Dry Run)

The global `--dry-run` flag runs the real pipeline against a real card but
writes nothing:

- **SD card** — mounted read-only if avcardtool mounts it; nothing is written
  (`navdata install` refuses outright under `--dry-run`).
- **Uploads** — each uploader still checks its credentials and saves the exact
  payload to `<data_dir>/debug/`, then stops before any network request
  (FlySto doesn't refresh its token; Savvy doesn't copy to staging).
- **State** — `processed_files.json` is read and dedup behaves normally within
  the run, but changes stay in memory; `carryd_state.json` is not updated.
- **Notifications** — printed to the terminal, with who they'd go to (or why
  they wouldn't be sent). Add `--send-notifications` to really send them, with
  the subject tagged `[DRY RUN]`.
- **Navdata** — `navdata auto-update` reads the card and asks flyGarmin what
  it would install, then reports the plan (and prints the "installed" email).
  It skips the unlock, which can use up a subscription credit, the downloads
  and the install.

Because nothing is saved, you can run the same scenario as many times as you
like with the card left in the reader:

```bash
lsblk -f                                   # find the card, e.g. /dev/sda1

# What would happen on the next insert (uses the real processed_files.json)
avcardtool --dry-run auto-process /dev/sda1

# Treat every log on the card as new (also skips first-run historical marking)
avcardtool --dry-run auto-process /dev/sda1 --force

# Check the emails themselves against real data
avcardtool --dry-run --send-notifications auto-process /dev/sda1 --force

# What navdata auto-update would install on this card
avcardtool --dry-run navdata auto-update /dev/sda1
```

To try a specific history, point `--data-dir` at a scratch copy (never saved
to the config) and edit its state:

```bash
cp -r ~/.local/share/avcardtool /tmp/scenario

# Brand-new install: first run marks everything historical
avcardtool --dry-run --data-dir /tmp/empty auto-process /dev/sda1

# "Forget" one log so it's treated as the newest unseen flight
jq '.processed |= with_entries(select(.value.filename != "log_20260327_152821_KOAK.csv"))' \
  /tmp/scenario/processed_files.json > /tmp/p.json && mv /tmp/p.json /tmp/scenario/processed_files.json
avcardtool --dry-run --data-dir /tmp/scenario auto-process /dev/sda1
```

To exercise the full udev → systemd path, set `"dry_run": true` under `system`
in the config and re-trigger the insert without pulling the card:

```bash
sudo systemctl start avcardtool-processor@sda1.service   # flight logs only
# or replay the whole insert (also starts avcardtool-navdata@sda1):
sudo udevadm trigger --action=add --sysname-match=sda1
journalctl -u 'avcardtool-*' -f
```

`system.dry_run` applies to every command, navdata included, so remove it
when you're done or the Pi will stop uploading and updating.

## Development

AVCardTool uses **Poetry** for dependency management. Releases ship as a
wheel packaged into a `.deb`.

### Setup

```bash
git clone https://github.com/elvinzhou/g3xuploader.git
cd g3xuploader
poetry install
```

### Run Tests

```bash
poetry run pytest
```

### Build Packages

```bash
poetry build              # wheel + sdist in dist/
bash package_deb.sh       # .deb from the wheel (needs dpkg-dev)
```

A standalone single-file binary can be built with PyInstaller (a dev
dependency), using `avcardtool_wrapper.py` as the entry point:

```bash
poetry run pyinstaller --onefile --name avcardtool --paths src \
  --collect-submodules avcardtool avcardtool_wrapper.py
./dist/avcardtool --version
```

### Releasing

Releases are automatic. Every push to `main` runs
[`release.yml`](.github/workflows/release.yml), which works out the next
version from the [Conventional Commit](https://www.conventionalcommits.org)
subjects since the last `v*` tag:

| Commit subject | Release | Example |
|----------------|---------|---------|
| `feat: ...` | minor | 1.8.5 → 1.9.0 |
| `fix:`, `perf:`, `refactor:`, `revert:`, `build:`, `deps:` | patch | 1.8.5 → 1.8.6 |
| `feat!: ...`, or `BREAKING CHANGE:` in the body | major | 1.8.5 → 2.0.0 |
| `docs:`, `chore:`, `ci:`, `test:`, `style:` | none | — |
| anything else (e.g. `navdata: ...`) | patch | 1.8.5 → 1.8.6 |

A scope is allowed, e.g. `fix(carryd): ...`. The largest bump among the
commits wins. When a release is due, the workflow runs the tests, bumps the
version with `bump-my-version` (`pyproject.toml`, `__init__.py`,
`install.sh`), pushes a `Bump version: X → Y` commit and `vY` tag to `main`,
builds the `.deb`, and publishes a GitHub Release. The release notes list
features and fixes grouped from the commit subjects. Installed Pis pick it up
via `avcardtool self-update`.

Don't bump versions by hand. To preview what the next release would be:

```bash
python3 scripts/release_version.py
```

To force a release or a specific bump level, run the **Release** workflow
manually from the Actions tab and choose `patch`, `minor` or `major`.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

Not affiliated with Garmin. Always verify flight data and databases through official methods before flight. Use at your own risk.
