"""
Unified command-line interface for aviation tools.

Provides subcommands for:
- Flight data processing (flight)
- Navigation database management (navdata)
- Configuration management (config)
- Automatic processing (auto-process)
"""

import sys
import json
from pathlib import Path
from typing import Optional
import click
import logging


def _prompt_password(prompt: str = "Password: ") -> str:
    """Prompt for a password, displaying * for each character typed."""
    import termios
    import tty

    sys.stdout.write(prompt)
    sys.stdout.flush()

    chars = []
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                break
            elif ch in ('\x7f', '\x08'):  # backspace / delete
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif ch == '\x03':  # Ctrl-C
                raise KeyboardInterrupt
            else:
                chars.append(ch)
                sys.stdout.write('*')
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    sys.stdout.write('\n')
    return ''.join(chars)

from avcardtool import __version__
from avcardtool.core import Config, setup_logging

logger = logging.getLogger(__name__)


# ============================================================================
# Main CLI Group
# ============================================================================

@click.group()
@click.version_option(version=__version__, prog_name="avcardtool")
@click.option(
    '--config',
    type=click.Path(path_type=Path),
    help='Path to configuration file'
)
@click.option(
    '--verbose', '-v',
    is_flag=True,
    help='Enable verbose logging'
)
@click.pass_context
def cli(ctx, config: Optional[Path], verbose: bool):
    """
    Aviation Tools - Unified flight data and navigation database management.

    Process flight logs from various manufacturers and manage aviation databases
    all from one convenient command-line interface.
    """
    # Initialize context object
    ctx.ensure_object(dict)

    # Load configuration
    try:
        ctx.obj['config'] = Config(config_path=config)
    except Exception as e:
        click.echo(f"Warning: Could not load configuration: {e}", err=True)
        ctx.obj['config'] = Config()

    # Setup logging
    log_level = 'DEBUG' if (verbose or ctx.obj['config'].system.debug) else ctx.obj['config'].system.log_level
    setup_logging(
        log_file=ctx.obj['config'].system.log_file,
        log_level=log_level
    )


# ============================================================================
# Flight Data Command Group
# ============================================================================

@cli.group()
@click.pass_context
def flight(ctx):
    """
    Flight data processing commands.

    Process flight logs, calculate Hobbs/Tach times, detect OOOI events,
    and upload to tracking services.
    """
    pass


@flight.command('process')
@click.argument(
    'path',
    type=click.Path(exists=True, path_type=Path),
    required=False
)
@click.option(
    '--processor',
    type=str,
    help='Force specific processor (e.g., garmin_g3x)'
)
@click.pass_context
def flight_process(ctx, path: Optional[Path], processor: Optional[str]):
    """
    Process flight data from SD card or directory.

    If PATH is not provided, attempts to auto-detect SD card.
    """
    click.echo("Flight data processing functionality coming soon...")
    click.echo(f"Would process: {path or 'auto-detect'}")
    if processor:
        click.echo(f"Using processor: {processor}")


