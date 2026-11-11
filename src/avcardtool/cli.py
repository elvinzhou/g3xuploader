"""
Unified command-line interface for aviation tools.

Provides subcommands for:
- Flight data processing (flight)
- Navigation database management (navdata)
- Configuration management (config)
- Automatic processing (auto-process)
"""

import os
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
@click.option(
    '--dry-run',
    is_flag=True,
    help='Read everything, write nothing: no uploads, no processed-file/state '
         'writes, no SD card writes, no navdata downloads. Upload payloads are '
         'saved to <data_dir>/debug/ and notifications are printed.'
)
@click.option(
    '--send-notifications',
    is_flag=True,
    help='With --dry-run: really send notifications (subject tagged "[DRY RUN]").'
)
@click.option(
    '--data-dir',
    type=click.Path(path_type=Path, file_okay=False),
    help='Override system.data_dir for this run (e.g. a scratch copy for testing). '
         'Never saved to the config file.'
)
@click.pass_context
def cli(ctx, config: Optional[Path], verbose: bool, dry_run: bool,
        send_notifications: bool, data_dir: Optional[Path]):
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

    overrides = {}
    if dry_run:
        overrides['dry_run'] = True
    if send_notifications:
        overrides['dry_run_send_notifications'] = True
    if data_dir:
        overrides['data_dir'] = str(data_dir.expanduser().resolve())
    if overrides:
        ctx.obj['config'].override_system(**overrides)

    # Setup logging
    log_level = 'DEBUG' if (verbose or ctx.obj['config'].system.debug) else ctx.obj['config'].system.log_level
    setup_logging(
        log_file=ctx.obj['config'].system.log_file,
        log_level=log_level
    )
    if ctx.obj['config'].system.dry_run:
        logger.warning("DRY RUN — nothing will be uploaded or written to the SD card")


