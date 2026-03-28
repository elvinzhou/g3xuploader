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
    type=click.Path(path_type=Path)
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
        batch_id = None
        batch_system_ids: list = []
        try:
            batch_id = api.create_batch_update(batch_dbs)
            click.echo(f"Batch ID: {batch_id}")
            # GET the plan — the response includes device serial numbers (used for feat_unlk.dat)
            batch_plan = api.get_batch_update(batch_id)
            batch_system_ids = [
                int(d["serial"])
                for d in batch_plan.get("devices", [])
                if isinstance(d.get("serial"), int)
            ]
            if batch_system_ids:
                click.echo(f"Device serial(s): {batch_system_ids}")
        except Exception as e:
            click.echo(f"Warning: could not create batch session ({e}). Falling back to direct unlock.")
            batch_id = None

        # ---------------------------------------------------------------
        # Step 3: for each entry unlock, fetch file list, and download
        # ---------------------------------------------------------------
        manifest_entries = []
        downloaded = []

        for dev, avdb, s, target in plan:
            expiry = (target.invalid_at or "no expiry")[:10]
            click.echo(f"\n  {avdb.name}  device={dev.name}  series={s.series_id}  issue={target.name} (expires {expiry})")

            # Unlock with BatchUpdate auth if we have a session
            unlock_codes = []
            try:
                unlock_resp = api.unlock(s.series_id, target.name, dev.device_id, card_serial, batch_id=batch_id)
                unlock_codes = unlock_resp.get("unlockCodes", [])
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

                # Peek at TAW header to get database_type (avionics device code).
                # Used during install to filter out TAW files for other avionics units
                # that share this subscription (e.g. jg600a, bdg600, shzn, tgtn).
                taw_db_type = None
                if dest.suffix.lower() == ".taw":
                    try:
                        from avcardtool.navdata.garmin.taw_parser import TAWParser
                        taw_info = TAWParser().parse(dest)
                        taw_db_type = taw_info.header.database_type
                        click.echo(
                            f"    avionics='{taw_info.header.avionics}' "
                            f"db_type=0x{taw_db_type:04X}"
                        )
                    except Exception:
                        pass

                manifest_entries.append({
                    "local_path": str(dest.relative_to(output_dir)),
                    "destination": db_file.destination,
                    "avdb_type": avdb.name,
                    "avdb_type_id": avdb.type_id,   # needed for PUT /devices/installed-issues
                    "series_id": s.series_id,
                    "issue_name": target.name,
                    "device_id": dev.device_id,     # needed for PUT /devices/installed-issues
                    "removable_paths": issue_files.removable_paths,
                    "unlock_codes": unlock_codes,
                    "taw_database_type": taw_db_type,  # avionics type code; None for .jnx/.hif
                })

        # Write manifest so 'navdata install' knows destinations and cleanup paths
        if manifest_entries:
            import datetime
            # Collect unique system IDs.  Prefer serials from the batch-update
            # GET response (which includes the real hardware serial); fall back
            # to whatever the aircraft list returned.
            if batch_system_ids:
                system_ids = batch_system_ids
            else:
                system_ids = list({
                    dev.system_id_raw
                    for dev, _, _, _ in plan
                    if dev.system_id_raw is not None
                })
            # Determine the target avionics database_type via the /device-models/ API.
            # Each device model has a productID that equals the TAW header database_type
            # (= feat_unlk security_id).  Match the device name(s) in our plan against
            # the device-models list to get the authoritative productID.
            # Fall back to frequency heuristic if the API call fails or finds no match.
            primary_db_type = None
            device_type_map: dict = {}  # name → productID, for all devices in this plan
            device_names = list({dev.name for dev, _, _, _ in plan if dev.name})
            try:
                device_models = api.list_device_models()
                # Build name → productID map (case-insensitive)
                model_map = {
                    m["name"].lower(): m["productID"]
                    for m in device_models
                    if m.get("name") and m.get("productID") is not None
                }
                for dname in device_names:
                    product_id = model_map.get(dname.lower())
                    if product_id is not None:
                        device_type_map[dname] = product_id
                        if primary_db_type is None:
                            primary_db_type = product_id
                        click.echo(f"Device model '{dname}' → productID={product_id} (0x{product_id:04X})")
                if primary_db_type is None and device_names:
                    click.echo(f"Warning: device name(s) {device_names!r} not found in /device-models/ — falling back to frequency heuristic")
            except Exception as e:
                click.echo(f"Warning: /device-models/ lookup failed ({e}) — falling back to frequency heuristic")

            if primary_db_type is None:
                from collections import Counter
                db_type_counts = Counter(
                    e["taw_database_type"]
                    for e in manifest_entries
                    if e.get("taw_database_type") is not None
                )
                primary_db_type = db_type_counts.most_common(1)[0][0] if db_type_counts else None

            manifest = {
                "downloaded_at": datetime.datetime.now().isoformat(),
                "aircraft": ac.tail_number,
                "card_serial": card_serial,   # used by install to derive vol_id for feat_unlk.dat
                "system_ids": system_ids,      # avionics hardware IDs for feat_unlk.dat
                "batch_id": batch_id,          # used by install for PUT /devices/installed-issues
                "device_database_type": primary_db_type,  # TAW db_type for this avionics unit
                "device_type_map": device_type_map,        # name → productID for all devices
                "entries": manifest_entries,
            }
            manifest_path = output_dir / "navdata_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            click.echo(f"\nManifest saved: {manifest_path}")

        click.echo(f"\nDownloaded {len(downloaded)} file(s) to {output_dir}")
        if downloaded:
            click.echo("Run 'avcardtool navdata install <SD_CARD_PATH>' to write to your SD card.")

    except GarminAPIError as e:
        click.echo(f"API error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# feat_unlk.dat CRC reader
# ---------------------------------------------------------------------------

# Maps flyGarmin avdb type name → feat_unlk Feature name
_AVDB_TO_FEAT_UNLK: dict = {
    "NavData":           "NAVIGATION",
    "Terrain":           "TERRAIN",
    "Obstacle":          "OBSTACLE",
    "SafeTaxi":          "SAFETAXI",
    "ChartView":         "FLITE_CHARTS",
    "Basemap":           "BASEMAP",
    "Airport Directory": "AIRPORT_DIR",
    "Sectionals":        "SECTIONALS",
}


def _read_feat_unlk_crcs(sd_card: Path) -> dict:
    """
    Return the database file CRC stored in each feat_unlk.dat slot.

    feat_unlk.dat records the Garmin CRC-32 of the installed database file.
    If the CRC for a freshly downloaded file matches the stored value the card
    already has that exact version and the install can be skipped.

    Returns {Feature.name: crc_int} — zero-valued (blank/uninitialised) CRCs
    are excluded.
    """
    from avcardtool.navdata.garmin.feat_unlk import Feature

    path = sd_card / "feat_unlk.dat"
    if not path.exists():
        return {}

    crcs: dict = {}
    try:
        with open(path, "rb") as f:
            for feature in Feature:
                # CONTENT1 layout within the slot:
                #   0-1   MAGIC1
                #   2-3   security_id delta
                #   4-7   MAGIC2
                #   8-11  feature_bit
                #  12-15  reserved
                #  16-19  encoded_vol_id
                #  20-21  MAGIC3 (NAVIGATION only)
                #  20/22  file_CRC (4 bytes LE)
                crc_offset = 22 if feature == Feature.NAVIGATION else 20
                f.seek(feature.offset + crc_offset)
                raw = f.read(4)
                if len(raw) == 4:
                    crc = int.from_bytes(raw, "little")
                    if crc:
                        crcs[feature.name] = crc
    except OSError:
        pass
    return crcs


def _extract_taw_crcs(taw_path: Path) -> dict:
    """
    Read the database file CRC embedded at the end of each region in a TAW.

    flyGarmin TAW regions are typically stored raw (uncompressed).  The last
    4 bytes of each region are the Garmin CRC-32 of that database file — the
    same value that feat_unlk.dat stores.  Returns {Feature.name: crc_int}.

    For the rare compressed region the fast-path value will be wrong; the
    CRC comparison in auto-update will then fall through to a full install,
    which is harmless.
    """
    from avcardtool.navdata.garmin.taw_parser import TAWParser, TAW_REGION_PATHS
    from avcardtool.navdata.garmin.feat_unlk import FILENAME_TO_FEATURE

    crcs: dict = {}
    try:
        taw_info = TAWParser().parse(taw_path)
        with open(taw_path, "rb") as f:
            for region in taw_info.regions:
                dest = TAW_REGION_PATHS.get(region.region_type)
                if not dest:
                    continue
                feature = FILENAME_TO_FEATURE.get(dest)
                if not feature or region.compressed_size < 4:
                    continue
                f.seek(region.offset + region.compressed_size - 4)
                raw = f.read(4)
                if len(raw) == 4:
                    crc = int.from_bytes(raw, "little")
                    if crc:
                        crcs[feature.name] = crc
    except Exception:
        pass
    return crcs


# ---------------------------------------------------------------------------
# Shared download cache helpers
# ---------------------------------------------------------------------------

_DL_STATE_FILE = "download_state.json"
_DL_LOCK_FILE  = "download_state.lock"


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_dl_state(cache_dir: Path) -> dict:
    p = cache_dir / _DL_STATE_FILE
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _write_dl_state(cache_dir: Path, state: dict) -> None:
    (cache_dir / _DL_STATE_FILE).write_text(json.dumps(state, indent=2))


def _acquire_dl_slot(cache_dir: Path, key: str) -> str:
    """
    Try to claim the download slot for *key* in the shared cache.

    Returns:
        "mine"   — slot acquired; caller must download then call _release_dl_slot
        "cached" — a previous run completed this download; reuse the files
        "wait"   — another live process is currently downloading this key
    """
    import fcntl

    with open(cache_dir / _DL_LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        state = _read_dl_state(cache_dir)
        entry = state.get(key, {})

        if entry.get("status") == "complete":
            return "cached"

        if entry.get("status") == "downloading":
            pid = entry.get("pid", 0)
            if pid and _is_pid_alive(pid):
                return "wait"
            # Stale entry from a crashed process — take it over

        state[key] = {"status": "downloading", "pid": os.getpid(), "files": [], "feature_crcs": {}}
        _write_dl_state(cache_dir, state)
        return "mine"


def _release_dl_slot(
    cache_dir: Path,
    key: str,
    files: list,          # [{"cache_path": str, "destination": ..., "taw_database_type": ...}]
    feature_crcs: dict,
    removable_paths: list,
    avdb_type_id: int,
    series_id: int,
    success: bool,
) -> None:
    """Mark the download slot complete (or remove it on failure so the next run retries)."""
    import fcntl

    with open(cache_dir / _DL_LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        state = _read_dl_state(cache_dir)
        if success:
            state[key] = {
                "status": "complete",
                "pid": os.getpid(),
                "files": files,
                "feature_crcs": feature_crcs,
                "removable_paths": removable_paths,
                "avdb_type_id": avdb_type_id,
                "series_id": series_id,
            }
        else:
            state.pop(key, None)
        _write_dl_state(cache_dir, state)


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink src → dst within the same filesystem, copy otherwise."""
    import shutil as _shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        _shutil.copy2(src, dst)


def _resolve_target_db_type(sd_card: Path, manifest: dict) -> Optional[int]:
    """
    Determine which avionics database_type (= TAW database_type = feat_unlk security_id)
    to use when installing to *sd_card*.

    Resolution order:
      1. avionics.txt at SD card root — content is the avionics name (e.g. "G3X Touch").
         Looked up in manifest's device_type_map (populated during download from
         the /device-models/ API).
      2. Existing feat_unlk.dat on the card — the NAVIGATION slot stores the
         security_id at bytes 2-3; recover it as int.from_bytes + SEC_ID_OFFSET.
      3. Manifest has exactly one distinct db_type — unambiguous, use it.
      4. Return None — caller must error and tell the user to create avionics.txt.
    """
    from avcardtool.navdata.garmin.feat_unlk import SEC_ID_OFFSET, Feature

    # --- 1. avionics.txt ---
    avionics_file = sd_card / "avionics.txt"
    if avionics_file.exists():
        try:
            avionics_name = avionics_file.read_text(encoding="utf-8").strip()
            device_type_map: dict = manifest.get("device_type_map", {})
            # Case-insensitive lookup
            name_lower = avionics_name.lower()
            for map_name, product_id in device_type_map.items():
                if map_name.lower() == name_lower:
                    click.echo(f"avionics.txt: '{avionics_name}' → db_type=0x{product_id:04X}")
                    return product_id
            click.echo(
                f"Warning: avionics.txt says '{avionics_name}' but that name is not in "
                f"the manifest's device_type_map {list(device_type_map.keys())} — "
                f"ignoring avionics.txt"
            )
        except Exception as e:
            click.echo(f"Warning: could not read avionics.txt ({e}) — ignoring")

    # --- 2. Existing feat_unlk.dat ---
    feat_unlk_path = sd_card / "feat_unlk.dat"
    if feat_unlk_path.exists():
        try:
            nav_offset = Feature.NAVIGATION.offset
            with open(feat_unlk_path, "rb") as f:
                f.seek(nav_offset + 2)
                raw = f.read(2)
            if len(raw) == 2:
                stored = int.from_bytes(raw, "little")
                security_id = (stored + SEC_ID_OFFSET) & 0xFFFF
                click.echo(
                    f"feat_unlk.dat: NAVIGATION slot security_id=0x{security_id:04X} "
                    f"→ using as target db_type"
                )
                return security_id
        except Exception as e:
            click.echo(f"Warning: could not read feat_unlk.dat ({e})")

    # --- 3. Single distinct db_type in manifest ---
    distinct = {
        e["taw_database_type"]
        for e in manifest.get("entries", [])
        if e.get("taw_database_type") is not None
    }
    if len(distinct) == 1:
        db_type = next(iter(distinct))
        click.echo(f"Single avionics db_type in manifest: 0x{db_type:04X}")
        return db_type

    return None


@navdata.command('install')
@click.argument(
    'sd_card',
    type=click.Path(path_type=Path),
    required=False,
)
@click.option(
    '--from', 'from_dir',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help='Download directory containing navdata_manifest.json (default: data_dir/navdata/)',
)
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompt')
@click.pass_context
def navdata_install(ctx, sd_card: Optional[Path], from_dir: Optional[Path], yes: bool):
    """
    Install downloaded databases to the SD card.

    SD_CARD is the mount point of the card (e.g. /mnt/sdcard or M:\\ on Windows).
    If omitted, auto-detection is attempted (Linux only).

    Reads navdata_manifest.json written by 'navdata download' to know which
    files go where and which old files to remove first.
    """
    from avcardtool.navdata.garmin.taw_parser import TAWExtractor, TAWParseError
    import shutil

    cfg = ctx.obj['config']
    download_dir = from_dir or (Path(cfg.system.data_dir) / "navdata")

    # ---------------------------------------------------------------
    # Load manifest
    # ---------------------------------------------------------------
    manifest_path = download_dir / "navdata_manifest.json"
    if not manifest_path.exists():
        click.echo(f"No manifest found at {manifest_path}.", err=True)
        click.echo("Run 'avcardtool navdata download' first.", err=True)
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    entries = manifest.get("entries", [])
    if not entries:
        click.echo("Manifest is empty — nothing to install.", err=True)
        sys.exit(1)

    # ---------------------------------------------------------------
    # Resolve SD card path
    # ---------------------------------------------------------------
    if sd_card is None:
        try:
            from avcardtool.navdata.sdcard import SDCardDetector
            cards = SDCardDetector().scan_for_cards()
            garmin_cards = [c for c in cards if c.is_garmin and c.mount_point]
            if garmin_cards:
                sd_card = Path(garmin_cards[0].mount_point)
                click.echo(f"Auto-detected SD card: {sd_card}")
            elif cards and cards[0].mount_point:
                sd_card = Path(cards[0].mount_point)
                click.echo(f"Auto-detected card (not Garmin-formatted): {sd_card}")
        except Exception:
            pass

    if sd_card is None:
        click.echo("No SD card found. Specify the mount point as an argument.", err=True)
        sys.exit(1)

    if not sd_card.exists():
        click.echo(f"SD card path does not exist: {sd_card}", err=True)
        sys.exit(1)

    # ---------------------------------------------------------------
    # Summary and confirmation
    # ---------------------------------------------------------------
    avdb_types = sorted({e["avdb_type"] for e in entries})
    removable = sorted({p for e in entries for p in e.get("removable_paths", [])})

    click.echo(f"\nDownload directory : {download_dir}")
    click.echo(f"SD card            : {sd_card}")
    click.echo(f"Databases          : {', '.join(avdb_types)}")
    click.echo(f"Files to install   : {len(entries)}")
    if removable:
        click.echo(f"Old files to remove: {len(removable)}")

    if not yes:
        click.confirm("\nProceed with installation?", abort=True)

    # ---------------------------------------------------------------
    # Step 1: Remove old files listed in removablePaths
    # ---------------------------------------------------------------
    if removable:
        click.echo("\nRemoving old database files...")
        for rel_path in removable:
            # Strip leading slash: API returns "/fbo.gpi", Path("/fbo.gpi") is absolute
            clean = rel_path.replace("\\", "/").lstrip("/")
            target = sd_card / clean
            # Guard against path traversal
            try:
                target.resolve().relative_to(sd_card.resolve())
            except ValueError:
                click.echo(f"  Skipping unsafe path: {rel_path}", err=True)
                continue
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                click.echo(f"  Removed: {clean}")

    # ---------------------------------------------------------------
    # Step 2: Install each file
    # ---------------------------------------------------------------
    extractor = TAWExtractor()
    installed_files = []
    errors = []
    # Maps str(installed_file_path) → security_id (TAW database_type)
    # so Step 3 can look up the correct security_id per extracted file.
    _extracted_security_ids: dict[str, int] = {}

    # Determine which avionics unit this card belongs to.
    # Required when a subscription covers multiple avionics (e.g. G3X Touch + GTN 6XX)
    # so we only extract TAW files for the correct unit.
    click.echo("\nResolving target avionics type...")
    target_db_type: Optional[int] = _resolve_target_db_type(sd_card, manifest)
    if target_db_type is None:
        device_type_map = manifest.get("device_type_map", {})
        if device_type_map:
            names = ", ".join(f"'{n}'" for n in device_type_map)
            click.echo(
                f"Cannot determine which avionics this card belongs to.\n"
                f"Create a file named 'avionics.txt' in the SD card root containing "
                f"exactly one of: {names}",
                err=True,
            )
        else:
            click.echo(
                "Cannot determine avionics type — no device_type_map in manifest.\n"
                "Re-run 'avcardtool navdata download' to refresh the manifest.",
                err=True,
            )
        sys.exit(1)
    click.echo(f"Target avionics db_type: 0x{target_db_type:04X}")

    click.echo(f"\nInstalling {len(entries)} file(s)...")

    for entry in entries:
        local_path = download_dir / entry["local_path"]
        destination = entry.get("destination")  # relative path on SD card, or None
        avdb_type = entry["avdb_type"]

        if not local_path.exists():
            click.echo(f"  Missing: {local_path.name} — skipping")
            errors.append(f"Missing local file: {local_path}")
            continue

        suffix = local_path.suffix.lower()

        # Skip TAW files intended for a different avionics unit.
        if suffix == ".taw" and target_db_type is not None:
            entry_db_type = entry.get("taw_database_type")
            if entry_db_type is not None and entry_db_type != target_db_type:
                click.echo(
                    f"  Skipping {local_path.name} "
                    f"(db_type=0x{entry_db_type:04X}, not for this avionics)"
                )
                continue

        if suffix == ".taw":
            # Extract TAW archive — files go to paths determined by region headers.
            # Parse the header first to get security_id (database_type), which is
            # needed later when writing feat_unlk.dat.
            click.echo(f"  Extracting {local_path.name}  ({avdb_type})")
            try:
                taw_info = extractor.list_contents(local_path)
                security_id = taw_info.header.database_type
                extracted = extractor.extract_to_directory(
                    local_path, sd_card, preserve_paths=True, overwrite=True
                )
                for f in extracted:
                    click.echo(f"    → {f.relative_to(sd_card)}")
                    installed_files.append(f)
                    _extracted_security_ids[str(f)] = security_id
            except TAWParseError as e:
                click.echo(f"  Error extracting {local_path.name}: {e}", err=True)
                errors.append(str(e))

        elif suffix in (".jnx", ".hif"):
            # Copy directly to destination path from API, or infer from filename
            if destination:
                dest_path = sd_card / Path(destination.replace("\\", "/"))
            else:
                # Fallback: infer from filename for known raster types
                name = local_path.name
                if name.startswith("SECT_"):
                    dest_path = sd_card / "rasters" / "SECT" / name
                elif name.startswith("HI_"):
                    dest_path = sd_card / "rasters" / "HI" / name
                elif name.startswith("LO_"):
                    dest_path = sd_card / "rasters" / "LO" / name
                elif name.startswith("HELI_"):
                    dest_path = sd_card / "rasters" / "HELI" / name
                elif suffix == ".hif":
                    dest_path = sd_card / "rasters" / name
                else:
                    dest_path = sd_card / name

            click.echo(f"  Copying  {local_path.name}  → {dest_path.relative_to(sd_card)}")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest_path)
            from avcardtool.navdata.garmin.taw_parser import _set_hidden
            _set_hidden(dest_path)
            installed_files.append(dest_path)

        else:
            click.echo(f"  Unknown file type {suffix}, skipping: {local_path.name}", err=True)

    # ---------------------------------------------------------------
    # Step 3: feat_unlk.dat + .evidf.dat
    #
    # feat_unlk.dat is a copy-protection file.  Each database type
    # occupies a fixed 913-byte slot at a predetermined offset.  Each
    # slot contains the encoded FAT32 volume serial, the database
    # file's embedded CRC, and the truncated avionics system ID.
    # See src/avcardtool/navdata/garmin/feat_unlk.py for details.
    #
    # .evidf.dat is 4 bytes: encode_volume_id(vol_id) — exactly the
    # same encoded serial stored in every feat_unlk.dat slot.  Garmin
    # Aviation Database Manager writes it on every scan/install.
    # ---------------------------------------------------------------
    from avcardtool.navdata.garmin.feat_unlk import (
        write_feat_unlk_for_file, vol_id_from_card_serial,
        get_vol_id_from_sd_card, encode_volume_id,
    )
    from avcardtool.navdata.garmin.taw_parser import _set_hidden as _set_hidden_attr

    # Resolve vol_id: prefer manifest card_serial (used at download time),
    # fall back to reading from the mounted block device.
    vol_id: Optional[int] = vol_id_from_card_serial(manifest.get("card_serial", ""))
    if vol_id is None:
        vol_id = get_vol_id_from_sd_card(sd_card)
    if vol_id is None:
        click.echo("\nWarning: cannot determine SD card volume serial — "
                   "feat_unlk.dat and .evidf.dat will not be written.", err=True)
    else:
        # Use the first system_id available (typically one device per aircraft)
        system_ids = manifest.get("system_ids", [])
        system_id: int = system_ids[0] if system_ids else 0

        feat_unlk_count = 0
        for installed_file in installed_files:
            # security_id comes from the TAW header (database_type field).
            # We stored it per-entry when building extracted_security_ids above.
            entry_sec_id = _extracted_security_ids.get(str(installed_file), 0)
            if write_feat_unlk_for_file(sd_card, installed_file, vol_id,
                                        entry_sec_id, system_id):
                feat_unlk_count += 1

        if feat_unlk_count:
            _set_hidden_attr(sd_card / "feat_unlk.dat")
            click.echo(f"\nUpdated feat_unlk.dat  ({feat_unlk_count} feature slot(s))")

        # .evidf.dat — 4 bytes: encoded volume serial (same value embedded in
        # every feat_unlk.dat slot).  Written by GADM on every scan/install.
        evidf_path = sd_card / ".evidf.dat"
        try:
            evidf_path.write_bytes(encode_volume_id(vol_id).to_bytes(4, 'little'))
            _set_hidden_attr(evidf_path)
            click.echo(f"Updated .evidf.dat  (0x{encode_volume_id(vol_id):08X})")
        except Exception as e:
            click.echo(f"Warning: could not write .evidf.dat: {e}", err=True)

    # ---------------------------------------------------------------
    # Step 4: Update GarminDevice.xml version entries
    # ---------------------------------------------------------------

    garmin_device_xml = sd_card / "Garmin" / "GarminDevice.xml"
    if garmin_device_xml.exists() and installed_files:
        try:
            _update_garmin_device_xml(garmin_device_xml, manifest)
            click.echo("\nUpdated GarminDevice.xml")
        except Exception as e:
            click.echo(f"\nWarning: could not update GarminDevice.xml: {e}", err=True)

    # ---------------------------------------------------------------
    # Step 5: Write .gadm.meta
    # Garmin Aviation Database Manager writes this hidden JSON file
    # to track which account/device last updated the card.
    # ---------------------------------------------------------------
    gadm_meta_path = sd_card / ".gadm.meta"
    try:
        import datetime as _dt

        # Preserve existing fields (tail number, account) if already present
        existing_gadm: dict = {}
        if gadm_meta_path.exists():
            try:
                existing_gadm = json.loads(gadm_meta_path.read_text())
            except Exception:
                pass

        # Pull account info from Garmin auth tokens if available
        garmin_account = existing_gadm.get("garminAccount", "")
        tail_number = existing_gadm.get("gadmTailNumber", manifest.get("aircraft", ""))
        try:
            from avcardtool.navdata.garmin.auth import GarminAuth
            _auth = GarminAuth(token_dir=Path(cfg.system.data_dir))
            if _auth.tokens.display_name:
                garmin_account = _auth.tokens.display_name
        except Exception:
            pass

        gadm = {
            "id": existing_gadm.get("id") or __import__("uuid").uuid4().hex,
            "garminAccount": garmin_account,
            "gadmLastInstalled": _dt.datetime.now().strftime(
                "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
            ),
            "gadmAvionicsName": existing_gadm.get("gadmAvionicsName", "G3X Touch"),
            "gadmCardDescription": existing_gadm.get("gadmCardDescription"),
            "gadmTailNumber": tail_number,
            "gadmFleetMetadata": existing_gadm.get("gadmFleetMetadata"),
        }
        gadm_meta_path.write_text(json.dumps(gadm))
        click.echo("Updated .gadm.meta")
    except Exception as e:
        click.echo(f"Warning: could not update .gadm.meta: {e}", err=True)

    # ---------------------------------------------------------------
    # Step 6: Report installed issues to flyGarmin
    #
    # PUT /devices/installed-issues tells the server which cycles are
    # now on the card so fly.garmin.com shows the correct status and
    # subscription tracking stays up to date.  Uses the same BatchUpdate
    # session that was created during download.
    # ---------------------------------------------------------------
    batch_id = manifest.get("batch_id")
    if batch_id and installed_files:
        try:
            from avcardtool.navdata.garmin.auth import GarminAuth
            from avcardtool.navdata.garmin.api import FlyGarminAPI
            _auth = GarminAuth(token_dir=Path(cfg.system.data_dir))
            _api = FlyGarminAPI(_auth)

            import datetime as _dt2
            now_iso = _dt2.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

            # Deduplicate: one report per unique (device_id, series_id, issue_name)
            seen: set = set()
            installations = []
            for entry in entries:
                key = (entry.get("device_id"), entry.get("series_id"), entry["issue_name"])
                if key not in seen:
                    seen.add(key)
                    installations.append({
                        "name": entry["issue_name"],
                        "seriesID": entry["series_id"],
                        "installedAt": now_iso,
                        "avdbTypeID": entry.get("avdb_type_id", 0),
                        "deviceID": entry.get("device_id", 0),
                    })

            _api.report_installed_issues(installations, batch_id)
            click.echo(f"Reported {len(installations)} installed issue(s) to fly.garmin.com")
        except Exception as e:
            click.echo(f"Warning: could not report to fly.garmin.com: {e}", err=True)

    # ---------------------------------------------------------------
    # Write cycle tracking file for auto-update
    # ---------------------------------------------------------------
    # .navdata_cycles.json lets 'navdata auto-update' know what is already
    # on the card so it can skip databases that are already current.
    if installed_files:
        try:
            import datetime as _dt3
            cycle_map = {}
            for entry in entries:
                avdb_type = entry.get("avdb_type", "")
                issue_name = entry.get("issue_name", "")
                series_id = entry.get("series_id")
                if avdb_type and issue_name:
                    cycle_map[avdb_type] = {"issue": issue_name, "series_id": series_id}
            if cycle_map:
                (sd_card / ".navdata_cycles.json").write_text(
                    json.dumps({"updated_at": _dt3.datetime.now().isoformat(), "cycles": cycle_map}, indent=2)
                )
        except Exception as e:
            click.echo(f"Warning: could not write .navdata_cycles.json: {e}", err=True)

    # ---------------------------------------------------------------
    # Done
    # ---------------------------------------------------------------
    if errors:
        click.echo(f"\nCompleted with {len(errors)} error(s):")
        for e in errors:
            click.echo(f"  {e}", err=True)
    else:
        click.echo(f"\nInstalled {len(installed_files)} file(s) to {sd_card}")


def _update_garmin_device_xml(xml_path: Path, manifest: dict) -> None:
    """
    Update <UpdateFile> version entries in GarminDevice.xml for each
    installed database.  Garmin's client does this after every install;
    the G3X reads the file to show database currency on the avionics page.

    Version encoding observed on a real card:
        cycle "20T1"  → <Major>20</Major><Minor>1</Minor>
        cycle "26B2"  → <Major>26</Major><Minor>2</Minor>
        cycle "2603"  → <Major>26</Major><Minor>3</Minor>
    i.e. Major = first two digits; Minor = trailing numeric digits.
    """
    import re
    import xml.etree.ElementTree as ET

    # Part-number prefixes known to map to each avdb type
    # (derived from observed GarminDevice.xml entries)
    AVDB_PART_PREFIXES = {
        "NavData":   ["006-D0600", "006-D1159"],
        "Terrain":   ["006-D0678"],
        "Obstacle":  ["006-D0123"],
        "SafeTaxi":  ["006-D0680"],
        "ChartView": ["006-D3497"],
    }

    # Parse cycle string into Major/Minor
    def _parse_cycle(cycle: str):
        # Strip leading letters (e.g. "20T1" → year=20, minor=1)
        # Keep trailing digits as Minor; leading digits as Major
        m = re.match(r'^(\d+)\D*(\d+)$', cycle)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r'^(\d{2})(\d{2})$', cycle)  # plain "2603"
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    # Build a mapping: avdb_type → (major, minor) from manifest entries
    type_versions: dict = {}
    for entry in manifest.get("entries", []):
        avdb_type = entry.get("avdb_type", "")
        issue_name = entry.get("issue_name", "")
        major, minor = _parse_cycle(issue_name)
        if major is not None and avdb_type not in type_versions:
            type_versions[avdb_type] = (major, minor)

    if not type_versions:
        return

    # Parse XML preserving the original text as much as possible
    ET.register_namespace("", "http://www.garmin.com/xmlschemas/GarminDevice/v2")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    ns = {"gd": "http://www.garmin.com/xmlschemas/GarminDevice/v2"}

    for update_file in root.findall(".//gd:UpdateFile", ns):
        pn_el = update_file.find("gd:PartNumber", ns)
        if pn_el is None:
            continue
        part_number = pn_el.text or ""

        for avdb_type, prefixes in AVDB_PART_PREFIXES.items():
            if any(part_number.startswith(p) for p in prefixes):
                if avdb_type not in type_versions:
                    continue
                major, minor = type_versions[avdb_type]
                ver_el = update_file.find("gd:Version", ns)
                if ver_el is not None:
                    maj_el = ver_el.find("gd:Major", ns)
                    min_el = ver_el.find("gd:Minor", ns)
                    if maj_el is not None:
                        maj_el.text = str(major)
                    if min_el is not None:
                        min_el.text = str(minor)
                # Remove <Description>Missing</Description> if present
                desc_el = update_file.find("gd:Description", ns)
                if desc_el is not None and desc_el.text == "Missing":
                    update_file.remove(desc_el)
                break

    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)


@navdata.command('auto-update')
@click.argument('device', type=click.Path(path_type=Path), required=False)
@click.pass_context
def navdata_auto_update(ctx, device: Optional[Path]):
    """
    Silently update all inserted SD cards with latest navigation databases.

    DEVICE: block device path (e.g. /dev/sda1).  If omitted, every mounted
    FAT32 card is scanned.

    Designed for unattended use — no prompts, all output goes to stdout/stderr
    for capture by journald.  Typically invoked by the avcardtool-navdata@.service
    systemd unit when a card is inserted.

    For each card:
      1. Resolves which avionics it belongs to (avionics.txt → feat_unlk.dat →
         single-device fallback).
      2. Reads .navdata_cycles.json to know what is already installed.
      3. Downloads the current cycle (if the card is expired/behind) AND the
         next upcoming cycle (if already available on the server).
      4. Installs to the card and writes updated .navdata_cycles.json.
    """
    import datetime
    import logging as _logging

    _log = _logging.getLogger("avcardtool.navdata.auto_update")

    cfg = ctx.obj['config']
    data_dir = Path(cfg.system.data_dir)

    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError
    from avcardtool.navdata.garmin.api import FlyGarminAPI, BatchDatabase
    from avcardtool.navdata.garmin.taw_parser import TAWParser
    from avcardtool.navdata.sdcard import SDCardDetector

    # ---------------------------------------------------------------
    # Authenticate
    # ---------------------------------------------------------------
    auth = GarminAuth(token_dir=data_dir)
    if not auth.ensure_authenticated():
        _log.error("Not authenticated. Run 'avcardtool navdata login' first.")
        sys.exit(1)

    api = FlyGarminAPI(auth)

    # ---------------------------------------------------------------
    # Fetch aircraft + device-models (shared across all cards)
    # ---------------------------------------------------------------
    try:
        aircraft_list = api.list_aircraft()
    except GarminAPIError as e:
        _log.error(f"Could not fetch aircraft list: {e}")
        sys.exit(1)

    if not aircraft_list:
        _log.error("No aircraft found on this account.")
        sys.exit(1)

    ac = aircraft_list[0]

    try:
        device_models = api.list_device_models()
        model_map = {
            m["name"].lower(): m["productID"]
            for m in device_models
            if m.get("name") and m.get("productID") is not None
        }
    except Exception as e:
        _log.warning(f"Could not fetch device models: {e}")
        model_map = {}

    device_type_map: dict = {}
    for dev in ac.devices:
        product_id = model_map.get(dev.name.lower())
        if product_id is not None:
            device_type_map[dev.name] = product_id

    # ---------------------------------------------------------------
    # Find SD cards
    # ---------------------------------------------------------------
    detector = SDCardDetector()
    _we_mounted: Optional[str] = None

    if device:
        all_cards = detector.scan_for_cards()
        cards = [c for c in all_cards if c.device_path == str(device)]
        if not cards:
            # Not yet mounted — wait briefly for udisks, then try ourselves
            import time as _time
            _time.sleep(2)
            all_cards = detector.scan_for_cards()
            cards = [c for c in all_cards if c.device_path == str(device)]
        if not cards:
            try:
                _we_mounted = detector.mount_card(str(device))
                all_cards = detector.scan_for_cards()
                cards = [c for c in all_cards if c.device_path == str(device)]
            except Exception as e:
                _log.error(f"Could not mount {device}: {e}")
                sys.exit(1)
    else:
        cards = detector.scan_for_cards()

    if not cards:
        _log.info("No SD cards found.")
        return

    # ---------------------------------------------------------------
    # Process each card
    # ---------------------------------------------------------------
    for card in cards:
        if not card.mount_point:
            _log.info(f"Skipping {card.device_path}: not mounted")
            continue

        mount = Path(card.mount_point)
        _log.info(f"Processing card: {mount}  serial={card.volume_id}")

        # --- Resolve avionics type ---
        # Build a synthetic manifest so _resolve_target_db_type can do its checks.
        # Entries cover all known avionics so feat_unlk.dat fallback has a full
        # set of db_types to cross-reference against.
        mock_manifest = {
            "device_type_map": device_type_map,
            "entries": [{"taw_database_type": pid} for pid in device_type_map.values()],
        }
        target_db_type = _resolve_target_db_type(mount, mock_manifest)
        if target_db_type is None:
            _log.warning(
                f"  Cannot determine avionics for {mount}. "
                f"Create avionics.txt with one of: {list(device_type_map.keys())}"
            )
            continue

        target_dev = next(
            (d for d in ac.devices if device_type_map.get(d.name) == target_db_type),
            None,
        )
        if target_dev is None:
            _log.warning(f"  db_type=0x{target_db_type:04X} matched no registered device — skipping")
            continue

        _log.info(f"  Avionics: {target_dev.name}")

        # --- Read what is currently on the card ---
        installed_cycles: dict = {}
        cycles_file = mount / ".navdata_cycles.json"
        if cycles_file.exists():
            try:
                installed_cycles = json.loads(cycles_file.read_text()).get("cycles", {})
                _log.info(f"  Installed: {installed_cycles}")
            except Exception as e:
                _log.warning(f"  Could not read .navdata_cycles.json: {e}")

        # --- Select which issues to download ---
        # current: latest installable, if different from what is on the card.
        # next:    first issue in available_issues that is not yet installable
        #          (upcoming cycle, pre-download).
        plan = []  # [(avdb, series, issue)]
        for avdb in target_dev.avdb_types:
            installed_issue = installed_cycles.get(avdb.name, {}).get("issue")
            for s in avdb.series:
                installable_names = {i.name for i in s.installable_issues}

                if s.installable_issues:
                    current = s.installable_issues[0]
                    if current.name != installed_issue:
                        _log.info(f"  {avdb.name}: need current {current.name} (have {installed_issue or 'none'})")
                        plan.append((avdb, s, current))

                for issue in s.available_issues:
                    if issue.name not in installable_names:
                        if issue.name != installed_issue:
                            _log.info(f"  {avdb.name}: pre-downloading next cycle {issue.name}")
                            plan.append((avdb, s, issue))
                        break  # only the earliest upcoming cycle

        if not plan:
            _log.info(f"  All databases current — nothing to do.")
            continue

        # ---------------------------------------------------------------
        # Download with shared cache + coordination
        # ---------------------------------------------------------------
        card_serial = card.volume_id or "0"
        cache_dir = data_dir / "navdata" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        card_dir  = data_dir / "navdata" / f"auto_{card_serial.replace('-', '')}"
        card_dir.mkdir(parents=True, exist_ok=True)

        # Read what the card already has (from feat_unlk.dat CRCs).
        # These are compared against the CRCs stored per-issue in the
        # download state to skip installs when the card is already current.
        feat_unlk_crcs = _read_feat_unlk_crcs(mount)
        if feat_unlk_crcs:
            _log.info(f"  feat_unlk.dat CRCs: { {k: hex(v) for k, v in feat_unlk_crcs.items()} }")

        # Create a batch session for the issues we plan to download.
        batch_id = None
        batch_system_ids: list = []
        try:
            batch_dbs = [
                BatchDatabase(series_id=s.series_id, issue_name=issue.name, device_ids=[target_dev.device_id])
                for _, s, issue in plan
            ]
            batch_id = api.create_batch_update(batch_dbs)
            batch_plan = api.get_batch_update(batch_id)
            batch_system_ids = [
                int(d["serial"]) for d in batch_plan.get("devices", [])
                if isinstance(d.get("serial"), int)
            ]
        except Exception as e:
            _log.warning(f"  Batch session failed ({e}) — continuing without batch auth")

        manifest_entries = []
        import time as _time

        for avdb, s, issue in plan:
            cache_key = f"{avdb.name}/{issue.name}"
            issue_cache_dir = cache_dir / avdb.name.replace(" ", "_") / issue.name

            # --- Acquire download slot ---
            slot = _acquire_dl_slot(cache_dir, cache_key)

            if slot == "wait":
                _log.info(f"  {cache_key}: another process is downloading, waiting...")
                deadline = _time.time() + 1800
                while _time.time() < deadline:
                    _time.sleep(5)
                    slot = _acquire_dl_slot(cache_dir, cache_key)
                    if slot != "wait":
                        break
                if slot == "wait":
                    _log.error(f"  {cache_key}: timed out waiting — skipping")
                    continue

            if slot == "cached":
                _log.info(f"  {cache_key}: using cached download")
                # Check if this card already has this exact version via feat_unlk CRC.
                dl_state = _read_dl_state(cache_dir)
                cached_crcs = dl_state.get(cache_key, {}).get("feature_crcs", {})
                feat_name = _AVDB_TO_FEAT_UNLK.get(avdb.name)
                if (feat_name
                        and feat_unlk_crcs.get(feat_name)
                        and cached_crcs.get(feat_name)
                        and feat_unlk_crcs[feat_name] == cached_crcs[feat_name]):
                    _log.info(
                        f"  {avdb.name}/{issue.name}: feat_unlk CRC matches "
                        f"(0x{feat_unlk_crcs[feat_name]:08X}) — already installed, skipping"
                    )
                    continue

                # Hardlink cached files into this card's dir and rebuild manifest entries.
                for file_entry in dl_state.get(cache_key, {}).get("files", []):
                    src = cache_dir / file_entry["cache_path"]
                    dst = card_dir / file_entry["cache_path"]
                    _link_or_copy(src, dst)
                    manifest_entries.append({
                        "local_path": file_entry["cache_path"],
                        "destination": file_entry["destination"],
                        "avdb_type": avdb.name,
                        "avdb_type_id": dl_state[cache_key].get("avdb_type_id", avdb.type_id),
                        "series_id": dl_state[cache_key].get("series_id", s.series_id),
                        "issue_name": issue.name,
                        "device_id": target_dev.device_id,
                        "removable_paths": dl_state[cache_key].get("removable_paths", []),
                        "unlock_codes": [],
                        "taw_database_type": file_entry.get("taw_database_type"),
                    })
                continue

            # slot == "mine" — download now
            try:
                api.unlock(s.series_id, issue.name, target_dev.device_id, card_serial, batch_id=batch_id)
            except Exception as e:
                _log.warning(f"  Unlock failed for {cache_key}: {e}")

            try:
                issue_files = api.list_files(s.series_id, issue.name)
            except Exception as e:
                _log.error(f"  list_files failed for {cache_key}: {e}")
                _release_dl_slot(cache_dir, cache_key, [], {}, [], avdb.type_id, s.series_id, success=False)
                continue

            downloaded: list = []
            file_entries: list = []
            for db_file in issue_files.main_files + issue_files.auxiliary_files:
                try:
                    dest = api.download_file(db_file, issue_cache_dir)
                    _log.info(f"  Downloaded {dest.name} ({dest.stat().st_size:,} bytes)")

                    taw_db_type = None
                    if dest.suffix.lower() == ".taw":
                        try:
                            taw_db_type = TAWParser().parse(dest).header.database_type
                        except Exception:
                            pass

                    cache_path = str(dest.relative_to(cache_dir))
                    downloaded.append(dest)
                    file_entries.append({
                        "cache_path": cache_path,
                        "destination": db_file.destination,
                        "taw_database_type": taw_db_type,
                    })
                    # Hardlink into card dir
                    _link_or_copy(dest, card_dir / cache_path)
                    manifest_entries.append({
                        "local_path": cache_path,
                        "destination": db_file.destination,
                        "avdb_type": avdb.name,
                        "avdb_type_id": avdb.type_id,
                        "series_id": s.series_id,
                        "issue_name": issue.name,
                        "device_id": target_dev.device_id,
                        "removable_paths": issue_files.removable_paths,
                        "unlock_codes": [],
                        "taw_database_type": taw_db_type,
                    })
                except Exception as e:
                    _log.error(f"  Download failed for {db_file.file_name}: {e}")

            # Extract per-feature CRCs from the downloaded TAW files (fast path:
            # read last 4 bytes of each raw region — no full extraction needed).
            feature_crcs: dict = {}
            for dest in downloaded:
                if dest.suffix.lower() == ".taw":
                    feature_crcs.update(_extract_taw_crcs(dest))
            if feature_crcs:
                _log.info(f"  Extracted CRCs: { {k: hex(v) for k, v in feature_crcs.items()} }")

            _release_dl_slot(
                cache_dir, cache_key,
                files=file_entries,
                feature_crcs=feature_crcs,
                removable_paths=issue_files.removable_paths,
                avdb_type_id=avdb.type_id,
                series_id=s.series_id,
                success=bool(downloaded),
            )

        if not manifest_entries:
            _log.info(f"  Nothing to install for card at {mount}")
            continue

        # Write per-card manifest (files are in card_dir, local_path relative to card_dir)
        system_ids = batch_system_ids or (
            [target_dev.system_id_raw] if target_dev.system_id_raw is not None else []
        )
        manifest_path = card_dir / "navdata_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "downloaded_at": datetime.datetime.now().isoformat(),
                "aircraft": ac.tail_number,
                "card_serial": card_serial,
                "system_ids": system_ids,
                "batch_id": batch_id,
                "device_database_type": target_db_type,
                "device_type_map": device_type_map,
                "entries": manifest_entries,
            }, f, indent=2)

        _log.info(f"  Installing to {mount}...")
        try:
            ctx.invoke(navdata_install, sd_card=mount, from_dir=card_dir, yes=True)
        except SystemExit as e:
            if e.code != 0:
                _log.error(f"  Install failed (exit {e.code})")
        except Exception as e:
            _log.error(f"  Install raised: {e}")

    if _we_mounted:
        try:
            detector.unmount_card(_we_mounted)
        except Exception as e:
            _log.warning(f"Could not unmount {_we_mounted}: {e}")


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