@flight.command('analyze')
@click.argument(
    'log_file',
    type=click.Path(exists=True, path_type=Path)
)
@click.option(
    '--json',
    'output_json',
    is_flag=True,
    help='Output results as JSON'
)
@click.pass_context
def flight_analyze(ctx, log_file: Path, output_json: bool):
    """
    Analyze a single flight log file.

    Calculates Hobbs/Tach times and detects OOOI events without uploading.
    """
    from avcardtool.flight_data import PROCESSORS, FlightDataAnalyzer

    cfg = ctx.obj['config']

    # Try to find a processor that can handle this file
    processor = None
    for ProcessorClass in PROCESSORS:
        proc = ProcessorClass()
        if proc.detect_log_format(log_file):
            processor = proc
            click.echo(f"Detected format: {proc.get_name()}")
            break

    if not processor:
        click.echo(f"Error: Unable to detect log format for {log_file}", err=True)
        click.echo("Supported formats: Garmin G3X Touch (.csv)", err=True)
        sys.exit(1)

    try:
        # Parse the log file
        click.echo(f"Parsing: {log_file}")
        flight_data = processor.parse_log(log_file)

        # Analyze the flight
        analyzer = FlightDataAnalyzer(cfg.flight_data)

        if output_json:
            # JSON output
            result = analyzer.analyze_summary(flight_data)
            click.echo(json.dumps(result, indent=2))
        else:
            # Human-readable output
            analysis = analyzer.analyze(flight_data)

            click.echo("\n" + "=" * 60)
            click.echo(f"Flight Analysis: {log_file.name}")
            click.echo("=" * 60)
            click.echo(f"Aircraft: {analysis.aircraft_ident}")
            click.echo(f"Date: {analysis.date}")
            click.echo(f"Data Points: {analysis.detection.data_points}")

            if analysis.detection.is_flight:
                click.echo(f"\n✓ Flight Detected")
                click.echo(f"  Airborne Time: {analysis.detection.airborne_time_minutes:.1f} minutes")
                click.echo(f"  Max Speed: {analysis.detection.max_ground_speed_kts:.1f} kts")
                click.echo(f"  Altitude Change: {analysis.detection.altitude_change_ft:.0f} ft")

                if analysis.hobbs:
                    click.echo(f"\nHobbs Time:")
                    click.echo(f"  Start: {analysis.hobbs.starting_hours:.2f} hours")
                    click.echo(f"  Increment: +{analysis.hobbs.increment_hours:.2f} hours")
                    click.echo(f"  End: {analysis.hobbs.ending_hours:.2f} hours")

                if analysis.tach:
                    click.echo(f"\nTach Time:")
                    click.echo(f"  Start: {analysis.tach.starting_hours:.2f} hours")
                    click.echo(f"  Increment: +{analysis.tach.increment_hours:.2f} hours")
                    click.echo(f"  End: {analysis.tach.ending_hours:.2f} hours")

                if analysis.oooi:
                    click.echo(f"\nOOOI Times:")
                    if analysis.oooi.out_time:
                        click.echo(f"  Out (Engine Start): {analysis.oooi.out_time.strftime('%H:%M:%S')}")
                    if analysis.oooi.off_time:
                        click.echo(f"  Off (Takeoff): {analysis.oooi.off_time.strftime('%H:%M:%S')}")
                    if analysis.oooi.on_time:
                        click.echo(f"  On (Landing): {analysis.oooi.on_time.strftime('%H:%M:%S')}")
                    if analysis.oooi.in_time:
                        click.echo(f"  In (Engine Stop): {analysis.oooi.in_time.strftime('%H:%M:%S')}")
                    if analysis.oooi.block_time_minutes:
                        click.echo(f"  Block Time: {analysis.oooi.block_time_minutes:.1f} minutes")
                    if analysis.oooi.flight_time_minutes:
                        click.echo(f"  Flight Time: {analysis.oooi.flight_time_minutes:.1f} minutes")
            else:
                click.echo(f"\n✗ Not a Flight")
                click.echo(f"  Reason: {analysis.detection.rejection_reason}")

            click.echo("=" * 60 + "\n")

    except Exception as e:
        logger.exception("Error analyzing flight")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@flight.command('upload')
