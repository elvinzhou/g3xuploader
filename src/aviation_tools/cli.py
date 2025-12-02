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

from aviation_tools import __version__
from aviation_tools.core import Config, setup_logging

logger = logging.getLogger(__name__)


# ============================================================================
# Main CLI Group
# ============================================================================

@click.group()
@click.version_option(version=__version__, prog_name="aviation-tools")
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
    log_level = 'DEBUG' if verbose else ctx.obj['config'].system.log_level
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
    from aviation_tools.flight_data import PROCESSORS, FlightDataAnalyzer

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


@flight.command('list-processors')
@click.pass_context
def flight_list_processors(ctx):
    """List available flight data processors."""
    from aviation_tools.flight_data import PROCESSORS

    click.echo("Available Flight Data Processors:\n")

    for ProcessorClass in PROCESSORS:
        proc = ProcessorClass()
        extensions = ', '.join(proc.get_supported_extensions())
        click.echo(f"  • {proc.get_name()}")
        click.echo(f"    Extensions: {extensions}")

    click.echo("\nMore processors coming soon (Dynon, Aspen, Avidyne, etc.)")


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
@click.option(
    '--password', '-p',
    prompt=True,
    hide_input=True,
    help='Garmin account password'
)
@click.option('--force', is_flag=True, help='Force re-login')
@click.pass_context
def navdata_login(ctx, email: str, password: str, force: bool):
    """Login to Garmin flyGarmin portal."""
    click.echo("Garmin authentication functionality coming soon...")
    click.echo(f"Would authenticate: {email}")


@navdata.command('list-databases')
@click.option('--device', '-d', type=int, help='Device index')
@click.pass_context
def navdata_list_databases(ctx, device: Optional[int]):
    """List available databases for download."""
    click.echo("Available databases will be listed here...")


@navdata.command('download')
@click.argument('selection', required=False, default='all')
@click.option('--device', '-d', type=int, help='Device index')
@click.option(
    '--output', '-o',
    type=click.Path(path_type=Path),
    help='Output directory'
)
@click.pass_context
def navdata_download(ctx, selection: str, device: Optional[int], output: Optional[Path]):
    """
    Download navigation databases.

    SELECTION can be 'all', or comma-separated indices (e.g., '0,1,2').
    """
    click.echo(f"Would download: {selection}")
    if output:
        click.echo(f"To directory: {output}")


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

    Converts old g3x_processor config to aviation-tools format.
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
# Automatic Processing Command
# ============================================================================

@cli.command('auto-process')
@click.argument(
    'device',
    type=click.Path(path_type=Path),
    required=False
)
@click.pass_context
def auto_process(ctx, device: Optional[Path]):
    """
    Automatically process both flight data and navigation databases.

    This is the main command triggered by systemd when an SD card is inserted.
    It will:
      1. Process any flight logs on the card
      2. Check for and install database updates
      3. Safely unmount the card

    If DEVICE is not provided, attempts to auto-detect SD card.
    """
    click.echo("=" * 60)
    click.echo("Aviation Tools - Automatic SD Card Processing")
    click.echo("=" * 60)

    cfg = ctx.obj['config']

    # Process flight data if enabled
    if cfg.flight_data.enabled:
        click.echo("\n[1/2] Processing flight data...")
        click.echo("Flight data processing functionality coming soon...")

    # Update navigation databases if enabled
    if cfg.navdata.enabled:
        click.echo("\n[2/2] Checking for navigation database updates...")
        click.echo("Navigation database update functionality coming soon...")

    click.echo("\n✓ Processing complete!")


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