def _notification_manager(cfg):
    """Build a NotificationManager, or None when notifications are off/unconfigured."""
    try:
        from avcardtool.notifications import NotificationManager
        manager = NotificationManager.from_config(cfg)
        # In dry-run the manager prints what would be sent (and why not, if
        # it wouldn't), so it is useful even when notifications are off.
        return manager if (manager.active or manager.dry_run) else None
    except Exception as e:
        logger.warning(f"Notifications unavailable: {e}")
        return None


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
    help='Analyze and build payloads (saved to <data_dir>/debug/) but don\'t upload. '
         'Same as the global --dry-run.'
)
@click.pass_context
def flight_upload(ctx, log_file: Path, service: tuple, dry_run: bool):
    """
    Analyze and upload a flight log file.

    Analyzes the flight data and uploads to configured services.

    Services: cloudahoy, flysto, savvy_aviation, carryd
    """
    from avcardtool.flight_data import PROCESSORS, FlightDataAnalyzer
    from avcardtool.flight_data.uploaders import UPLOADERS

    cfg = ctx.obj['config']
    dry_run = dry_run or cfg.system.dry_run

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
            uploader_config['dry_run'] = dry_run

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
    type=click.Path(path_type=Path, readable=False)
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
@click.option(
    '--force',
    is_flag=True,
    help='Re-process all files even if already marked as processed '
         '(also skips first-run historical marking).'
)
@click.pass_context
def auto_process(ctx, path: Path, service: tuple, skip_uploads: bool, force: bool):
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
    dry_run = cfg.system.dry_run

    # Resolve device path → mount point (always read-only: this command
    # never writes to the card)
    if path.is_block_device():
        from avcardtool.core import resolve_device_mount_point
        try:
            path = resolve_device_mount_point(path, readonly=True)
        except RuntimeError as e:
            click.echo(f"Error: {e}", err=True)
            manager = _notification_manager(cfg)
            if manager:
                from avcardtool.notifications.events import build_processing_error_event
                manager.notify(build_processing_error_event(
                    f"Could not mount SD card {path}: {e}"
                ))
            sys.exit(1)
    else:
        path = path.resolve()

    click.echo(f"Processing: {path}\n")
    if dry_run:
        click.echo("DRY RUN: no uploads, and processed_files.json / carryd_state.json are not modified\n")

    # Initialize processed files database (in-memory only for a dry run)
    db_path = Path(cfg.system.data_dir) / 'processed_files.json'
    is_first_run = not db_path.exists()
    processed_db = ProcessedFilesDatabase(db_path, read_only=dry_run)

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

    # On the very first run, optionally mark all existing files as historical
    # so they are skipped rather than uploaded (prevents flooding services with
    # flight history the user has already seen).
    if is_first_run and cfg.system.mark_historical_on_first_run and not force:
        click.echo(f"First run: marking {len(log_files)} existing file(s) as historical (skipping uploads)")
        for log_file in log_files:
            file_hash = hash_file(log_file)

            # Try to extract a fingerprint so that the same flight recorded by
            # other display units on later SD card insertions is also skipped.
            fingerprint = None
            for ProcessorClass in PROCESSORS:
                proc = ProcessorClass()
                if proc.detect_log_format(log_file):
                    try:
                        flight_data = proc.parse_log(log_file)
                        fingerprint = flight_data.flight_fingerprint()
                    except Exception:
                        pass
                    break

            processed_db.mark_historical(file_hash, log_file, fingerprint)
            click.echo(f"  ⊙ {log_file.name}" + (f"  [{fingerprint}]" if fingerprint else ""))
        click.echo("\nDone. Only flights recorded after this point will be uploaded.")
        if dry_run:
            click.echo("(dry run: nothing was saved; use --force to process these files anyway)")
        sys.exit(0)

    # Sort files chronologically using the timestamp in Garmin filenames
    # (log_YYYYMMDD_HHMMSS_...).  This ordering is required so that
    # pending_uploads[-1] is always the most recently recorded flight.
    import re as _re

    def _log_date_key(p: Path) -> str:
        m = _re.search(r'log_(\d{8}_\d{6})', p.name)
        return m.group(1) if m else p.name

    log_files.sort(key=_log_date_key)

    # Determine upload services once (same for every file in this batch).
    if service:
        upload_services = list(service)
    else:
        upload_services = []
        for service_name in cfg.flight_data.uploaders:
            if cfg.flight_data.uploaders[service_name].enabled:
                upload_services.append(service_name)

    # Pre-scan the newest unprocessed file for a non-flight override.
    #
    # If the most recent recording is a ground power cycle (not a flight),
    # the avionics recorded its internal Hobbs and engine hours in that
    # file's #airframe_info header.  Those starting values are more
    # authoritative than whatever our algorithm calculated as the ending
    # hours of the previous flight, so we'll substitute them below.
    override_meta: Optional[dict] = None
    _unprocessed = [f for f in log_files if not processed_db.is_processed(hash_file(f))]
    if _unprocessed:
        _newest = _unprocessed[-1]
        for _PC in PROCESSORS:
            _p = _PC()
            if _p.detect_log_format(_newest):
                try:
                    _nfd = _p.parse_log(_newest)
                    _nan = FlightDataAnalyzer(cfg.flight_data).analyze(_nfd)
                    if not _nan.detection.is_flight:
                        override_meta = {
                            'file': _newest.name,
                            'file_date_key': _log_date_key(_newest),
                            'airframe_hours': _nfd.metadata.airframe_hours_start,
                            'engine_hours': _nfd.metadata.engine_hours_start,
                            'aircraft_ident': _nfd.metadata.aircraft_ident,
                            'date': _nfd.metadata.date,
                        }
                except Exception:
                    pass
                break

    # Process each file — detect flights and collect pending uploads.
    # Uploads are deferred until after the loop so the non-flight override
    # can be applied to the most recently dated flight before any data is sent.
    pending_uploads: list = []  # [(file_hash, log_file, flight_data, analysis, fingerprint)]

    stats = {
        'total': len(log_files),
        'already_processed': 0,
        'flights': 0,
        'non_flights': 0,
        'upload_success': 0,
        'upload_failed': 0
    }

    # Collected for the end-of-run notification (see NOTIFICATIONS_DESIGN.md)
    from avcardtool.notifications.events import flight_entry_from_summary
    notified_flights: list = []
    notify_notes: list = []
    parse_errors: list = []

    for log_file in log_files:
        click.echo(f"Processing: {log_file.name}")

        # Check if already processed
        file_hash = hash_file(log_file)
        if not force and processed_db.is_processed(file_hash):
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

            # Check for duplicate flight from another display unit on the same aircraft
            fingerprint = flight_data.flight_fingerprint()
            if not force and fingerprint and processed_db.is_duplicate_flight(fingerprint):
                click.echo(f"  ⊙ Duplicate flight from another display unit (skipped uploads)")
                stats['already_processed'] += 1
                processed_db.mark_processed(
                    file_hash,
                    log_file,
                    analysis.aircraft_ident,
                    True,
                    flight_fingerprint=fingerprint
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
                    True,
                    flight_fingerprint=fingerprint
                )
                notified_flights.append(flight_entry_from_summary(log_file.name, {
                    'aircraft_ident': analysis.aircraft_ident,
                    'hobbs': {'increment_hours': analysis.hobbs.increment_hours,
                              'ending_hours': analysis.hobbs.ending_hours} if analysis.hobbs else None,
                    'tach': {'increment_hours': analysis.tach.increment_hours,
                             'ending_hours': analysis.tach.ending_hours} if analysis.tach else None,
                }))
                continue

            pending_uploads.append((file_hash, log_file, flight_data, analysis, fingerprint))

        except Exception as e:
            logger.exception(f"Error processing {log_file}")
            click.echo(f"  ✗ Error: {e}", err=True)
            parse_errors.append(f"{log_file.name}: {e}")

    # Build analysis summaries and apply the non-flight override where applicable.
    #
    # The override targets only pending_uploads[-1] — the most recently dated
    # flight — because the non-flight file immediately follows it.  The
    # condition is: override file timestamp > flight file timestamp.
    pending_with_summaries: list = []
    for idx, (fh, lf, fd, an, fp) in enumerate(pending_uploads):
        summary = FlightDataAnalyzer(cfg.flight_data).analyze_summary(fd)
        is_most_recent = (idx == len(pending_uploads) - 1)

        if is_most_recent and override_meta:
            # Only valid if no other flight sits between this flight and the
            # override file.  Files already recorded as flights/historical
            # (e.g. from earlier insertions) mean the override file follows
            # *those*, not this older flight.
            _lo, _hi = _log_date_key(lf), override_meta['file_date_key']
            _flight_between = False
            for _f in log_files:
                _k = _log_date_key(_f)
                if _lo < _k < _hi:
                    _rec = processed_db.get_record(hash_file(_f)) or {}
                    if _rec.get('is_flight') or _rec.get('historical'):
                        _flight_between = True
                        break
            if _lo < _hi and not _flight_between:
                new_hobbs = override_meta['airframe_hours']
                old_hobbs = (summary.get('hobbs') or {}).get('ending_hours')
                if new_hobbs is not None and new_hobbs != old_hobbs:
                    summary = {**summary, 'hobbs': {**(summary.get('hobbs') or {}),
                                                     'ending_hours': new_hobbs}}
                    click.echo(
                        f"\n  {lf.name}: Hobbs ending adjusted "
                        f"{old_hobbs:.2f}h → {new_hobbs:.2f}h "
                        f"(authoritative value from {override_meta['file']})"
                    )
                    notify_notes.append(
                        f"Hobbs ending adjusted {old_hobbs:.2f}h → {new_hobbs:.2f}h "
                        f"(authoritative value from {override_meta['file']})"
                    )

                new_tach = override_meta['engine_hours']
                old_tach = (summary.get('tach') or {}).get('ending_hours')
                if new_tach is not None and new_tach != old_tach:
                    summary = {**summary, 'tach': {**(summary.get('tach') or {}),
                                                    'ending_hours': new_tach}}
                    click.echo(
                        f"  {lf.name}: Tach ending adjusted "
                        f"{old_tach:.2f}h → {new_tach:.2f}h "
                        f"(authoritative value from {override_meta['file']})"
                    )
                    notify_notes.append(
                        f"Tach ending adjusted {old_tach:.2f}h → {new_tach:.2f}h "
                        f"(authoritative value from {override_meta['file']})"
                    )

        pending_with_summaries.append((fh, lf, fd, an, fp, summary))

    # Carryd is submitted once with the most recent flight's summary (after the
    # non-flight override has been applied) rather than once per flight.
    # Collect per-flight services separately so we can handle that below.
    per_flight_services = [s for s in upload_services if s != 'carryd']
    do_carryd = 'carryd' in upload_services and not skip_uploads

    # Upload all pending flights (excluding Carryd — handled after the loop)
    for file_hash, log_file, flight_data, analysis, fingerprint, analysis_summary in pending_with_summaries:
        upload_results = {}
        for service_name in per_flight_services:
            if service_name not in UPLOADERS:
                continue

            uploader_cfg = cfg.flight_data.uploaders.get(service_name)
            if uploader_cfg is None:
                continue
            uploader_config = dict(uploader_cfg.config)
            uploader_config['enabled'] = uploader_cfg.enabled
            uploader_config['data_dir'] = cfg.system.data_dir
            uploader_config['debug'] = cfg.system.debug
            uploader_config['dry_run'] = dry_run
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
            upload_results,
            flight_fingerprint=fingerprint
        )

        notified_flights.append(
            flight_entry_from_summary(log_file.name, analysis_summary, upload_results)
        )

    # Carryd: submit once using the flight with the latest avionics-recorded
    # timestamp across all pending flights.  Using the avionics date (from
    # OOOI data inside the file) rather than the filename timestamp means
    # an older SD card inserted later cannot overwrite Carryd with stale data
    # — we persist the last submitted timestamp and skip if not newer.
    if do_carryd and pending_with_summaries:
        def _avionics_ts(summary: dict) -> str:
            oooi = summary.get('oooi') or {}
            return oooi.get('off_time') or oooi.get('out_time') or summary.get('date') or ''

        best = max(pending_with_summaries, key=lambda t: _avionics_ts(t[5]))
        _, _, fd_best, _, _, summary_best = best
        candidate_ts = _avionics_ts(summary_best)

        carryd_state_path = Path(cfg.system.data_dir) / 'carryd_state.json'
        carryd_state: dict = {}
        try:
            carryd_state = json.loads(carryd_state_path.read_text())
        except Exception:
            pass
        last_submitted_ts = carryd_state.get('last_submitted_at', '')

        if candidate_ts and last_submitted_ts and candidate_ts <= last_submitted_ts:
            click.echo(
                f"\n  Carryd: skipping — flight at {candidate_ts} is not newer "
                f"than last submission ({last_submitted_ts})"
            )
            notify_notes.append(
                f"Carryd: skipped — flight at {candidate_ts} is not newer "
                f"than last submission ({last_submitted_ts})"
            )
        else:
            uploader_cfg = cfg.flight_data.uploaders.get('carryd')
            if uploader_cfg and uploader_cfg.enabled and 'carryd' in UPLOADERS:
                uploader_config = dict(uploader_cfg.config)
                uploader_config['enabled'] = uploader_cfg.enabled
                uploader_config['data_dir'] = cfg.system.data_dir
                uploader_config['debug'] = cfg.system.debug
                uploader_config['dry_run'] = dry_run
                carryd_uploader = UPLOADERS['carryd'](uploader_config)
                result = carryd_uploader.upload_flight(fd_best, summary_best)
                if result.success:
                    click.echo(f"\n  Carryd: ✓ {result.message}")
                    stats['upload_success'] += 1
                    notify_notes.append(f"Carryd: ✓ {result.message}")
                    if candidate_ts and dry_run:
                        click.echo(f"  (dry run: carryd_state.json not updated; would record {candidate_ts})")
                    elif candidate_ts:
                        carryd_state['last_submitted_at'] = candidate_ts
                        try:
                            carryd_state_path.write_text(json.dumps(carryd_state, indent=2))
                        except Exception as e:
                            click.echo(f"  Warning: could not save Carryd state: {e}", err=True)
                else:
                    click.echo(f"\n  Carryd: ✗ {result.message}")
                    stats['upload_failed'] += 1
                    notify_notes.append(f"Carryd: ✗ {result.message}")

    # When no flights were found but a non-flight file provided authoritative
    # times, we can still keep Carryd in sync using those values directly.
    # This requires the Carryd GET endpoint (get_current_times) so we can
    # compare before pushing and avoid spurious updates.
    #
    # Uncomment the block below once GET /api/v1/aircraft is available.
    #
    # if override_meta and not pending_uploads and not skip_uploads \
    #         and 'carryd' in upload_services:
    #     uploader_cfg = cfg.flight_data.uploaders.get('carryd')
    #     if uploader_cfg and uploader_cfg.enabled:
    #         from avcardtool.flight_data.uploaders.carryd import CarrydUploader
    #         uploader_config = dict(uploader_cfg.config)
    #         uploader_config['enabled'] = uploader_cfg.enabled
    #         uploader_config['data_dir'] = cfg.system.data_dir
    #         uploader_config['debug'] = cfg.system.debug
    #         carryd = CarrydUploader(uploader_config)
    #
    #         # Check what Carryd already has before pushing.
    #         current = carryd.get_current_times(
    #             registration=override_meta.get('aircraft_ident')
    #         )
    #         hobbs_changed = (
    #             current is None
    #             or current.get('totalTime') != override_meta['airframe_hours']
    #         )
    #         # Build engine_times dict if logbooks are configured.
    #         engine_times = {}
    #         if carryd.engine_logbooks and override_meta['engine_hours'] is not None:
    #             engine_times[carryd.engine_logbooks[0]] = override_meta['engine_hours']
    #         engines_changed = any(
    #             (current or {}).get('engineTimes', {}).get(k) != v
    #             for k, v in engine_times.items()
    #         ) if current else bool(engine_times)
    #
    #         if hobbs_changed or engines_changed:
    #             synthetic_summary = {
    #                 'aircraft_ident': override_meta['aircraft_ident'],
    #                 'date': override_meta['date'],
    #                 'hobbs': {'ending_hours': override_meta['airframe_hours']},
    #                 'tach': {'ending_hours': override_meta['engine_hours']},
    #             }
    #             # upload_flight expects a FlightData for debug file naming only;
    #             # pass None — the debug branch is guarded by self.debug and
    #             # would need a small None-check added to carryd.py if enabled.
    #             result = carryd.upload_flight(None, synthetic_summary)
    #             status = '✓' if result.success else '✗'
    #             click.echo(
    #                 f"\n  Carryd no-flight sync ({override_meta['file']}): "
    #                 f"{status} {result.message}"
    #             )
    #         else:
    #             click.echo(f"\n  Carryd already current — no sync needed")

    # Summary
    click.echo(f"\n{'='*60}")
    click.echo("Processing Summary" + (" (DRY RUN)" if dry_run else ""))
    click.echo(f"{'='*60}")
    click.echo(f"Total files: {stats['total']}")
    click.echo(f"Already processed: {stats['already_processed']}")
    click.echo(f"Flights detected: {stats['flights']}")
    click.echo(f"Non-flights: {stats['non_flights']}")
    if not skip_uploads and upload_services:
        click.echo(f"Uploads successful: {stats['upload_success']}")
        click.echo(f"Uploads failed: {stats['upload_failed']}")
    click.echo(f"{'='*60}\n")

    # Notify — only when this run actually handled something new; a card
    # re-insert where every file was already processed stays silent.
    if stats['flights'] or stats['non_flights'] or parse_errors:
        manager = _notification_manager(cfg)
        if manager:
            from avcardtool.notifications.events import build_flights_processed_event
            manager.notify(build_flights_processed_event(
                stats,
                notified_flights,
                notes=notify_notes,
                errors=parse_errors,
                uploads_skipped=skip_uploads or not upload_services,
            ))


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
                        installed = f"  installed={avdb.installed_issue_name}" if avdb.installed_issue_name else ""
                        if installable:
                            latest = installable[0]
                            can_install = f"  → can install: {latest.name} ({latest.effective_at[:10]}–{(latest.invalid_at or 'no expiry')[:10]})"
                        elif s.issues_remaining == 0:
                            expiry = s.expected_end_date[:10] if s.expected_end_date else "unknown"
                            can_install = f"  [subscription expired {expiry}]"
                        else:
                            can_install = "  (no installable issues)"
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
              help='SD card volume serial, used to build feat_unlk.dat (default: auto-detect)')
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

        # No batch-update session: POST /batch-updates/ returned 500 every
        # time, and direct unlock (the fallback it always ended up on) works.

        # Avionics types of this aircraft's displays: TAW files for any other
        # family in a subscription (e.g. G600, X3) are skipped before download.
        device_type_map, primary_db_type = _build_device_type_map(api, [d for d, _, _, _ in plan])
        # ---------------------------------------------------------------
        # Step 2: for each entry unlock, fetch file list, and download
        # ---------------------------------------------------------------
        manifest_entries = []
        downloaded = []

        for dev, avdb, s, target in plan:
            expiry = (target.invalid_at or "no expiry")[:10]
            click.echo(f"\n  {avdb.name}  device={dev.name}  series={s.series_id}  issue={target.name} (expires {expiry})")

            # Fetch the file list (doesn't need the unlock). Garmin lists this
            # device's own files for its series, so all of them are used.
            issue_files = api.list_files(s.series_id, target.name)
            all_files = issue_files.main_files + issue_files.auxiliary_files
            if not all_files:
                click.echo("    No files found for this issue.")
                continue

            unlock_codes = []
            try:
                unlock_resp = api.unlock(s.series_id, target.name, dev.device_id)
                unlock_codes = unlock_resp.get("unlockCodes", [])
                click.echo("    Unlocked.")
            except Exception as e:
                click.echo(f"    Warning: unlock failed ({e}) — attempting download anyway.")

            series_dir = output_dir / avdb.name.replace(" ", "_") / target.name
            for db_file in all_files:
                def _progress(done: int, total: int, name: str = db_file.file_name) -> None:
                    pct = int(done / total * 100) if total else 0
                    click.echo(f"\r    {name}: {pct}%  ", nl=False)

                dest = api.download_file(db_file, series_dir, progress_callback=_progress)
                click.echo(f"\r    {db_file.file_name}: done ({db_file.file_size:,} bytes)  ")
                downloaded.append(dest)

                # The avionics type recorded for a TAW is the device's own (what
                # install matches against the card and signs feat_unlk.dat with).
                # The header's type can differ: a GTN's navigation data is served
                # as 'jg600a-*.taw', whose header names the G600 family.
                taw_db_type = None
                if dest.suffix.lower() == ".taw":
                    taw_db_type = device_type_map.get(dev.name)
                    try:
                        from avcardtool.navdata.garmin.taw_parser import TAWParser
                        taw_info = TAWParser().parse(dest)
                        header_type = taw_info.header.database_type
                        if taw_db_type is None:
                            taw_db_type = header_type
                        click.echo(
                            f"    avionics='{taw_info.header.avionics}' "
                            f"header db_type=0x{header_type:04X}"
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
            # Unique System IDs from the aircraft list (device_system_ids
            # below records which device each belongs to).
            system_ids = list({
                dev.system_id_raw
                for dev, _, _, _ in plan
                if dev.system_id_raw is not None
            })
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
                # device_id → System ID, so install signs with the card's own device
                "device_system_ids": {
                    str(dev.device_id): dev.system_id_raw
                    for dev, _, _, _ in plan
                    if dev.system_id_raw is not None
                },
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

        # A "complete" entry with no files is a leftover negative result ("nothing
        # for this card's avionics") from older versions; treat it as a miss so
        # the file list is checked again instead of silently installing nothing.
        if entry.get("status") == "complete" and entry.get("files"):
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


# Issues kept per series in the download cache: the previous cycle, the
# current one, and the upcoming one that is pre-downloaded ahead of its
# effective date. Issue names (e.g. 2611, 26S6) sort chronologically within
# a series, so the newest three by name are previous/current/next.
NAVDATA_CACHE_KEEP_ISSUES = 3


def _tree_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _prune_navdata_cache(cache_dir: Path, keep: int = NAVDATA_CACHE_KEEP_ISSUES, log=None) -> int:
    """
    Delete downloaded issues older than the newest *keep* per series.

    Removes cache/<series>/<issue>/, its download_state.json entries, and the
    hardlinked copies under the per-card auto_*/ folders (those links would
    otherwise keep the data on disk). An issue another process is still
    downloading is never removed. Returns the bytes freed.
    """
    import fcntl
    import shutil as _shutil

    if not cache_dir.exists():
        return 0
    navdata_dir = cache_dir.parent

    with open(cache_dir / _DL_LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        state = _read_dl_state(cache_dir)

        series_issues: dict = {}
        busy: set = set()
        for key, entry in state.items():
            series, _, issue = key.split("@")[0].partition("/")
            if not issue:
                continue
            series_issues.setdefault(series, set()).add(issue)
            if entry.get("status") == "downloading" and _is_pid_alive(entry.get("pid", 0)):
                busy.add((series, issue))
        for series_dir in cache_dir.iterdir():
            if series_dir.is_dir() and series_dir.name.isdigit():
                for issue_dir in series_dir.iterdir():
                    if issue_dir.is_dir():
                        series_issues.setdefault(series_dir.name, set()).add(issue_dir.name)

        freed = 0
        for series, issues in series_issues.items():
            for issue in sorted(issues, reverse=True)[keep:]:
                if (series, issue) in busy:
                    continue
                for target in [cache_dir / series / issue,
                               *navdata_dir.glob(f"auto_*/{series}/{issue}")]:
                    if target.is_dir():
                        freed += _tree_size(target) if target.parent.parent == cache_dir else 0
                        _shutil.rmtree(target, ignore_errors=True)
                for key in [k for k in state if k.split("@")[0] == f"{series}/{issue}"]:
                    del state[key]
                if log:
                    log.info(f"  Pruned old navdata cache: series {series} issue {issue}")
        _write_dl_state(cache_dir, state)
    return freed


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


# Word identifying each database type in a GarminDevice.xml <Description>,
# e.g. "AOPA US Directory Data 26D4" for Airport Directory. Defaults to the
# type's own name ("Worldwide-9 Terrain 20T1", "US SafeTaxi 26S4", ...).
_DEVICE_XML_KEYWORDS = {"Airport Directory": "Directory"}


def _cycles_on_display(sd_card: Path) -> list:
    """
    Database descriptions the avionics reports as loaded, from the card's
    Garmin/GarminDevice.xml (written by the display itself), e.g.
    ["USA-VFR Navigation Data 2607", "Worldwide-9 Terrain 20T1", ...].
    """
    import xml.etree.ElementTree as ET

    xml_path = sd_card / "Garmin" / "GarminDevice.xml"
    if not xml_path.exists():
        return []
    try:
        root = ET.parse(xml_path).getroot()
    except Exception as e:
        click.echo(f"Warning: could not read {xml_path.name} ({e})", err=True)
        return []
    descriptions = []
    for el in root.iter():
        if el.tag.endswith("UpdateFile"):
            for child in el:
                if child.tag.endswith("Description") and (child.text or "").strip():
                    descriptions.append(child.text.strip())
    return descriptions


def _on_display(entry: dict, descriptions: list) -> bool:
    """True if the display already has this entry's database at this cycle."""
    issue = entry.get("issue_name", "")
    avdb = entry.get("avdb_type", "")
    keyword = _DEVICE_XML_KEYWORDS.get(avdb, avdb).lower()
    return bool(issue) and any(
        d.split()[-1] == issue and keyword in d.lower() for d in descriptions
    )


def _build_device_type_map(api, devices: list) -> tuple:
    """
    Map each device name to its avionics productID (= TAW header
    database_type = feat_unlk security_id), using the productID on the
    device itself, else /device-models/ by name.

    Returns (device_type_map, primary_db_type).
    """
    primary_db_type = None
    device_type_map: dict = {}  # name → productID, for all devices given
    plan_devices = list({id(dev): dev for dev in devices if dev.name}.values())
    # First: use productID directly from the device object if the API returned it
    for dev in plan_devices:
        if dev.product_id is not None:
            device_type_map[dev.name] = dev.product_id
            if primary_db_type is None:
                primary_db_type = dev.product_id
            click.echo(f"Device model '{dev.name}' → productID={dev.product_id} (0x{dev.product_id:04X})")
    # Second: for devices still missing, cross-reference /device-models/ by name
    unresolved_names = [dev.name for dev in plan_devices if dev.name not in device_type_map]
    if unresolved_names:
        try:
            device_models = api.list_device_models()
            model_map = {
                m["name"].lower(): m["productID"]
                for m in device_models
                if m.get("name") and m.get("productID") is not None
            }
            for dname in unresolved_names:
                product_id = model_map.get(dname.lower())
                if product_id is not None:
                    device_type_map[dname] = product_id
                    if primary_db_type is None:
                        primary_db_type = product_id
                    click.echo(f"Device model '{dname}' → productID={product_id} (0x{product_id:04X})")
            still_unresolved = [n for n in unresolved_names if n not in device_type_map]
            if still_unresolved:
                click.echo(f"Warning: device name(s) {still_unresolved!r} not found in /device-models/ — falling back to frequency heuristic")
        except Exception as e:
            click.echo(f"Warning: /device-models/ lookup failed ({e}) — falling back to frequency heuristic")
    return device_type_map, primary_db_type


def _system_id_for_card(manifest: dict, entries: list) -> int:
    """
    The avionics System ID to sign this card's feat_unlk.dat slots with.

    An aircraft can have several devices (e.g. a G3X Touch and a GTN), each
    with its own System ID, and the display rejects slots signed with
    another unit's ID. Use the ID of the device the installed entries belong
    to; fall back to the manifest's only ID, or (with a warning) its first.
    """
    by_device = manifest.get("device_system_ids") or {}
    ids = {by_device[str(e.get("device_id"))] for e in entries
           if str(e.get("device_id")) in by_device}
    if len(ids) == 1:
        return ids.pop()
    system_ids = manifest.get("system_ids", [])
    if len(system_ids) == 1:
        return system_ids[0]
    if system_ids:
        click.echo(
            "Warning: the download lists several avionics System IDs and doesn't say "
            "which belongs to this card's device — using the first. Re-run "
            "'avcardtool navdata download' to record them per device.",
            err=True,
        )
        return system_ids[0]
    return 0


def _filter_entries_for_avionics(entries: list, target_db_type: int) -> tuple:
    """
    Split manifest entries into (for this card's avionics, for other avionics).

    A device's subscription can include TAW files for other avionics families
    (e.g. a GTN's series also serving G600 'bdg600-*' obstacle files). Keep:
      * TAW files whose header database_type matches the card's avionics;
      * other files (.jnx/.hif charts and rasters, or a TAW whose type could
        not be read) only if they belong to a device that has at least one
        matching TAW file.
    """
    matching_devices = {
        e.get("device_id") for e in entries
        if e.get("taw_database_type") == target_db_type
    }
    keep, skip = [], []
    for e in entries:
        db_type = e.get("taw_database_type")
        if db_type is not None:
            ok = db_type == target_db_type
        else:
            ok = e.get("device_id") in matching_devices
        (keep if ok else skip).append(e)
    return keep, skip


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
    if cfg.system.dry_run:
        # auto-update never reaches here in a dry run; this catches a direct
        # `avcardtool --dry-run navdata install`, which would otherwise write.
        click.echo("DRY RUN: 'navdata install' writes to the SD card — refusing. "
                   "Use 'avcardtool --dry-run navdata auto-update' to preview an update.", err=True)
        sys.exit(1)
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
    # Which avionics is this card for? Only its databases get installed.
    # ---------------------------------------------------------------
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

    entries, skipped = _filter_entries_for_avionics(entries, target_db_type)
    if skipped:
        names = sorted({f"{e['avdb_type']} ({Path(e['local_path']).name})" for e in skipped})
        click.echo(f"Skipping {len(skipped)} file(s) for other avionics: "
                   + ", ".join(names[:8]) + (" …" if len(names) > 8 else ""))
    if not entries:
        click.echo(
            f"Nothing in the download is for this card's avionics (db_type 0x{target_db_type:04X}).",
            err=True,
        )
        sys.exit(1)
    # Skip databases the display already has at this cycle, as GADM does
    # (e.g. terrain 20T1, which never changes, is not re-installed).
    on_display = _cycles_on_display(sd_card)
    already = [e for e in entries if _on_display(e, on_display)]
    if already:
        names = sorted({f"{e['avdb_type']} {e['issue_name']}" for e in already})
        click.echo("Already on the display (per Garmin/GarminDevice.xml), skipping: "
                   + ", ".join(names))
        entries = [e for e in entries if e not in already]
    if not entries:
        click.echo("Everything in the download is already on the display — nothing to install.")
        sys.exit(0)

    # Every later step (cleanup, feat_unlk.dat, flyGarmin install report)
    # must only see this card's databases.
    manifest = {**manifest, "entries": entries}

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
                    from avcardtool.navdata.garmin.taw_parser import _clear_readonly
                    _clear_readonly(target)
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

        if suffix == ".taw":
            # Extract TAW archive — files go to paths determined by region headers.
            # Parse the header first to get security_id (database_type), which is
            # needed later when writing feat_unlk.dat.
            click.echo(f"  Extracting {local_path.name}  ({avdb_type})")
            try:
                taw_info = extractor.list_contents(local_path)
                # The manifest's type is the avionics this file was selected for. It
                # equals the header's type, except for a file Garmin serves to a device
                # whose header names another family (e.g. the GTN Xi's nav data), which
                # must be signed with the card's own type.
                security_id = entry.get("taw_database_type") or taw_info.header.database_type
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
            from avcardtool.navdata.garmin.taw_parser import _set_hidden, _clear_readonly
            if dest_path.exists():
                _clear_readonly(dest_path)
            shutil.copy2(local_path, dest_path)
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
        get_vol_id_from_sd_card, encode_volume_id, truncate_system_id,
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
        system_id: int = _system_id_for_card(manifest, entries)
        if system_id:
            click.echo(f"Avionics System ID: 0x{system_id:X} "
                       f"→ feat_unlk value 0x{truncate_system_id(system_id):08X}")
        else:
            click.echo("\nWarning: no avionics System ID in manifest — feat_unlk.dat "
                       "will be signed with 0 and the G3X will reject it as "
                       "'System ID not correct'. Re-run 'navdata download' after "
                       "updating so the device systemId is captured.", err=True)

        feat_unlk_count = 0
        for installed_file in installed_files:
            # security_id comes from the TAW header (database_type field).
            # We stored it per-entry when building extracted_security_ids above.
            # Files copied rather than extracted from a TAW (e.g. a rasters
            # file served as-is) belong to this card's avionics too.
            entry_sec_id = _extracted_security_ids.get(str(installed_file), target_db_type)
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
    # GarminDevice.xml is deliberately left alone: it is the avionics' own
    # description of itself (written by the display), and GADM doesn't
    # update it on install either — a GADM-updated card still lists the
    # previous cycles there.
    # ---------------------------------------------------------------

    # ---------------------------------------------------------------
    # Step 4: Write .gadm.meta
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
        if not garmin_account:
            # Only fill it in when missing: GADM writes the account's display
            # name ("First Last"), which the login token doesn't know.
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
            # UTC, matching the "GMT+0000" label (was local time mislabelled)
            "gadmLastInstalled": _dt.datetime.now(_dt.timezone.utc).strftime(
                "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
            ),
            "gadmAvionicsName": existing_gadm.get("gadmAvionicsName", "G3X Touch"),
            "gadmCardDescription": existing_gadm.get("gadmCardDescription"),
            "gadmTailNumber": tail_number,
            "gadmFleetMetadata": existing_gadm.get("gadmFleetMetadata"),
        }
        if gadm_meta_path.exists():
            from avcardtool.navdata.garmin.taw_parser import _clear_readonly
            _clear_readonly(gadm_meta_path)
        gadm_meta_path.write_text(json.dumps(gadm))
        click.echo("Updated .gadm.meta")
    except Exception as e:
        click.echo(f"Warning: could not update .gadm.meta: {e}", err=True)

    # ---------------------------------------------------------------
    # Step 6: Report installed issues to flyGarmin
    #
    # PUT /devices/installed-issues tells the server which cycles are
    # now on the card so fly.garmin.com shows the correct status and
    # subscription tracking stays up to date.  Needs the BatchUpdate
    # session from download, which is currently disabled (it returned 500),
    # so this only runs for manifests that still carry a batch_id.
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


@navdata.command('auto-update')
@click.argument('device', type=click.Path(path_type=Path, readable=False), required=False)
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

    With the global --dry-run, steps 1-2 run against the real card (mounted
    read-only) and flyGarmin, and the plan is reported, but nothing is
    unlocked, downloaded, or written to the card.
    """
    import datetime
    import logging as _logging

    _log = _logging.getLogger("avcardtool.navdata.auto_update")

    cfg = ctx.obj['config']
    data_dir = Path(cfg.system.data_dir)
    dry_run = cfg.system.dry_run
    if dry_run:
        _log.info("DRY RUN: reporting the update plan only — no unlock, download, or card writes")

    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError
    from avcardtool.navdata.garmin.api import FlyGarminAPI
    from avcardtool.navdata.garmin.taw_parser import TAWParser
    from avcardtool.navdata.sdcard import SDCardDetector
    from avcardtool.notifications.events import (
        build_garmin_auth_expired_event,
        build_navdata_update_failed_event,
        build_navdata_updated_event,
    )

    notify_manager = _notification_manager(cfg)

    # ---------------------------------------------------------------
    # Authenticate
    # ---------------------------------------------------------------
    auth = GarminAuth(token_dir=data_dir)
    if not auth.ensure_authenticated():
        if auth.tokens.access_token:
            _log.error("Garmin access token expired. Re-run 'avcardtool navdata login' to re-authenticate.")
        else:
            _log.error("Not authenticated. Run 'avcardtool navdata login' first.")
        # The daily re-check would otherwise fail silently forever; the
        # manager rate-limits this event to one notification per 24h.
        if notify_manager:
            notify_manager.notify(build_garmin_auth_expired_event())
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

    device_type_map: dict = {}
    # Use productID directly from the API response if available
    for dev in ac.devices:
        if dev.product_id is not None:
            device_type_map[dev.name] = dev.product_id
    # Fall back to /device-models/ name matching for devices without a direct productID
    unresolved_devs = [dev for dev in ac.devices if dev.name not in device_type_map]
    if unresolved_devs:
        try:
            device_models = api.list_device_models()
            model_map = {
                m["name"].lower(): m["productID"]
                for m in device_models
                if m.get("name") and m.get("productID") is not None
            }
            for dev in unresolved_devs:
                product_id = model_map.get(dev.name.lower())
                if product_id is not None:
                    device_type_map[dev.name] = product_id
        except Exception as e:
            _log.warning(f"Could not fetch device models: {e}")

    # ---------------------------------------------------------------
    # Find SD cards
    # ---------------------------------------------------------------
    detector = SDCardDetector()
    _we_mounted: Optional[str] = None
    _we_remounted: list = []  # cards we remounted rw that should be unmounted when done

    if device:
        from avcardtool.core import resolve_device_mount_point, get_mount_point
        already_mounted = get_mount_point(device) is not None
        try:
            mount = resolve_device_mount_point(device, readonly=dry_run)
        except RuntimeError as e:
            _log.error(f"Could not mount {device}: {e}")
            sys.exit(1)
        if not already_mounted:
            _we_mounted = str(mount)
        all_cards = detector.scan_for_cards()
        cards = [c for c in all_cards if c.device_path == str(device)]
    else:
        cards = detector.scan_for_cards()

    if not cards:
        _log.info("No SD cards found.")
        return

    # ---------------------------------------------------------------
    # Process each card
    # ---------------------------------------------------------------
    issues: list = []  # (severity, message) pairs collected for end-of-run summary

    for card in cards:
        if not card.mount_point:
            _log.info(f"Skipping {card.device_path}: not mounted")
            continue

        # udisks2 sometimes auto-mounts FAT volumes read-only (e.g. dirty bit).
        # Ensure we have read-write access before attempting any writes.
        # A dry run only reads, so it uses the existing mount as-is.
        from avcardtool.core import resolve_device_mount_point
        from avcardtool.core.utils import _is_mounted_readonly
        if dry_run:
            mount = Path(card.mount_point)
        else:
            was_readonly = _is_mounted_readonly(Path(card.device_path))
            try:
                mount = resolve_device_mount_point(Path(card.device_path), readonly=False)
            except RuntimeError as e:
                _log.error(f"  Could not get read-write mount for {card.device_path}: {e}")
                issues.append(("ERROR", f"Mount failed for {card.device_path}: {e}"))
                continue
            if was_readonly:
                _we_remounted.append(str(mount))

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
            unresolved = [d for d in ac.devices if d.name not in device_type_map]
            if len(unresolved) == 1:
                target_dev = unresolved[0]
                device_type_map[target_dev.name] = target_db_type
                _log.info(
                    f"  Note: '{target_dev.name}' not in device model list by name "
                    f"— using card's declared db_type 0x{target_db_type:04X}"
                )
            else:
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
        #
        # Entitlement check: skip a series entirely when installable_issues is
        # empty AND issues_remaining is 0.  The aircraft API returns avdb_types
        # for every database the device *supports*, not just ones the account is
        # subscribed to.  Without this guard we would attempt to download and
        # unlock databases the user has never purchased (e.g. Basemap on a GTN),
        # resulting in 403 unlock errors and wasted downloads.
        plan = []  # [(avdb, series, issue, old_issue, is_upcoming)]
        already_current = []  # [{"database", "issue"}] for the notification
        for avdb in target_dev.avdb_types:
            installed_issue = installed_cycles.get(avdb.name, {}).get("issue")
            for s in avdb.series:
                installable_names = {i.name for i in s.installable_issues}

                # No installable issues and no remaining subscription credits
                # means the user is not entitled to this database — skip it.
                if not s.installable_issues and s.issues_remaining == 0:
                    continue

                if s.installable_issues:
                    current = s.installable_issues[0]
                    if current.name != installed_issue:
                        _log.info(f"  {avdb.name}: need current {current.name} (have {installed_issue or 'none'})")
                        plan.append((avdb, s, current, installed_issue, False))
                    else:
                        already_current.append({"database": avdb.name, "issue": current.name})

                for issue in s.available_issues:
                    if issue.name not in installable_names:
                        if issue.name != installed_issue:
                            _log.info(f"  {avdb.name}: pre-downloading next cycle {issue.name}")
                            plan.append((avdb, s, issue, installed_issue, True))
                        break  # only the earliest upcoming cycle

        if not plan:
            _log.info(f"  All databases current — nothing to do.")
            continue

        card_serial = card.volume_id or "0"

        if dry_run:
            _report_navdata_dry_run(
                _log, notify_manager, plan, already_current, mount,
                data_dir / "navdata" / "cache", card_serial, target_dev.name,
                ac.tail_number, target_db_type,
            )
            continue

        # ---------------------------------------------------------------
        # Download with shared cache + coordination
        # ---------------------------------------------------------------
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

        manifest_entries = []
        unavailable: list = []  # current issues Garmin lists no file for on this card
        # (avdb name, issue name) → cycle-transition info for the notification
        plan_info = {
            (avdb.name, issue.name): {
                "old_issue": old_issue,
                "upcoming": is_upcoming,
                "effective_at": issue.effective_at,
            }
            for avdb, s, issue, old_issue, is_upcoming in plan
        }
        import time as _time

        for avdb, s, issue, old_issue, is_upcoming in plan:
            # Include series_id in the cache key so that different avionics
            # families that share the same avdb name and cycle (e.g. GTN series
            # 2230 vs G3X series 2239 both named "Navigation Data/2603") get
            # separate cache slots and don't serve each other's TAW files.
            cache_key = _dl_cache_key(s.series_id, issue.name, target_db_type)
            issue_cache_dir = cache_dir / str(s.series_id) / issue.name

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

            # slot == "mine" — download now. List files first (no unlock
            # needed) and keep only this card's avionics before unlocking.
            try:
                issue_files = api.list_files(s.series_id, issue.name)
            except Exception as e:
                msg = f"File list failed for {cache_key}: {e}"
                _log.error(f"  {msg}")
                issues.append(("ERROR", msg))
                _release_dl_slot(cache_dir, cache_key, [], {}, [], avdb.type_id, s.series_id, success=False)
                continue

            # Garmin's list for this device's series is the device's own files.
            card_files = issue_files.main_files + issue_files.auxiliary_files
            if not card_files:
                _log.info(f"  {cache_key}: nothing for this card's avionics — skipped")
                # Not cached as done: Garmin may list the file later, and the
                # next run should notice. (The check is one small API call.)
                _release_dl_slot(cache_dir, cache_key, [], {}, issue_files.removable_paths,
                                 avdb.type_id, s.series_id, success=False)
                if not is_upcoming:
                    unavailable.append(f"{avdb.name} {issue.name}")
                continue

            # Cheap pre-check, before unlocking or downloading anything: if every
            # TAW's database CRC (read with tiny Range requests) already matches
            # what the card's feat_unlk.dat records, the card has this exact
            # version, e.g. Terrain installed by Garmin's own tool, which leaves
            # no .navdata_cycles.json entry. Don't fetch gigabytes for it.
            if feat_unlk_crcs and all(
                f.file_name.lower().endswith(".taw") for f in card_files
            ):
                remote_crcs = [api.peek_taw_feature_crcs(f) for f in card_files]
                if all(
                    r and all(feat_unlk_crcs.get(k) == v for k, v in r.items())
                    for r in remote_crcs
                ):
                    _log.info(
                        f"  {avdb.name}/{issue.name}: card's feat_unlk CRCs already "
                        f"match — already installed, skipping download"
                    )
                    # Not marked complete: another card may still need these files.
                    _release_dl_slot(cache_dir, cache_key, [], {}, [],
                                     avdb.type_id, s.series_id, success=False)
                    continue

            try:
                api.unlock(s.series_id, issue.name, target_dev.device_id)
            except Exception as e:
                msg = f"Unlock failed for {cache_key}: {e}"
                _log.warning(f"  {msg}")
                issues.append(("WARNING", msg))

            downloaded: list = []
            file_entries: list = []
            for db_file in card_files:
                try:
                    dest = api.download_file(db_file, issue_cache_dir)
                    _log.info(f"  Downloaded {dest.name} ({dest.stat().st_size:,} bytes)")

                    # Record the card's own avionics type (not the header's: a GTN's
                    # nav data is 'jg600a-*.taw', header type G600): install matches
                    # on it and signs feat_unlk.dat with it.
                    taw_db_type = target_db_type if dest.suffix.lower() == ".taw" else None
                    if taw_db_type is not None:
                        try:
                            header_type = TAWParser().parse(dest).header.database_type
                            if header_type != target_db_type:
                                _log.info(f"  {dest.name}: header type 0x{header_type:04X}, "
                                          f"installing as 0x{target_db_type:04X}")
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
                    msg = f"Download failed for {db_file.file_name}: {e}"
                    _log.error(f"  {msg}")
                    issues.append(("ERROR", msg))

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

        for what in unavailable:
            msg = (f"{what} is available but Garmin's file list has no file for "
                   f"{target_dev.name} (card {card_serial}) — not installed")
            _log.error(f"  {msg}")
            issues.append(("ERROR", msg))

        if not manifest_entries:
            _log.info(f"  Nothing to install for card at {mount}")
            continue

        # Write per-card manifest (files are in card_dir, local_path relative to card_dir)
        system_ids = [target_dev.system_id_raw] if target_dev.system_id_raw is not None else []
        manifest_path = card_dir / "navdata_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "downloaded_at": datetime.datetime.now().isoformat(),
                "aircraft": ac.tail_number,
                "card_serial": card_serial,
                "system_ids": system_ids,
                "device_database_type": target_db_type,
                "device_type_map": device_type_map,
                "entries": manifest_entries,
            }, f, indent=2)

        _log.info(f"  Installing to {mount}...")
        install_ok = True
        try:
            ctx.invoke(navdata_install, sd_card=mount, from_dir=card_dir, yes=True)
        except SystemExit as e:
            if e.code != 0:
                install_ok = False
                msg = f"Install failed for card at {mount} (exit {e.code})"
                _log.error(f"  {msg}")
                issues.append(("ERROR", msg))
        except Exception as e:
            install_ok = False
            msg = f"Install raised for card at {mount}: {e}"
            _log.error(f"  {msg}")
            issues.append(("ERROR", msg))

        if install_ok and notify_manager:
            # One entry per unique database/cycle actually staged for this card
            installed_dbs = []
            seen_installs: set = set()
            for entry in manifest_entries:
                key = (entry["avdb_type"], entry["issue_name"])
                if key in seen_installs:
                    continue
                seen_installs.add(key)
                info = plan_info.get(key, {})
                installed_dbs.append({
                    "database": entry["avdb_type"],
                    "old_issue": info.get("old_issue"),
                    "new_issue": entry["issue_name"],
                    "effective_at": info.get("effective_at"),
                    "upcoming": info.get("upcoming", False),
                })
            notify_manager.notify(build_navdata_updated_event(
                card_serial=card_serial,
                avionics=target_dev.name,
                aircraft=ac.tail_number,
                installed=installed_dbs,
                already_current=already_current,
                file_count=len(manifest_entries),
            ))

    # ---------------------------------------------------------------
    # End-of-run summary
    # ---------------------------------------------------------------
    if issues:
        _log.error("=" * 60)
        _log.error("NAVDATA UPDATE COMPLETED WITH ISSUES (%d):", len(issues))
        for severity, msg in issues:
            _log.error("  [%s] %s", severity, msg)
        _log.error("=" * 60)
        # WARNINGs alone don't warrant an alert; they ride along when an
        # ERROR fires the event.
        if notify_manager and any(sev == "ERROR" for sev, _ in issues):
            notify_manager.notify(build_navdata_update_failed_event(issues))
    else:
        _log.info("Navdata dry run completed." if dry_run else "Navdata update completed successfully.")

    if not dry_run:
        try:
            freed = _prune_navdata_cache(data_dir / "navdata" / "cache", log=_log)
            if freed:
                _log.info(f"Navdata cache pruned: freed {freed / 1e6:,.0f} MB")
        except Exception as e:
            _log.warning(f"Navdata cache pruning failed: {e}")

    if _we_mounted:
        try:
            detector.unmount_card(_we_mounted)
        except Exception as e:
            _log.warning(f"Could not unmount {_we_mounted}: {e}")

    for mount_point in _we_remounted:
        try:
            detector.unmount_card(mount_point)
            _log.info(f"Unmounted {mount_point}")
        except Exception as e:
            _log.warning(f"Could not unmount {mount_point}: {e}")


def _dl_cache_key(series_id: int, issue_name: str, db_type: Optional[int]) -> str:
    """
    Download-cache key for one series/issue as fetched for one avionics type.

    A series' file list can hold TAWs for several avionics families and only
    the card's own are downloaded, so the cached file list is per avionics
    type. (Files themselves live in the shared cache/<series>/<issue>/ dir.)
    """
    key = f"{series_id}/{issue_name}"
    return f"{key}@{db_type:04X}" if db_type is not None else key


def _report_navdata_dry_run(
    _log, notify_manager, plan: list, already_current: list, mount: Path,
    cache_dir: Path, card_serial: str, avionics: str, aircraft: Optional[str],
    target_db_type: Optional[int] = None,
) -> None:
    """
    Report what navdata auto-update would install on one card, without
    unlocking, downloading, or writing anything.

    Mirrors the real run's cache check: an issue already downloaded whose
    feat_unlk.dat CRC matches the card would be skipped there, so it is
    reported as skipped here too. Everything else would be downloaded (or
    reused from cache) and installed.
    """
    from avcardtool.notifications.events import build_navdata_updated_event

    feat_unlk_crcs = _read_feat_unlk_crcs(mount)
    dl_state = _read_dl_state(cache_dir) if cache_dir.exists() else {}

    would_install = []
    seen: set = set()
    for avdb, s, issue, old_issue, is_upcoming in plan:
        key = (avdb.name, issue.name)
        if key in seen:
            continue
        seen.add(key)

        cache_key = _dl_cache_key(s.series_id, issue.name, target_db_type)
        cached = dl_state.get(cache_key, {})
        feat_name = _AVDB_TO_FEAT_UNLK.get(avdb.name)
        if (cached.get("status") == "complete"
                and feat_name
                and feat_unlk_crcs.get(feat_name)
                and cached.get("feature_crcs", {}).get(feat_name) == feat_unlk_crcs[feat_name]):
            _log.info(f"  [DRY RUN] {avdb.name}/{issue.name}: feat_unlk CRC matches cache — would skip")
            continue

        source = "from cache" if cached.get("status") == "complete" else "download"
        label = "pre-load upcoming" if is_upcoming else "install"
        _log.info(
            f"  [DRY RUN] would {label} {avdb.name}: {old_issue or 'none'} → {issue.name} "
            f"({source}, effective {issue.effective_at or 'unknown'})"
        )
        would_install.append({
            "database": avdb.name,
            "old_issue": old_issue,
            "new_issue": issue.name,
            "effective_at": issue.effective_at,
            "upcoming": is_upcoming,
        })

    if not would_install:
        _log.info(f"  [DRY RUN] Nothing would be installed on {mount}")
        return

    if notify_manager:
        notify_manager.notify(build_navdata_updated_event(
            card_serial=card_serial,
            avionics=avionics,
            aircraft=aircraft,
            installed=would_install,
            already_current=already_current,
        ))


def _fmt_size(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1_024:
        return f"{n / 1_024:.1f} KB"
    return f"{n} B"


@navdata.command('update')
@click.argument('device', type=click.Path(path_type=Path, readable=False), required=False)
@click.option('--aircraft', '-a', type=int, default=0, show_default=True,
              help='Aircraft index (from list-databases)')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompt')
@click.pass_context
def navdata_update(ctx, device: Optional[Path], aircraft: int, yes: bool):
    """
    Interactively update navigation databases on SD card(s).

    Shows a plan for each connected card — what is installed, what will be
    downloaded, and file sizes — then asks for a single confirmation before
    proceeding.

    DEVICE can be a block device (e.g. /dev/sda1) or a mount point to target
    a specific card.  If omitted, all detected SD cards are processed.
    """
    import datetime
    import time as _time
    from avcardtool.navdata.garmin.auth import GarminAuth, GarminAPIError
    from avcardtool.navdata.garmin.api import FlyGarminAPI
    from avcardtool.navdata.garmin.taw_parser import TAWParser
    from avcardtool.navdata.sdcard import SDCardDetector

    cfg = ctx.obj['config']
    data_dir = Path(cfg.system.data_dir)

    # --- Authenticate ---
    auth = GarminAuth(token_dir=data_dir)
    if not auth.ensure_authenticated():
        msg = (
            "Garmin access token expired. Run 'avcardtool navdata login' to re-authenticate."
            if auth.tokens.access_token
            else "Not authenticated. Run 'avcardtool navdata login' first."
        )
        click.echo(msg, err=True)
        sys.exit(1)

    api = FlyGarminAPI(auth)

    # --- Fetch aircraft + device models (shared across all cards) ---
    click.echo("Fetching aircraft and database information from flyGarmin...")
    try:
        aircraft_list = api.list_aircraft()
    except GarminAPIError as e:
        click.echo(f"API error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)

    if not aircraft_list:
        click.echo("No aircraft found on this account.", err=True)
        sys.exit(1)

    if aircraft >= len(aircraft_list):
        click.echo(f"Aircraft index {aircraft} out of range (0–{len(aircraft_list)-1}).", err=True)
        sys.exit(1)

    ac = aircraft_list[aircraft]

    device_type_map: dict = {}
    # Use productID directly from the API response if available
    for dev in ac.devices:
        if dev.product_id is not None:
            device_type_map[dev.name] = dev.product_id
    # Fall back to /device-models/ name matching for devices without a direct productID
    unresolved_devs = [dev for dev in ac.devices if dev.name not in device_type_map]
    if unresolved_devs:
        try:
            device_models = api.list_device_models()
            model_map = {
                m["name"].lower(): m["productID"]
                for m in device_models
                if m.get("name") and m.get("productID") is not None
            }
            for dev in unresolved_devs:
                product_id = model_map.get(dev.name.lower())
                if product_id is not None:
                    device_type_map[dev.name] = product_id
        except Exception as e:
            click.echo(f"Warning: could not fetch device models: {e}", err=True)

    click.echo(f"Registered devices on {ac.tail_number}:")
    for dev in ac.devices:
        pid = device_type_map.get(dev.name)
        pid_str = f"  db_type=0x{pid:04X}" if pid is not None else "  (product ID unresolved)"
        click.echo(f"  • {dev.name}{pid_str}")

    # --- Collect cards to process ---
    detector = SDCardDetector()
    # _we_mounted: mount points we brought up that should be torn down on exit
    _we_mounted: list = []

    # Each entry: {'mount': Path, 'card_serial': str, 'device_path': str|None}
    raw_cards: list = []

    if device:
        dev_path = Path(device)
        if dev_path.is_block_device():
            from avcardtool.core import resolve_device_mount_point, get_mount_point
            already_mounted = get_mount_point(dev_path) is not None
            try:
                mount = resolve_device_mount_point(dev_path, readonly=False)
            except RuntimeError as e:
                click.echo(f"Could not mount {dev_path}: {e}", err=True)
                sys.exit(1)
            if not already_mounted:
                _we_mounted.append(str(mount))
            all_scanned = detector.scan_for_cards()
            card_info = next((c for c in all_scanned if c.device_path == str(dev_path)), None)
            serial = card_info.volume_id if card_info and card_info.volume_id else "0"
            raw_cards.append({'mount': mount, 'card_serial': serial, 'device_path': str(dev_path)})
        else:
            raw_cards.append({'mount': dev_path.resolve(), 'card_serial': "0", 'device_path': None})
    else:
        scanned = detector.scan_for_cards()
        candidates = [c for c in scanned if c.mount_point]
        # Prefer Garmin-formatted cards; fall back to any FAT32 card
        if not candidates:
            click.echo("No SD cards found. Insert a card or specify the device path.", err=True)
            sys.exit(1)
        garmin_first = sorted(candidates, key=lambda c: (0 if c.is_garmin else 1))
        from avcardtool.core import resolve_device_mount_point, get_mount_point
        from avcardtool.core.utils import _is_mounted_readonly
        for c in garmin_first:
            dev_path = Path(c.device_path) if c.device_path else None
            mount = Path(c.mount_point)
            if dev_path and dev_path.is_block_device():
                if _is_mounted_readonly(dev_path):
                    click.echo(f"  {mount}: mounted read-only, remounting rw...")
                try:
                    mount = resolve_device_mount_point(dev_path, readonly=False)
                except RuntimeError as e:
                    click.echo(f"  Could not get rw mount for {dev_path}: {e}", err=True)
                    continue
            raw_cards.append({
                'mount': mount,
                'card_serial': c.volume_id or "0",
                'device_path': c.device_path,
            })
        click.echo(f"Found {len(raw_cards)} SD card(s).")

    # --- Build a plan for each card ---
    # File info is fetched once per (series_id, issue_name) and reused across cards.
    file_info_cache: dict = {}   # (series_id, issue_name) -> (files, removable, size)

    # card_plans: list of dicts, one per card that has something to do
    card_plans: list = []
    cards_current: list = []  # mount points of cards already up to date

    mock_manifest = {
        "device_type_map": device_type_map,
        "entries": [{"taw_database_type": pid} for pid in device_type_map.values()],
    }

    for raw in raw_cards:
        mount = raw['mount']
        card_serial = raw['card_serial']

        if not mount.exists():
            click.echo(f"Warning: {mount} does not exist — skipping.", err=True)
            continue

        # Avionics type
        target_db_type = _resolve_target_db_type(mount, mock_manifest)
        if target_db_type is None:
            if device_type_map:
                names = ", ".join(f"'{n}'" for n in device_type_map)
                click.echo(
                    f"Warning: cannot determine avionics for {mount}.\n"
                    f"  Create 'avionics.txt' there with one of: {names}",
                    err=True,
                )
            else:
                click.echo(f"Warning: no device model data for {mount} — skipping.", err=True)
            continue

        target_dev = next(
            (d for d in ac.devices if device_type_map.get(d.name) == target_db_type),
            None,
        )
        if target_dev is None:
            # device_type_map is built from model_map name matches, which can miss
            # devices whose name in list_aircraft() differs from list_device_models().
            # If exactly one registered device couldn't be resolved by name, trust the
            # card's declared db_type (from feat_unlk.dat) and pair them.
            unresolved = [d for d in ac.devices if d.name not in device_type_map]
            if len(unresolved) == 1:
                target_dev = unresolved[0]
                device_type_map[target_dev.name] = target_db_type
                click.echo(
                    f"  Note: '{target_dev.name}' not found in Garmin device model list "
                    f"by name — using card's declared db_type 0x{target_db_type:04X}."
                )
            else:
                registered = ", ".join(
                    f"{d.name} (0x{device_type_map[d.name]:04X})"
                    for d in ac.devices if d.name in device_type_map
                ) or "(none resolved)"
                click.echo(
                    f"Warning: db_type 0x{target_db_type:04X} on {mount} matched no "
                    f"registered device for {ac.tail_number}.\n"
                    f"  Registered devices: {registered}\n"
                    f"  Create avionics.txt on the card with the device name to override.",
                    err=True,
                )
                continue

        # Read installed cycles
        installed_cycles: dict = {}
        cycles_file = mount / ".navdata_cycles.json"
        if cycles_file.exists():
            try:
                installed_cycles = json.loads(cycles_file.read_text()).get("cycles", {})
            except Exception:
                pass

        # Build plan
        plan = []
        current_ok = []
        for avdb in target_dev.avdb_types:
            installed_issue = installed_cycles.get(avdb.name, {}).get("issue")
            for s in avdb.series:
                if not s.installable_issues and s.issues_remaining == 0:
                    continue
                if s.installable_issues:
                    current = s.installable_issues[0]
                    if current.name != installed_issue:
                        plan.append((avdb, s, current, installed_issue, "update"))
                    else:
                        key = (avdb.name, installed_issue)
                        if key not in {(n, i) for n, i in current_ok}:
                            current_ok.append((avdb.name, installed_issue))
                installable_names = {i.name for i in s.installable_issues}
                for issue in s.available_issues:
                    if issue.name not in installable_names:
                        if issue.name != installed_issue:
                            plan.append((avdb, s, issue, installed_issue, "pre-download"))
                        break

        if not plan:
            cards_current.append((mount, target_dev.name, current_ok))
            continue

        # Fetch file sizes (deduplicated across cards by cache key)
        plan_details = []
        total_bytes = 0
        all_removable: set = set()
        for avdb, s, issue, installed_issue, reason in plan:
            cache_key = (s.series_id, issue.name)
            if cache_key not in file_info_cache:
                try:
                    issue_files = api.list_files(s.series_id, issue.name)
                    files = issue_files.main_files + issue_files.auxiliary_files
                    removable = issue_files.removable_paths
                    size = sum(f.file_size for f in files if f.file_size)
                except Exception as e:
                    click.echo(f"  Warning: could not get file info for {avdb.name}/{issue.name}: {e}", err=True)
                    files = []
                    removable = []
                    size = 0
                file_info_cache[cache_key] = (files, removable, size)
            files, removable, size = file_info_cache[cache_key]
            all_removable.update(removable)
            plan_details.append((avdb, s, issue, installed_issue, reason, files, removable, size))
            total_bytes += size

        card_plans.append({
            'mount': mount,
            'card_serial': card_serial,
            'target_dev': target_dev,
            'target_db_type': target_db_type,
            'plan_details': plan_details,
            'current_ok': current_ok,
            'all_removable': all_removable,
            'total_bytes': total_bytes,
        })

    # --- Nothing to do? ---
    if not card_plans:
        if cards_current:
            click.echo("\nAll databases are up to date on all cards.")
            for mount, avionics, current_ok in cards_current:
                click.echo(f"\n  {mount}  ({avionics})")
                for name, cycle in current_ok:
                    click.echo(f"    ✓ {name}: {cycle}")
        else:
            click.echo("\nNo actionable SD cards found.")
        return

    # --- Display summary: one section per card ---
    W = 68
    grand_total = sum(cp['total_bytes'] for cp in card_plans)

    click.echo()
    click.echo("=" * W)
    click.echo("{:^{w}}".format("── Navdata Update Plan ──", w=W))
    click.echo("=" * W)
    click.echo(f"  Aircraft : {ac.tail_number}")
    click.echo(f"  Cards    : {len(card_plans)} to update" +
               (f", {len(cards_current)} already current" if cards_current else ""))

    C1, C2, C3, C4, C5 = 18, 10, 10, 10, 10
    row = "  {:<{c1}}  {:<{c2}}  {:<{c3}}  {:<{c4}}  {:>{c5}}"

    for i, cp in enumerate(card_plans, 1):
        click.echo()
        click.echo(f"  Card {i} of {len(card_plans)}: {cp['mount']}  (serial: {cp['card_serial']})")
        click.echo(f"  Avionics : {cp['target_dev'].name}")
        click.echo()
        click.echo(row.format("Database", "Installed", "New Cycle", "Action", "Size",
                              c1=C1, c2=C2, c3=C3, c4=C4, c5=C5))
        click.echo("  " + "─" * (C1 + C2 + C3 + C4 + C5 + 8))
        for avdb, s, issue, installed_issue, reason, files, removable, size in cp['plan_details']:
            installed_str = installed_issue or "(none)"
            action = "pre-DL" if reason == "pre-download" else "update"
            size_str = _fmt_size(size) if size else "—"
            click.echo(row.format(avdb.name, installed_str, issue.name, action, size_str,
                                  c1=C1, c2=C2, c3=C3, c4=C4, c5=C5))
        if cp['current_ok']:
            click.echo()
            click.echo("  Already up to date:")
            for name, cycle in cp['current_ok']:
                click.echo(f"    ✓ {name}: {cycle}")
        if cp['all_removable']:
            click.echo(f"\n  Old files to remove : {len(cp['all_removable'])}")
        click.echo(f"  Subtotal            : {_fmt_size(cp['total_bytes'])}")

    if cards_current:
        click.echo()
        click.echo(f"  Skipping {len(cards_current)} card(s) already up to date:")
        for mount, avionics, _ in cards_current:
            click.echo(f"    • {mount}  ({avionics})")

    click.echo()
    click.echo(f"  Grand total download : {_fmt_size(grand_total)}")
    click.echo("=" * W)
    click.echo()

    if not yes:
        n = len(card_plans)
        prompt = (
            f"Proceed with installation on {n} card(s)?"
            if n > 1
            else "Proceed with installation?"
        )
        click.confirm(prompt, abort=True)
        click.echo()

    # --- Download and install each card ---
    cache_dir = data_dir / "navdata" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    for cp in card_plans:
        mount = cp['mount']
        card_serial = cp['card_serial']
        target_dev = cp['target_dev']
        target_db_type = cp['target_db_type']
        plan_details = cp['plan_details']

        click.echo(f"\n{'─' * W}")
        click.echo(f"Processing {mount}  ({target_dev.name})")
        click.echo(f"{'─' * W}")

        card_dir = data_dir / "navdata" / f"update_{card_serial.replace('-', '')}"
        card_dir.mkdir(parents=True, exist_ok=True)

        feat_unlk_crcs = _read_feat_unlk_crcs(mount)

        manifest_entries = []
        crc_confirmed: dict = {}  # avdb.name -> issue.name for CRC-matched items

        for avdb, s, issue, installed_issue, reason, files, removable, size in plan_details:
            dl_cache_key = _dl_cache_key(s.series_id, issue.name, target_db_type)
            issue_cache_dir = cache_dir / str(s.series_id) / issue.name

            click.echo(f"\n  {avdb.name}  →  {issue.name}")

            slot = _acquire_dl_slot(cache_dir, dl_cache_key)

            if slot == "wait":
                click.echo("    Another process is downloading — waiting...")
                deadline = _time.time() + 1800
                while _time.time() < deadline:
                    _time.sleep(5)
                    slot = _acquire_dl_slot(cache_dir, dl_cache_key)
                    if slot != "wait":
                        break
                if slot == "wait":
                    click.echo("    Timed out — skipping.", err=True)
                    continue

            if slot == "cached":
                click.echo("    Using cached download.")
                dl_state = _read_dl_state(cache_dir)
                cached_crcs = dl_state.get(dl_cache_key, {}).get("feature_crcs", {})
                feat_name = _AVDB_TO_FEAT_UNLK.get(avdb.name)
                if (feat_name
                        and feat_unlk_crcs.get(feat_name)
                        and cached_crcs.get(feat_name)
                        and feat_unlk_crcs[feat_name] == cached_crcs[feat_name]):
                    click.echo("    Already installed (CRC match) — skipping.")
                    crc_confirmed[avdb.name] = issue.name
                    continue
                for file_entry in dl_state.get(dl_cache_key, {}).get("files", []):
                    src = cache_dir / file_entry["cache_path"]
                    dst = card_dir / file_entry["cache_path"]
                    _link_or_copy(src, dst)
                    manifest_entries.append({
                        "local_path": file_entry["cache_path"],
                        "destination": file_entry["destination"],
                        "avdb_type": avdb.name,
                        "avdb_type_id": dl_state[dl_cache_key].get("avdb_type_id", avdb.type_id),
                        "series_id": dl_state[dl_cache_key].get("series_id", s.series_id),
                        "issue_name": issue.name,
                        "device_id": target_dev.device_id,
                        "removable_paths": dl_state[dl_cache_key].get("removable_paths", removable),
                        "unlock_codes": [],
                        "taw_database_type": file_entry.get("taw_database_type"),
                    })
                continue

            # slot == "mine" — download now. Get the file list and keep only
            # this card's avionics before unlocking.
            dl_files = files
            dl_removable = removable
            if not dl_files:
                try:
                    issue_files = api.list_files(s.series_id, issue.name)
                    dl_files = issue_files.main_files + issue_files.auxiliary_files
                    dl_removable = issue_files.removable_paths
                except Exception as e:
                    click.echo(f"    Error: could not get file list: {e}", err=True)
                    _release_dl_slot(cache_dir, dl_cache_key, [], {}, [], avdb.type_id, s.series_id, success=False)
                    continue

            if not dl_files:
                click.echo("    Nothing here for this card's avionics — skipped.")
                _release_dl_slot(cache_dir, dl_cache_key, [], {}, dl_removable,
                                 avdb.type_id, s.series_id, success=False)
                continue

            try:
                api.unlock(s.series_id, issue.name, target_dev.device_id)
                click.echo("    Unlocked.")
            except Exception as e:
                click.echo(f"    Warning: unlock failed ({e}) — attempting download anyway.", err=True)

            downloaded: list = []
            file_entries: list = []

            for db_file in dl_files:
                def _progress(done: int, total: int, fname: str = db_file.file_name) -> None:
                    pct = int(done / total * 100) if total else 0
                    click.echo(f"\r    Downloading {fname}: {pct}%  ", nl=False)

                try:
                    dest = api.download_file(db_file, issue_cache_dir, progress_callback=_progress)
                    click.echo(f"\r    Downloaded  {db_file.file_name}  ({dest.stat().st_size:,} bytes)  ")

                    # The card's own avionics type, not the header's (see auto-update).
                    taw_db_type = target_db_type if dest.suffix.lower() == ".taw" else None

                    cache_path = str(dest.relative_to(cache_dir))
                    downloaded.append(dest)
                    file_entries.append({
                        "cache_path": cache_path,
                        "destination": db_file.destination,
                        "taw_database_type": taw_db_type,
                    })
                    _link_or_copy(dest, card_dir / cache_path)
                    manifest_entries.append({
                        "local_path": cache_path,
                        "destination": db_file.destination,
                        "avdb_type": avdb.name,
                        "avdb_type_id": avdb.type_id,
                        "series_id": s.series_id,
                        "issue_name": issue.name,
                        "device_id": target_dev.device_id,
                        "removable_paths": dl_removable,
                        "unlock_codes": [],
                        "taw_database_type": taw_db_type,
                    })
                except Exception as e:
                    click.echo(f"\r    Error downloading {db_file.file_name}: {e}  ", err=True)

            feature_crcs: dict = {}
            for dest in downloaded:
                if dest.suffix.lower() == ".taw":
                    feature_crcs.update(_extract_taw_crcs(dest))

            _release_dl_slot(
                cache_dir, dl_cache_key,
                files=file_entries,
                feature_crcs=feature_crcs,
                removable_paths=dl_removable,
                avdb_type_id=avdb.type_id,
                series_id=s.series_id,
                success=bool(downloaded),
            )

        if not manifest_entries:
            click.echo(f"\n  Nothing new to install for {mount}.")
            if crc_confirmed:
                # The plan thought these were missing, but CRCs prove they're
                # current. Record them so the next plan shows them as up to date.
                try:
                    cycles_file = mount / ".navdata_cycles.json"
                    existing: dict = {}
                    if cycles_file.exists():
                        try:
                            existing = json.loads(cycles_file.read_text()).get("cycles", {})
                        except Exception:
                            pass
                    for db_name, issue_name in crc_confirmed.items():
                        existing[db_name] = {"issue": issue_name}
                    cycles_file.write_text(json.dumps({"cycles": existing}, indent=2))
                    click.echo(f"  Updated .navdata_cycles.json with {len(crc_confirmed)} confirmed cycle(s).")
                except Exception as e:
                    click.echo(f"  Warning: could not update .navdata_cycles.json: {e}", err=True)
            continue

        system_ids = [target_dev.system_id_raw] if target_dev.system_id_raw is not None else []
        manifest_path = card_dir / "navdata_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "downloaded_at": datetime.datetime.now().isoformat(),
                "aircraft": ac.tail_number,
                "card_serial": card_serial,
                "system_ids": system_ids,
                "device_database_type": target_db_type,
                "device_type_map": device_type_map,
                "entries": manifest_entries,
            }, f, indent=2)

        click.echo(f"\nInstalling to {mount}...")
        try:
            ctx.invoke(navdata_install, sd_card=mount, from_dir=card_dir, yes=True)
        except SystemExit as e:
            if e.code and e.code != 0:
                click.echo(f"Installation failed for {mount} (exit {e.code}).", err=True)
        except Exception as e:
            click.echo(f"Installation error for {mount}: {e}", err=True)

    for mp in _we_mounted:
        try:
            detector.unmount_card(mp)
        except Exception:
            pass


# ============================================================================
# Setup Wizard
# ============================================================================

@cli.command('setup')
@click.option(
    '--config-path',
    type=click.Path(path_type=Path),
    default=None,
    help='Where to write the config file (default: ~/.config/avcardtool/config.json or /etc/avcardtool/config.json when root)'
)
@click.pass_context
def setup_wizard(ctx, config_path: Optional[Path]):
    """
    Interactive post-install setup wizard.

    Walks through enabling flight log processing, navigation database updates,
    upload service credentials, and first-run behavior, then writes a config
    file ready for use.
    """
    from avcardtool.core.config import (
        Config, SystemConfig, FlightDataConfig, NavdataConfig,
        NotificationsConfig, EngineTimeConfig, AirframeTimeConfig, UploaderConfig
    )

    click.echo("\n" + "=" * 60)
    click.echo("  avcardtool setup wizard")
    click.echo("=" * 60)
    click.echo(
        "\nThis wizard will help you configure avcardtool.\n"
        "Press Enter to accept defaults shown in [brackets].\n"
    )

    # ------------------------------------------------------------------
    # Section 1: System settings
    # ------------------------------------------------------------------
    click.echo("── System Settings ───────────────────────────────────────")

    default_data_dir = str(Path.home() / ".local" / "share" / "avcardtool")
    data_dir = click.prompt("Data directory", default=default_data_dir)
    enable_debug = click.confirm("Enable debug mode?", default=False)

    # ------------------------------------------------------------------
    # Section 2: Flight log processing
    # ------------------------------------------------------------------
    click.echo("\n── Flight Log Processing ─────────────────────────────────")
    click.echo(
        "\nWhen an SD card is inserted, avcardtool can automatically parse your\n"
        "G3X flight logs, calculate Hobbs and Tach times, detect OOOI events,\n"
        "and upload to configured tracking services.\n"
    )

    auto_process_flights = click.confirm(
        "Enable automatic flight log processing?", default=True
    )

    uploaders = {}
    engine_time_cfg = EngineTimeConfig()
    airframe_time_cfg = AirframeTimeConfig()

    if auto_process_flights:
        # --- Hobbs / Tach settings ---
        click.echo("\n  Log Processing Settings:")
        click.echo(
            "  These decide how Hobbs and Tach are calculated between the times\n"
            "  your avionics records its own readings (each log's header), which\n"
            "  always win when available.\n"
            "\n"
            "  ⚠  Make them match your avionics' configuration (on a G3X:\n"
            "     Engine/Airframe configuration → Airframe Time and Engine Time).\n"
            "     If they don't, avcardtool's numbers drift from the avionics' —\n"
            "     e.g. counting Hobbs from engine start when the G3X counts only\n"
            "     airborne time overestimated Hobbs by about 16% on real logs.\n"
            "     See \"Matching Hobbs and Tach to your avionics\" in the README.\n"
        )

        hobbs_trigger = click.prompt(
            "  Hobbs time trigger — what starts the clock\n"
            "    oil_pressure = engine running (oil pressure up)\n"
            "    rpm          = engine running (RPM above threshold)\n"
            "    flight_time  = airborne only (match a G3X whose Airframe Time\n"
            "                   counts flight time)\n"
            "  Trigger",
            type=click.Choice(['oil_pressure', 'rpm', 'flight_time']),
            default='oil_pressure',
            show_choices=True
        )
        airframe_time_cfg.trigger = hobbs_trigger

        tach_mode = click.prompt(
            "  Tach time mode (the G3X's Engine Time Record Mode)\n"
            "    variable = time accrues at RPM / Standard Recording RPM (most common)\n"
            "    fixed    = 1:1 with clock whenever engine is running\n"
            "  Mode",
            type=click.Choice(['variable', 'fixed']),
            default='variable',
            show_choices=True
        )
        engine_time_cfg.mode = tach_mode

        if tach_mode == 'variable':
            click.echo(
                "  Use the avionics' Standard Recording RPM (G3X: Engine Time →\n"
                "  Standard Recording RPM). Not redline — using redline (e.g. 2700\n"
                "  instead of 2350) underestimated tach by about 15% on real logs."
            )
            engine_time_cfg.reference_rpm = click.prompt(
                "  Standard Recording RPM (1 tach hour per hour at this RPM)",
                default=engine_time_cfg.reference_rpm, type=int,
            )

        # --- Upload services ---
        click.echo("\n  Upload Services:")
        click.echo("  Configure where to send your flight data after each flight.\n")

        # CloudAhoy
        click.echo("  CloudAhoy (https://www.cloudahoy.com)")
        if click.confirm("    Enable CloudAhoy?", default=False):
            api_token = click.prompt("    API token")
            uploaders['cloudahoy'] = UploaderConfig(
                enabled=True,
                config={'enabled': True, 'api_token': api_token}
            )
            click.echo("    ✓ CloudAhoy configured")
        else:
            uploaders['cloudahoy'] = UploaderConfig(
                enabled=False, config={'enabled': False, 'api_token': ''}
            )

        click.echo("")

        # FlySto
        click.echo("  FlySto (https://www.flysto.net)")
        if click.confirm("    Enable FlySto?", default=False):
            click.echo("    Create an API token at https://www.flysto.net/account/api")
            api_token = _prompt_password("    API token: ")
            uploaders['flysto'] = UploaderConfig(
                enabled=True,
                config={'enabled': True, 'api_token': api_token}
            )
        else:
            uploaders['flysto'] = UploaderConfig(
                enabled=False,
                config={'enabled': False, 'api_token': ''}
            )

        click.echo("")

        # Savvy Aviation
        click.echo("  Savvy Aviation (https://www.savvyaviation.com)")
        click.echo("    Note: no public API — files are staged locally for manual upload.")
        if click.confirm("    Enable Savvy Aviation staging?", default=False):
            uploaders['savvy_aviation'] = UploaderConfig(
                enabled=True, config={'enabled': True}
            )
            click.echo("    ✓ Savvy Aviation staging enabled")
        else:
            uploaders['savvy_aviation'] = UploaderConfig(
                enabled=False, config={'enabled': False}
            )

        click.echo("")

        # Carryd
        click.echo("  Carryd (flight time tracking)")
        if click.confirm("    Enable Carryd?", default=False):
            carryd_key = click.prompt("    API key (eab_...)")
            carryd_logbooks = click.prompt(
                "    Engine logbook UUID(s), comma-separated (leave blank to skip engine times)",
                default=""
            )
            logbook_list = [u.strip() for u in carryd_logbooks.split(',') if u.strip()]
            uploaders['carryd'] = UploaderConfig(
                enabled=True,
                config={'enabled': True, 'api_key': carryd_key, 'engine_logbooks': logbook_list}
            )
            click.echo("    ✓ Carryd configured")
        else:
            uploaders['carryd'] = UploaderConfig(
                enabled=False, config={'enabled': False, 'api_key': '', 'engine_logbooks': []}
            )

        # --- First-run behavior ---
        click.echo("\n  First-Run Behavior:")
        click.echo(
            "\n  When you insert your SD card for the first time, avcardtool will\n"
            "  find all existing log files — potentially months of flight history.\n"
            "  Services like FlySto don't deduplicate, so uploading everything at\n"
            "  once could flood your account with old flights.\n"
        )
        mark_historical = click.confirm(
            "  Mark all existing files as already processed on first SD card use?\n"
            "  (Recommended: only flights recorded after this setup will be uploaded)",
            default=True
        )
    else:
        mark_historical = False

    # ------------------------------------------------------------------
    # Section 3: Self-update
    # ------------------------------------------------------------------
    click.echo("\n── Self-Update ───────────────────────────────────────────")

    auto_self_update = click.confirm(
        "Enable automatic weekly self-update?", default=True
    )

    # ------------------------------------------------------------------
    # Section 4: Navigation database auto-update
    # ------------------------------------------------------------------
    click.echo("\n── Navigation Database Auto-Update ───────────────────────")
    click.echo(
        "\nWhen an SD card is inserted, avcardtool can check for and install\n"
        "updated Garmin NavData, terrain, obstacle, and chart databases\n"
        "automatically using your flyGarmin account.\n"
    )

    auto_update_navdata = click.confirm(
        "Enable automatic navigation database updates?", default=True
    )

    navdata_garmin = {}

    if auto_update_navdata:
        click.echo(
            "\n  Log in to flyGarmin to authorize database downloads.\n"
            "  Your credentials are stored locally as a token — your\n"
            "  password is not saved.\n"
        )
        from avcardtool.navdata.garmin.auth import GarminAuth, GarminAuthError

        garmin_email = click.prompt("  flyGarmin email")
        garmin_password = _prompt_password("  flyGarmin password: ")

        auth = GarminAuth(token_dir=Path(data_dir))

        def _mfa_callback():
            click.echo("\n" + "!" * 40)
            click.echo("  Multi-Factor Authentication Required")
            click.echo("!" * 40)
            return click.prompt("  Verification code")

        try:
            click.echo(f"\n  Logging in as {garmin_email}...")
            success = auth.login(garmin_email, garmin_password, mfa_callback=_mfa_callback)
            if success:
                click.echo(f"  ✓ Logged in as {auth.tokens.display_name}. Token saved.")
                navdata_garmin['email'] = garmin_email
                navdata_garmin['databases'] = ['navdata', 'terrain', 'obstacles']
        except GarminAuthError as e:
            click.echo(f"  ✗ Login failed: {e}")
            click.echo(
                "  Navigation database auto-update will be disabled.\n"
                "  You can log in later with: avcardtool navdata login"
            )
            auto_update_navdata = False
        except Exception as e:
            click.echo(f"  ✗ Unexpected error: {e}")
            auto_update_navdata = False

    # ------------------------------------------------------------------
    # Section 5: Email notifications
    # ------------------------------------------------------------------
    click.echo("\n── Email Notifications ───────────────────────────────────")
    click.echo(
        "\navcardtool can email you after flight logs are processed (with the\n"
        "final Hobbs/Tach times and upload results) and when new database\n"
        "cycles are installed to a card — plus alerts when something needs\n"
        "attention, such as an expired flyGarmin login.\n"
    )

    notifications_enabled = click.confirm("Enable email notifications?", default=False)
    notification_backends = {}

    if notifications_enabled:
        click.echo(
            "\n  Any SMTP provider works. Gmail accounts with 2FA need an App\n"
            "  Password (https://myaccount.google.com/apppasswords) — your\n"
            "  normal password will be rejected.\n"
        )
        smtp_host = click.prompt("  SMTP server", default="smtp.gmail.com")
        smtp_port = click.prompt(
            "  SMTP port (587 = STARTTLS, 465 = implicit TLS)", default=587, type=int
        )
        smtp_username = click.prompt(
            "  SMTP username (usually your email address; empty for localhost relays)",
            default="", show_default=False
        )
        smtp_password = _prompt_password("  SMTP password: ") if smtp_username else ""
        from avcardtool.notifications.backends.email_smtp import normalize_password
        if normalize_password(smtp_password, smtp_host) != smtp_password:
            smtp_password = normalize_password(smtp_password, smtp_host)
            click.echo("  (Removed the spaces from the Gmail app password — they're not part of it.)")
        from_addr = click.prompt(
            "  From address",
            default=smtp_username if '@' in smtp_username else None
        )
        to_raw = click.prompt(
            "  Send notifications to (comma-separated)",
            default=smtp_username if '@' in smtp_username else None
        )
        to_addrs = [addr.strip() for addr in to_raw.split(',') if addr.strip()]

        notification_backends['email'] = {
            'enabled': True,
            'smtp_host': smtp_host,
            'smtp_port': smtp_port,
            # TLS always, except for localhost relays (mirrors the backend's
            # policy of refusing plaintext to any remote host)
            'use_tls': smtp_host not in ('localhost', '127.0.0.1', '::1'),
            'username': smtp_username,
            'password': smtp_password,
            'from_addr': from_addr,
            'to_addrs': to_addrs,
        }

        if click.confirm("\n  Send a test email now?", default=True):
            import socket as _socket
            from avcardtool.notifications.backends.email_smtp import EmailBackend
            from avcardtool.notifications.base import NotificationEvent, Severity

            backend = EmailBackend(notification_backends['email'])
            problems = backend.validate_config()
            if problems:
                for problem in problems:
                    click.echo(f"    ✗ {problem}")
            else:
                click.echo("    Sending...")
                result = backend.send(NotificationEvent(
                    event_type="test",
                    severity=Severity.INFO,
                    title="Test notification",
                    body=(
                        f"avcardtool setup on {_socket.gethostname()} can send email.\n"
                        "Notification delivery is working."
                    ),
                ))
                if result.success:
                    click.echo(f"    ✓ {result.message}")
                else:
                    click.echo(f"    ✗ {result.message}")
                    if not click.confirm(
                        "    Keep this email configuration anyway?", default=True
                    ):
                        notifications_enabled = False
                        notification_backends = {}

    # ------------------------------------------------------------------
    # Section 6: Save configuration
    # ------------------------------------------------------------------
    click.echo("\n── Saving Configuration ──────────────────────────────────")

    if config_path is None:
        if os.geteuid() == 0:
            default_path = Path("/etc/avcardtool/config.json")
        else:
            default_path = Path.home() / ".config" / "avcardtool" / "config.json"
        config_path = Path(
            click.prompt("Config file path", default=str(default_path))
        )

    if config_path.exists():
        if not click.confirm(
            f"\n  {config_path} already exists. Overwrite?",
            default=False
        ):
            click.echo("Aborted — no changes written.")
            sys.exit(0)

    # Build config
    cfg = Config.__new__(Config)
    cfg.config_path = config_path

    cfg.flight_data = FlightDataConfig()
    cfg.flight_data.enabled = auto_process_flights
    cfg.flight_data.engine_time = engine_time_cfg
    cfg.flight_data.airframe_time = airframe_time_cfg
    cfg.flight_data.uploaders = uploaders

    cfg.navdata = NavdataConfig()
    cfg.navdata.enabled = auto_update_navdata
    cfg.navdata.auto_download = auto_update_navdata
    cfg.navdata.garmin = navdata_garmin

    cfg.notifications = NotificationsConfig()
    cfg.notifications.enabled = notifications_enabled
    cfg.notifications.backends = notification_backends

    cfg.system = SystemConfig()
    cfg.system.data_dir = data_dir
    cfg.system.log_file = str(Path(data_dir) / "avcardtool.log")
    cfg.system.debug = enable_debug
    cfg.system.mark_historical_on_first_run = mark_historical
    cfg.system.auto_process_flights = auto_process_flights
    cfg.system.auto_update_navdata = auto_update_navdata
    cfg.system.auto_self_update = auto_self_update

    try:
        cfg.save(config_path)
        click.echo(f"\n  ✓ Configuration saved to {config_path}")
    except Exception as e:
        click.echo(f"\n  ✗ Failed to save configuration: {e}", err=True)
        sys.exit(1)

    click.echo("\n" + "=" * 60)
    click.echo("Setup complete!")
    click.echo("=" * 60)

    if auto_process_flights or auto_update_navdata or auto_self_update or notifications_enabled:
        click.echo("\nEnabled features:")
        if auto_process_flights:
            click.echo("  ✓ Flight log processing")
        if auto_update_navdata:
            click.echo("  ✓ Navigation database auto-update")
        if auto_self_update:
            click.echo("  ✓ Automatic weekly self-update")
        if notifications_enabled:
            click.echo("  ✓ Email notifications")
        if auto_process_flights or auto_update_navdata:
            click.echo("\nInsert your SD card to trigger both.")
        click.echo("Monitor progress:")
        click.echo('  journalctl -u "avcardtool-*" -f')
    else:
        click.echo(
            "\nNo automated features were enabled.\n"
            "Re-run 'avcardtool setup' or edit the config to change this."
        )
    click.echo("")


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
    type=click.Choice(['flight_data', 'navdata', 'notifications', 'system', 'all']),
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
# Notifications
# ============================================================================

@cli.command('notify-test')
@click.pass_context
def notify_test(ctx):
    """
    Send a test notification through all configured backends.

    Verifies notification delivery end-to-end (config → manager → SMTP)
    without waiting for an SD card insertion.
    """
    import socket

    from avcardtool.notifications import NotificationManager
    from avcardtool.notifications.base import NotificationEvent, Severity

    cfg = ctx.obj['config']
    manager = NotificationManager.from_config(cfg)

    if not manager.config.enabled:
        click.echo("Notifications are disabled (notifications.enabled = false).", err=True)
        click.echo("Enable them in your config file and re-run.", err=True)
        sys.exit(1)
    if not manager.backends:
        click.echo("No notification backends are enabled.", err=True)
        click.echo("Configure notifications.backends.email in your config file.", err=True)
        sys.exit(1)

    # Surface configuration problems before attempting delivery
    problems = []
    for backend in manager.backends:
        problems.extend(backend.validate_config())
    if problems:
        click.echo("Configuration problems:")
        for problem in problems:
            click.echo(f"  ✗ {problem}")
        sys.exit(1)

    event = NotificationEvent(
        event_type="test",
        severity=Severity.INFO,
        title="Test notification",
        body=(
            f"This is a test notification from avcardtool {__version__} "
            f"on {socket.gethostname()}.\n"
            "If you can read this, notification delivery is working."
        ),
    )

    click.echo("Sending test notification...")
    results = manager.notify(event, force=True)
    all_ok = bool(results)
    for result in results:
        mark = '✓' if result.success else '✗'
        click.echo(f"  {mark} {result.backend}: {result.message}")
        all_ok = all_ok and result.success
    sys.exit(0 if all_ok else 1)


# ============================================================================
# Self-Update Command
# ============================================================================

@cli.command('self-update')
@click.option(
    '--version',
    default=None,
    metavar='VERSION',
    help='Install a specific version (e.g. 1.2.0), even if it is not newer. '
         'Defaults to the latest release, installed only if newer than this one.'
)
@click.option(
    '--system-files-only',
    is_flag=True,
    hidden=True,
    help='Internal: skip the pip upgrade and only refresh udev/systemd files. '
         'Used so the files are updated by the newly installed code.'
)
@click.pass_context
def self_update(ctx, version: Optional[str], system_files_only: bool):
    """
    Update avcardtool to the latest release from GitHub, if it is newer.

    Checks the latest GitHub release first and does nothing when this
    install is already at (or ahead of) it. Otherwise re-runs with sudo if
    not already root, so that udev rules and systemd service files are
    updated alongside the Python package.

    Examples:
        avcardtool self-update
        avcardtool self-update --version 1.2.0
    """
    import re
    import subprocess

    # Development (editable) install: the developer manages the code via git,
    # so skip the release check and pip upgrade, but still refresh system
    # files (below, as root) so udev rules and systemd units stay current.
    dev_path = _editable_install_path()
    requested_version = version  # only set when the user passed --version

    if not dev_path and version is None:
        try:
            latest = _latest_release_version()
        except Exception as e:
            click.echo(f"Could not check for updates: {e}", err=True)
            sys.exit(1)
        if _version_tuple(latest) <= _version_tuple(__version__):
            click.echo(f"Already up to date (v{__version__}; latest release is v{latest}).")
            return
        click.echo(f"New release available: v{latest} (installed: v{__version__})")
        version = latest

    if os.geteuid() != 0:
        # Re-run with exactly 'self-update' (no extra arguments) so the sudoers
        # rule, which matches arguments exactly, allows it without a password.
        # The root run repeats the release check itself. An explicit --version
        # is passed through, but then sudo may ask for a password.
        args = ['sudo', sys.argv[0], 'self-update']
        if requested_version:
            args += ['--version', requested_version]
        os.execvp('sudo', args)
        # execvp replaces the current process; this line is unreachable

    if system_files_only:
        # Re-launched by the upgrade below so this process runs the new code.
        click.echo(f"Updating system files with v{__version__}...")
        _update_system_files(ctx, version)
        return

    click.echo(f"Current version: v{__version__}")

    if dev_path:
        click.echo(f"Development install ({dev_path}) — skipping pip upgrade.")
        click.echo("Updating system files...")
        _update_system_files(ctx, None)
        return

    package = f"git+https://github.com/elvinzhou/g3xuploader.git@v{version}"
    click.echo(f"Updating avcardtool to v{version}...")

    # Run pip. On Debian/Raspberry Pi OS with Python 3.11+ (PEP 668), pip
    # refuses to install into the system environment without the
    # --break-system-packages flag. Try without it first; if pip rejects
    # with the managed-environment error, retry with the flag.
    pip_base = [sys.executable, '-m', 'pip', 'install', '--upgrade', package]
    result = subprocess.run(pip_base, capture_output=True, text=True)
    if result.returncode != 0 and 'externally-managed' in (result.stdout + result.stderr).lower():
        result = subprocess.run(
            pip_base + ['--break-system-packages'],
            capture_output=True, text=True
        )

    if result.returncode != 0:
        click.echo(f"Update failed:\n{result.stderr.strip()}", err=True)
        sys.exit(1)

    installed_match = re.search(r'Successfully installed avcardtool-([\d.]+)', result.stdout)
    new_version = installed_match.group(1) if installed_match else version
    click.echo(f"Installed v{new_version}.")

    # pip replaced the package on disk, but this process still runs the old
    # code, so any change to how system files are updated (timers, paths, new
    # units) would only take effect on the next run. Re-launch the freshly
    # installed binary for that step instead.
    sys.stdout.flush()
    try:
        os.execv(sys.argv[0], [sys.argv[0], *sys.argv[1:],
                               '--system-files-only', '--version', new_version])
    except OSError as e:
        click.echo(f"  Could not re-launch the new version ({e}); "
                   f"updating system files with v{__version__}.", err=True)
        _update_system_files(ctx, new_version)


_RELEASES_LATEST_URL = "https://api.github.com/repos/elvinzhou/g3xuploader/releases/latest"


def _latest_release_version() -> str:
    """Version of the latest published GitHub release, e.g. '1.9.3'."""
    import re
    import requests

    resp = requests.get(
        _RELEASES_LATEST_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=30,
    )
    resp.raise_for_status()
    tag = resp.json().get("tag_name", "")
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", tag)
    if not m:
        raise RuntimeError(f"unexpected release tag {tag!r}")
    return m.group(1)


def _version_tuple(v: str) -> tuple:
    """'1.10.2' -> (1, 10, 2), so versions compare numerically, not as text."""
    return tuple(int(part) for part in v.split("."))


def _editable_install_path() -> Optional[str]:
    """Source directory of an editable (pip install -e) install, else None."""
    import subprocess

    pip_show = subprocess.run(
        [sys.executable, '-m', 'pip', 'show', 'avcardtool'],
        capture_output=True, text=True
    )
    for line in pip_show.stdout.splitlines():
        if line.lower().startswith('editable project location'):
            return line.split(':', 1)[1].strip()
    return None


def _update_system_files(ctx: click.Context, version: Optional[str]) -> None:
    """Download and install updated udev rules and systemd service files from GitHub."""
    import getpass
    import subprocess
    import pwd

    cfg = ctx.obj.get('config') if ctx.obj else None

    # Determine the real (non-root) user when invoked via sudo
    real_username = os.environ.get('SUDO_USER') or getpass.getuser()
    try:
        pw = pwd.getpwnam(real_username)
        real_home = Path(pw.pw_dir)
    except KeyError:
        real_home = Path.home()

    # Running as root (sudo or the self-update unit), Config() looked in root's
    # home, not the real user's. Root's defaults would then disable the
    # navdata/self-update timers and write the wrong paths into the unit files.
    # Use the real user's config when root's own search found nothing of its own.
    user_cfg_path = real_home / ".config" / "avcardtool" / "config.json"
    if (cfg is not None and user_cfg_path.exists()
            and real_home != Path.home()
            and (cfg.config_path is None
                 or cfg.config_path.is_relative_to(Path.home()))):
        try:
            cfg = Config(config_path=user_cfg_path)
        except Exception as e:
            click.echo(f"Warning: could not load {user_cfg_path}: {e}", err=True)

    data_dir = cfg.system.data_dir if cfg else str(real_home / ".local" / "share" / "avcardtool")
    config_dir = (
        str(cfg.config_path.parent)
        if cfg and cfg.config_path
        else str(real_home / ".config" / "avcardtool")
    )

    base_url = (
        f"https://raw.githubusercontent.com/elvinzhou/g3xuploader/v{version}"
        if version
        else "https://raw.githubusercontent.com/elvinzhou/g3xuploader/main"
    )

    system_files = [
        {
            "url": f"{base_url}/systemd/99-avcardtool-sdcard.rules",
            "dest": Path("/etc/udev/rules.d/99-avcardtool-sdcard.rules"),
            "substitute": False,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-processor@.service",
            "dest": Path("/lib/systemd/system/avcardtool-processor@.service"),
            "substitute": True,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-navdata@.service",
            "dest": Path("/lib/systemd/system/avcardtool-navdata@.service"),
            "substitute": True,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-navdata-check.service",
            "dest": Path("/lib/systemd/system/avcardtool-navdata-check.service"),
            "substitute": True,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-navdata-check.timer",
            "dest": Path("/lib/systemd/system/avcardtool-navdata-check.timer"),
            "substitute": False,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-self-update.service",
            "dest": Path("/lib/systemd/system/avcardtool-self-update.service"),
            "substitute": True,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-self-update.timer",
            "dest": Path("/lib/systemd/system/avcardtool-self-update.timer"),
            "substitute": False,
        },
        {
            "url": f"{base_url}/systemd/avcardtool-sudoers",
            "dest": Path("/etc/sudoers.d/avcardtool"),
            "substitute": True,
            "mode": 0o440,
            "validate": True,
        },
        {
            "url": f"{base_url}/systemd/99-avcardtool.rules",
            "dest": Path("/etc/polkit-1/rules.d/99-avcardtool.rules"),
            "substitute": True,
        },
    ]

    click.echo("\nUpdating system files...")
    import requests as _requests

    # The per-card navdata-watch sleep loop was replaced by the system-wide
    # navdata-check timer — clean it up on installs that predate the change.
    legacy_watch = Path("/lib/systemd/system/avcardtool-navdata-watch@.service")
    if legacy_watch.exists():
        try:
            subprocess.run(
                ["systemctl", "stop", "avcardtool-navdata-watch@*"], capture_output=True
            )
            legacy_watch.unlink()
            click.echo(f"  Removed legacy {legacy_watch.name}")
        except Exception as e:
            click.echo(f"  Warning: could not remove {legacy_watch.name}: {e}", err=True)

    any_updated = False
    for entry in system_files:
        # Skip sudoers entry if self-update is disabled
        if entry.get("validate") and not (cfg.system.auto_self_update if cfg else True):
            if entry["dest"].exists():
                entry["dest"].unlink()
            continue
        try:
            resp = _requests.get(entry["url"], timeout=30)
            resp.raise_for_status()
            content = resp.text

            if entry["substitute"]:
                content = (
                    content
                    .replace("AVCARDTOOL_USER", real_username)
                    .replace("AVCARDTOOL_DATA_DIR", data_dir)
                    .replace("AVCARDTOOL_CONFIG_DIR", config_dir)
                )

            if entry.get("validate"):
                tmp = entry["dest"].with_suffix(".tmp")
                tmp.write_text(content)
                tmp.chmod(entry.get("mode", 0o644))
                result = subprocess.run(["visudo", "-c", "-f", str(tmp)], capture_output=True)
                if result.returncode != 0:
                    tmp.unlink()
                    click.echo(f"  Warning: sudoers validation failed — skipping {entry['dest'].name}", err=True)
                    continue
                tmp.rename(entry["dest"])
            else:
                entry["dest"].write_text(content)
                entry["dest"].chmod(entry.get("mode", 0o644))

            click.echo(f"  Updated {entry['dest']}")
            any_updated = True
        except Exception as e:
            click.echo(f"  Warning: could not update {entry['dest'].name}: {e}", err=True)

    if any_updated:
        try:
            subprocess.run(["udevadm", "control", "--reload-rules"], check=True, capture_output=True)
            subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
            click.echo("  Reloaded udev rules and systemd daemon.")
        except Exception as e:
            click.echo(f"  Warning: could not reload system: {e}", err=True)

        # Restart any currently-running avcardtool service instances so they
        # pick up the new binary and unit files without requiring a manual step.
        try:
            list_result = subprocess.run(
                ["systemctl", "list-units", "--state=running", "--no-legend",
                 "--no-pager", "avcardtool-*"],
                capture_output=True, text=True,
            )
            running_units = [
                line.split()[0]
                for line in list_result.stdout.splitlines()
                if line.strip() and not line.strip().startswith("●")
            ]
            # Skip the self-update service itself to avoid cutting off our own process
            running_units = [u for u in running_units if "self-update" not in u]
            if running_units:
                subprocess.run(["systemctl", "restart"] + running_units, capture_output=True)
                click.echo(f"  Restarted: {', '.join(running_units)}")
            else:
                click.echo("  No running avcardtool services to restart.")
        except Exception as e:
            click.echo(f"  Warning: could not restart services: {e}", err=True)

    # Enable or disable the self-update timer based on config
    auto_self_update = cfg.system.auto_self_update if cfg else True
    timer = "avcardtool-self-update.timer"
    try:
        if auto_self_update:
            subprocess.run(["systemctl", "enable", "--now", timer], check=True, capture_output=True)
            click.echo(f"  Enabled {timer}.")
        else:
            subprocess.run(["systemctl", "disable", "--now", timer], capture_output=True)
            click.echo(f"  Disabled {timer} (auto_self_update=false in config).")
    except Exception as e:
        click.echo(f"  Warning: could not update {timer}: {e}", err=True)

    # Enable or disable the daily navdata re-check timer based on config
    auto_update_navdata = cfg.system.auto_update_navdata if cfg else False
    check_timer = "avcardtool-navdata-check.timer"
    try:
        if auto_update_navdata:
            subprocess.run(["systemctl", "enable", "--now", check_timer], check=True, capture_output=True)
            click.echo(f"  Enabled {check_timer}.")
        else:
            subprocess.run(["systemctl", "disable", "--now", check_timer], capture_output=True)
            click.echo(f"  Disabled {check_timer} (auto_update_navdata=false in config).")
    except Exception as e:
        click.echo(f"  Warning: could not update {check_timer}: {e}", err=True)


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