@click.argument(
    'log_file',
    type=click.Path(exists=True, path_type=Path)
)
@click.option(
    '--service',
    multiple=True,
    help='Upload to specific service(s). Can be specified multiple times. If not specified, uploads to all enabled services.'
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Analyze but don\'t actually upload'
)
@click.pass_context
def flight_upload(ctx, log_file: Path, service: tuple, dry_run: bool):
    """
    Analyze and upload a flight log file.

    Analyzes the flight data and uploads to configured services.

    Services: cloudahoy, flysto, savvy_aviation, maintenance_tracker
    """
    from avcardtool.flight_data import PROCESSORS, FlightDataAnalyzer
    from avcardtool.flight_data.uploaders import UPLOADERS

    cfg = ctx.obj['config']

    # Try to find a processor that can handle this file
    processor = None
    for ProcessorClass in PROCESSORS:
        proc = ProcessorClass()
        if proc.detect_log_format(log_file):
            processor = proc
            click.echo(f"Detected format: {proc.get_name()}")
            break

    if not processor:
        click.echo(f"Error: Unable to detect log format for {log_file}", err=True)
        sys.exit(1)

    try:
        # Parse the log file
        click.echo(f"Parsing: {log_file}")
        flight_data = processor.parse_log(log_file)

        # Analyze the flight
        analyzer = FlightDataAnalyzer(cfg.flight_data)
        analysis = analyzer.analyze(flight_data)

        click.echo(f"\nAircraft: {analysis.aircraft_ident}")

        if not analysis.detection.is_flight:
            click.echo(f"✗ Not a Flight: {analysis.detection.rejection_reason}")
            click.echo("Skipping upload.")
            sys.exit(0)

        click.echo(f"✓ Flight Detected ({analysis.detection.airborne_time_minutes:.1f} minutes)")

        if dry_run:
            click.echo("\n--dry-run specified, skipping actual uploads")
            sys.exit(0)

        # Get analysis summary for uploaders
        analysis_summary = analyzer.analyze_summary(flight_data)

        # Determine which services to upload to
        if service:
            # User specified specific services
            upload_services = list(service)
        else:
            # Upload to all enabled services
            upload_services = []
            for service_name in cfg.flight_data.uploaders:
                if cfg.flight_data.uploaders[service_name].enabled:
                    upload_services.append(service_name)

        if not upload_services:
            click.echo("\nNo upload services enabled in configuration.")
            sys.exit(0)

        # Upload to each service
        click.echo(f"\nUploading to {len(upload_services)} service(s)...\n")
        results = {}

        for service_name in upload_services:
            if service_name not in UPLOADERS:
                click.echo(f"✗ {service_name}: Unknown service")
                continue

            if service_name not in cfg.flight_data.uploaders:
                click.echo(f"✗ {service_name}: Not configured")
                continue

            uploader_cfg = cfg.flight_data.uploaders[service_name]
            UploaderClass = UPLOADERS[service_name]

            # Build config dict with global settings merged in
            uploader_config = dict(uploader_cfg.config)
            uploader_config['enabled'] = uploader_cfg.enabled
            uploader_config['data_dir'] = cfg.system.data_dir
            uploader_config['debug'] = cfg.system.debug

            uploader = UploaderClass(uploader_config)

            # Upload
            click.echo(f"Uploading to {service_name}...", nl=False)
            result = uploader.upload_flight(flight_data, analysis_summary)
            results[service_name] = result

            if result.success:
                click.echo(f" ✓")
                if result.url:
                    click.echo(f"  URL: {result.url}")
            else:
                click.echo(f" ✗")
                click.echo(f"  Error: {result.message}")

        # Summary
        success_count = sum(1 for r in results.values() if r.success)
        click.echo(f"\nUpload complete: {success_count}/{len(results)} successful")

        if success_count < len(results):
            sys.exit(1)

    except Exception as e:
        logger.exception("Error uploading flight")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@flight.command('list-processors')
@click.pass_context
def flight_list_processors(ctx):
    """List available flight data processors."""
    from avcardtool.flight_data import PROCESSORS

    click.echo("Available Flight Data Processors:\n")

    for ProcessorClass in PROCESSORS:
        proc = ProcessorClass()
        extensions = ', '.join(proc.get_supported_extensions())
        click.echo(f"  • {proc.get_name()}")
        click.echo(f"    Extensions: {extensions}")

    click.echo("\nMore processors coming soon (Dynon, Aspen, Avidyne, etc.)")


@flight.command('list-uploaders')
@click.pass_context
def flight_list_uploaders(ctx):
    """List available upload services."""
    from avcardtool.flight_data.uploaders import UPLOADERS

    cfg = ctx.obj['config']

    click.echo("Available Upload Services:\n")

    for service_name, UploaderClass in UPLOADERS.items():
        uploader_cfg = cfg.flight_data.uploaders.get(service_name)
        enabled = uploader_cfg.enabled if uploader_cfg else False
        status = "✓ enabled" if enabled else "disabled"

        uploader = UploaderClass({})
        click.echo(f"  • {service_name} ({status})")
        doc = (UploaderClass.__doc__ or "").strip().split(chr(10))[0]
        click.echo(f"    {doc}")


@flight.command('flysto-auth')
@click.argument('authorization_code')
@click.pass_context
def flight_flysto_auth(ctx, authorization_code: str):
    """
    Complete FlySto OAuth authorization.

    After getting the authorization code from the FlySto OAuth flow,
    use this command to exchange it for access/refresh tokens.

    Example:
        avcardtool flight flysto-auth ABC123XYZ
    """
    from avcardtool.flight_data.uploaders import FlyStoUploader

    cfg = ctx.obj['config']

    if 'flysto' not in cfg.flight_data.uploaders:
        click.echo("Error: FlySto not configured. Please add flysto configuration first.", err=True)
        sys.exit(1)

    uploader_config = cfg.flight_data.uploaders['flysto']
    uploader_config['data_dir'] = cfg.system.data_dir

    uploader = FlyStoUploader(uploader_config)

    click.echo("Exchanging authorization code for tokens...")
    success, message = uploader.exchange_code_for_tokens(authorization_code)

    if success:
        click.echo(f"✓ {message}")
        click.echo(f"Tokens saved to: {uploader.token_file}")
        click.echo("\nFlySto is now configured for uploads!")
    else:
        click.echo(f"✗ {message}", err=True)
        sys.exit(1)


# ============================================================================
# Auto-Process Command
# ============================================================================

@cli.command('auto-process')
@click.argument(
    'path',
    type=click.Path(exists=True, path_type=Path)
)
@click.option(
    '--service',
    multiple=True,
    help='Upload to specific service(s). If not specified, uploads to all enabled services.'
)
@click.option(
    '--skip-uploads',
    is_flag=True,
    help='Process files but skip uploads'
)
@click.pass_context
def auto_process(ctx, path: Path, service: tuple, skip_uploads: bool):
    """
    Automatically process all flight logs in a directory or SD card.

    This command:
    1. Finds all G3X CSV log files
    2. Checks if already processed (deduplication)
    3. Analyzes flights
    4. Uploads to configured services
    5. Marks files as processed

    PATH can be:
    - SD card mount point (e.g., /media/sd_card)
    - Directory with log files
    - Device path (e.g., /dev/sda1) - will be mounted automatically
    """
    from avcardtool.flight_data import PROCESSORS, FlightDataAnalyzer
    from avcardtool.flight_data.uploaders import UPLOADERS
    from avcardtool.core import ProcessedFilesDatabase, hash_file

    cfg = ctx.obj['config']

    # Check if path is a device
    if path.is_block_device():
        click.echo(f"Mounting device: {path}")
        from avcardtool.core import mount_device
        mount_point = mount_device(str(path))
        if not mount_point:
            click.echo(f"Error: Could not mount {path}", err=True)
            sys.exit(1)
        path = Path(mount_point)
    else:
        path = path.resolve()

    click.echo(f"Processing: {path}\n")

    # Initialize processed files database
    db_path = Path(cfg.system.data_dir) / 'processed_files.json'
    processed_db = ProcessedFilesDatabase(db_path)

    # Find all G3X CSV files
    log_files = []
    search_dirs = [path / "data_log", path]  # Check data_log folder first

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for csv_file in search_dir.glob("*.csv"):
            try:
                with open(csv_file, 'r') as f:
                    first_line = f.readline()
                    if first_line.startswith('#airframe_info'):
                        log_files.append(csv_file)
            except Exception:
                pass

    if not log_files:
        click.echo("No G3X log files found")
        sys.exit(0)

    click.echo(f"Found {len(log_files)} log file(s)\n")

    upload_services = list(service) if service else []

    # Process each file
    stats = {
        'total': len(log_files),
        'already_processed': 0,
        'flights': 0,
        'non_flights': 0,
        'upload_success': 0,
        'upload_failed': 0
    }

    for log_file in log_files:
        click.echo(f"Processing: {log_file.name}")

        # Check if already processed
        file_hash = hash_file(log_file)
        if processed_db.is_processed(file_hash):
            click.echo(f"  ⊙ Already processed (skipped)")
            stats['already_processed'] += 1
            continue

        # Find processor
        processor = None
        for ProcessorClass in PROCESSORS:
            proc = ProcessorClass()
            if proc.detect_log_format(log_file):
                processor = proc
                break

        if not processor:
            click.echo(f"  ✗ Unknown format (skipped)")
            continue

        try:
            # Parse and analyze
            flight_data = processor.parse_log(log_file)
            analyzer = FlightDataAnalyzer(cfg.flight_data)
            analysis = analyzer.analyze(flight_data)

            if not analysis.detection.is_flight:
                click.echo(f"  ✗ Not a flight: {analysis.detection.rejection_reason}")
                stats['non_flights'] += 1
                processed_db.mark_processed(
                    file_hash,
                    log_file,
                    analysis.aircraft_ident,
                    False
                )
                continue

            stats['flights'] += 1
            click.echo(f"  ✓ Flight detected: {analysis.aircraft_ident}")
            click.echo(f"    Hobbs: +{analysis.hobbs.increment_hours:.2f}h, Tach: +{analysis.tach.increment_hours:.2f}h")

            if skip_uploads:
                click.echo(f"    (Skipping uploads)")
                processed_db.mark_processed(
                    file_hash,
                    log_file,
                    analysis.aircraft_ident,
                    True
                )
                continue

            # Upload to services
            analysis_summary = analyzer.analyze_summary(flight_data)

            # Determine which services to upload to
            if service:
                upload_services = list(service)
            else:
                upload_services = []
                for service_name in cfg.flight_data.uploaders:
                    if cfg.flight_data.uploaders[service_name].enabled:
                        upload_services.append(service_name)

            upload_results = {}
            for service_name in upload_services:
                if service_name not in UPLOADERS:
                    continue

                uploader_cfg = cfg.flight_data.uploaders.get(service_name)
                if uploader_cfg is None:
                    continue
                uploader_config = dict(uploader_cfg.config)
                uploader_config['enabled'] = uploader_cfg.enabled
                uploader_config['data_dir'] = cfg.system.data_dir
                uploader_config['debug'] = cfg.system.debug
                UploaderClass = UPLOADERS[service_name]
                uploader = UploaderClass(uploader_config)

                result = uploader.upload_flight(flight_data, analysis_summary)
                upload_results[service_name] = {
                    'success': result.success,
                    'message': result.message,
                    'url': result.url
                }

                if result.success:
                    click.echo(f"    ✓ {service_name}")
                    stats['upload_success'] += 1
                else:
                    click.echo(f"    ✗ {service_name}: {result.message}")
                    stats['upload_failed'] += 1

            # Mark as processed
            processed_db.mark_processed(
                file_hash,
                log_file,
                analysis.aircraft_ident,
                True,
                upload_results
            )

        except Exception as e:
            logger.exception(f"Error processing {log_file}")
            click.echo(f"  ✗ Error: {e}", err=True)

    # Summary
    click.echo(f"\n{'='*60}")
    click.echo(f"Processing Summary")
    click.echo(f"{'='*60}")
    click.echo(f"Total files: {stats['total']}")
    click.echo(f"Already processed: {stats['already_processed']}")
    click.echo(f"Flights detected: {stats['flights']}")
    click.echo(f"Non-flights: {stats['non_flights']}")
    if not skip_uploads and upload_services:
        click.echo(f"Uploads successful: {stats['upload_success']}")
        click.echo(f"Uploads failed: {stats['upload_failed']}")
    click.echo(f"{'='*60}\n")


# ============================================================================
# Navigation Database Command Group
# ============================================================================

@cli.group()
@click.pass_context
def navdata(ctx):
    """
    Navigation database management commands.

    Download and install aviation databases (NavData, Terrain, Obstacles, Charts).
    """
    pass


@navdata.command('login')
@click.option('--email', '-e', prompt=True, help='Garmin account email')
@click.option('--password', '-p', default=None, help='Garmin account password')
@click.option('--force', is_flag=True, help='Force re-login')
@click.pass_context
def navdata_login(ctx, email: str, password: Optional[str], force: bool):
    """Login to Garmin flyGarmin portal."""
    if password is None:
        password = _prompt_password("Password: ")
    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAuthError

    cfg = ctx.obj['config']
    from pathlib import Path as _Path
    auth = GarminAuth(token_dir=_Path(cfg.system.data_dir))

    if auth.is_authenticated() and not force:
        click.echo(f"Already authenticated as {auth.tokens.display_name}")
        return

    def get_mfa_code():
        click.echo("\n" + "!" * 40)
        click.echo("Multi-Factor Authentication Required")
        click.echo("!" * 40)
        return click.prompt("Enter the verification code sent to your email/phone")

    try:
        click.echo(f"Logging in to flyGarmin as {email}...")
        success = auth.login(email, password, mfa_callback=get_mfa_code)
        
        if success:
            click.echo(f"✓ Logged in as {auth.tokens.display_name}. Tokens saved to {auth.token_file}")
            click.echo("You can now download and update databases.")
    except GarminAuthError as e:
        click.echo(f"✗ Login failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"✗ Unexpected error during login: {e}", err=True)
        sys.exit(1)


@navdata.command('list-databases')
@click.pass_context
def navdata_list_databases(ctx):
    """List aircraft, devices, and available database subscriptions."""
    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError
    from avcardtool.navdata.garmin.api import FlyGarminAPI

    cfg = ctx.obj['config']
    auth = GarminAuth(token_dir=Path(cfg.system.data_dir))

    if not auth.is_authenticated():
        click.echo("Not logged in. Run 'avcardtool navdata login' first.", err=True)
        sys.exit(1)

    try:
        api = FlyGarminAPI(auth)
        aircraft_list = api.list_aircraft()

        if not aircraft_list:
            click.echo("No aircraft found on this account.")
            return

        for ai, ac in enumerate(aircraft_list):
            click.echo(f"\n[{ai}] {ac.tail_number}  ({ac.avdb_status})")
            for di, dev in enumerate(ac.devices):
                click.echo(f"     Device [{di}]: {dev.name}  serial={dev.display_serial}  ({dev.avdb_status})")
                for avdb in dev.avdb_types:
                    if not avdb.series:
                        continue
                    for s in avdb.series:
                        installable = s.installable_issues
                        latest = installable[0] if installable else (s.available_issues[0] if s.available_issues else None)
                        installed = f"  installed={avdb.installed_issue_name}" if avdb.installed_issue_name else ""
                        can_install = f"  → can install: {latest.name} ({latest.effective_at[:10]}–{(latest.invalid_at or 'no expiry')[:10]})" if latest else "  (no installable issues)"
                        click.echo(f"       {avdb.name} [{avdb.status}]  series={s.series_id}  {s.region_name}{installed}{can_install}")

    except GarminAPIError as e:
        click.echo(f"API error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@navdata.command('download')
@click.option('--aircraft', '-a', type=int, default=0, show_default=True,
              help='Aircraft index from list-databases')
@click.option('--device', '-d', type=int, default=None,
              help='Device index within aircraft (default: all devices)')
@click.option('--series', '-s', type=int, default=None,
              help='Series ID to download (default: all installable series)')
@click.option('--issue', '-i', default=None,
              help='Specific issue/cycle name (default: latest installable)')
@click.option('--card-serial', default=None,
              help='SD card volume serial for unlock (default: auto-detect)')
@click.option(
    '--output', '-o',
    type=click.Path(path_type=Path),
    default=None,
    help='Output directory (default: data_dir/navdata/)'
)
@click.pass_context
def navdata_download(ctx, aircraft: int, device: Optional[int], series: Optional[int],
                     issue: Optional[str], card_serial: Optional[str], output: Optional[Path]):
    """Download navigation databases from flyGarmin."""
    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError
    from avcardtool.navdata.garmin.api import FlyGarminAPI

    cfg = ctx.obj['config']
    auth = GarminAuth(token_dir=Path(cfg.system.data_dir))

    if not auth.is_authenticated():
        click.echo("Not logged in. Run 'avcardtool navdata login' first.", err=True)
        sys.exit(1)

    output_dir = output or (Path(cfg.system.data_dir) / "navdata")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect SD card serial
    if card_serial is None:
        try:
            from avcardtool.navdata.sdcard import SDCardDetector
            cards = SDCardDetector().scan_for_cards()
            if cards:
                card_serial = cards[0].volume_id
                click.echo(f"SD card serial: {card_serial}")
        except Exception:
            pass
    if not card_serial:
        card_serial = "0"

    try:
        from avcardtool.navdata.garmin.api import BatchDatabase
        api = FlyGarminAPI(auth)
        aircraft_list = api.list_aircraft()

        if not aircraft_list:
            click.echo("No aircraft found on this account.", err=True)
            sys.exit(1)

        if aircraft >= len(aircraft_list):
            click.echo(f"Aircraft index {aircraft} out of range (0–{len(aircraft_list)-1}).", err=True)
            sys.exit(1)

        ac = aircraft_list[aircraft]
        click.echo(f"Aircraft: {ac.tail_number}")

        devices = [ac.devices[device]] if device is not None else ac.devices

        # ---------------------------------------------------------------
        # Step 1: collect all (device, avdb, series, issue) tuples to download
        # ---------------------------------------------------------------
        plan = []  # list of (dev, avdb, series_obj, issue_obj)
        for dev in devices:
            for avdb in dev.avdb_types:
                for s in avdb.series:
                    if series is not None and s.series_id != series:
                        continue
                    candidates = s.installable_issues or s.available_issues
                    if not candidates:
                        continue
                    if issue:
                        target = next((i for i in candidates if i.name == issue), None)
                        if not target:
                            continue
                    else:
                        target = candidates[0]
                    plan.append((dev, avdb, s, target))

        if not plan:
            click.echo("No installable database issues found.")
            return

        # ---------------------------------------------------------------
        # Step 2: create a batch-update session (mirrors Garmin's own client)
        # ---------------------------------------------------------------
        batch_dbs = []
        for dev, avdb, s, target in plan:
            batch_dbs.append(BatchDatabase(
                series_id=s.series_id,
                issue_name=target.name,
                device_ids=[dev.device_id],
            ))

        click.echo(f"\nCreating batch-update session for {len(batch_dbs)} database(s)...")
        try:
            batch_id = api.create_batch_update(batch_dbs)
            click.echo(f"Batch ID: {batch_id}")
        except Exception as e:
            click.echo(f"Warning: could not create batch session ({e}). Falling back to direct unlock.")
            batch_id = None

        # ---------------------------------------------------------------
        # Step 3: for each entry unlock and download
        # ---------------------------------------------------------------
        downloaded = []
        for dev, avdb, s, target in plan:
            expiry = (target.invalid_at or "no expiry")[:10]
            click.echo(f"\n  {avdb.name}  device={dev.name}  series={s.series_id}  issue={target.name} (expires {expiry})")

            # Unlock with BatchUpdate auth if we have a session
            try:
                api.unlock(s.series_id, target.name, dev.device_id, card_serial, batch_id=batch_id)
                click.echo("    Unlocked.")
            except Exception as e:
                click.echo(f"    Warning: unlock failed ({e}) — attempting download anyway.")

            # Fetch file list
            issue_files = api.list_files(s.series_id, target.name)
            all_files = issue_files.main_files + issue_files.auxiliary_files
            if not all_files:
                click.echo("    No files found for this issue.")
                continue

            series_dir = output_dir / avdb.name.replace(" ", "_") / target.name
            for db_file in all_files:
                def _progress(done: int, total: int, name: str = db_file.file_name) -> None:
                    pct = int(done / total * 100) if total else 0
                    click.echo(f"\r    {name}: {pct}%  ", nl=False)

                dest = api.download_file(db_file, series_dir, progress_callback=_progress)
                click.echo(f"\r    {db_file.file_name}: done ({db_file.file_size:,} bytes)  ")
                downloaded.append(dest)

        click.echo(f"\nDownloaded {len(downloaded)} file(s) to {output_dir}")
        if downloaded:
            click.echo("Run 'avcardtool navdata install' to write to your SD card.")

    except GarminAPIError as e:
        click.echo(f"API error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@navdata.command('install')
@click.argument(
    'taw_file',
    type=click.Path(exists=True, path_type=Path)
)
@click.argument(
    'sd_card',
    type=click.Path(path_type=Path),
    required=False
)
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@click.pass_context
def navdata_install(ctx, taw_file: Path, sd_card: Optional[Path], yes: bool):
    """
    Install database to SD card.

    If SD_CARD is not provided, attempts to auto-detect.
    """
    click.echo(f"Would install: {taw_file}")
    if sd_card:
        click.echo(f"To: {sd_card}")
    else:
        click.echo("Auto-detecting SD card...")


@navdata.command('auto-update')
@click.option('--device', '-d', type=click.Path(path_type=Path), help='Device path')
@click.pass_context
def navdata_auto_update(ctx, device: Optional[Path]):
    """
    Automatically download latest databases and install to SD card.

    This is the all-in-one command for complete database updates.
    """
    click.echo("Automatic database update functionality coming soon...")


# ============================================================================
# Configuration Command Group
# ============================================================================

@cli.group()
@click.pass_context
def config(ctx):
    """Configuration management commands."""
    pass


@config.command('show')
@click.option(
    '--section',
    type=click.Choice(['flight_data', 'navdata', 'system', 'all']),
    default='all',
    help='Configuration section to show'
)
@click.pass_context
def config_show(ctx, section: str):
    """Display current configuration."""
    cfg = ctx.obj['config']
    cfg_dict = cfg.to_dict()

    if section == 'all':
        output = cfg_dict
    else:
        output = {section: cfg_dict[section]}

    click.echo(json.dumps(output, indent=2))


@config.command('generate')
@click.argument(
    'output_file',
    type=click.Path(path_type=Path)
)
@click.pass_context
def config_generate(ctx, output_file: Path):
    """Generate a default configuration file."""
    try:
        Config.generate_default(output_file)
        click.echo(f"Generated default configuration: {output_file}")
        click.echo("\nEdit the file to configure:")
        click.echo("  - Upload service credentials")
        click.echo("  - Hobbs/Tach calculation settings")
        click.echo("  - Garmin account for navdata downloads")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@config.command('validate')
@click.pass_context
def config_validate(ctx):
    """Validate current configuration."""
    cfg = ctx.obj['config']

    try:
        cfg.validate()
        click.echo("✓ Configuration is valid")
    except ValueError as e:
        click.echo(f"✗ Configuration error: {e}", err=True)
        sys.exit(1)


@config.command('migrate')
@click.argument(
    'legacy_config',
    type=click.Path(exists=True, path_type=Path)
)
@click.argument(
    'output_file',
    type=click.Path(path_type=Path),
    required=False
)
@click.pass_context
def config_migrate(ctx, legacy_config: Path, output_file: Optional[Path]):
    """
    Migrate legacy configuration to new format.

    Converts old g3x_processor config to avcardtool format.
    """
    try:
        cfg = Config()
        cfg.load(legacy_config)

        if output_file:
            cfg.save(output_file)
            click.echo(f"Migrated configuration saved to: {output_file}")
        else:
            click.echo("Migrated configuration:")
            click.echo(json.dumps(cfg.to_dict(), indent=2))

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ============================================================================
# Self-Update Command
# ============================================================================

@cli.command('self-update')
@click.option(
    '--version',
    default=None,
    metavar='VERSION',
    help='Install a specific version (e.g. 1.2.0). Defaults to latest.'
)
@click.pass_context
def self_update(ctx, version: Optional[str]):
    """
    Update avcardtool to the latest version from GitHub.

    Uses the same Python interpreter that is currently running so the correct
    virtual environment or user installation is always targeted.

    Examples:
        avcardtool self-update
        avcardtool self-update --version 1.2.0
    """
    import re
    import subprocess

    repo = "git+https://github.com/elvinzhou/g3xuploader.git"
    package = f"{repo}@v{version}" if version else repo
    target = f"v{version}" if version else "latest"

    click.echo(f"Updating avcardtool to {target}...")

    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--upgrade', package],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        # Extract installed version from pip output, e.g. "Successfully installed avcardtool-1.3.2"
        match = re.search(r'Successfully installed avcardtool-([\d.]+)', result.stdout)
        if match:
            click.echo(f"Updated to v{match.group(1)}. Restart avcardtool service to apply.")
        else:
            click.echo("Update successful. Restart avcardtool service to apply.")
    else:
        click.echo(f"Update failed:\n{result.stderr.strip()}", err=True)
        sys.exit(1)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for the CLI."""
    try:
        cli(obj={})
    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user", err=True)
        sys.exit(130)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
