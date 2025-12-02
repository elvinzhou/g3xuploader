#!/usr/bin/env python3
"""
G3X Database Updater - Main CLI

Command-line interface for the G3X Database Updater.

This tool automates the process of:
1. Authenticating with Garmin's flyGarmin portal
2. Downloading aviation databases (TAW/AWP files)
3. Extracting and writing them to SD cards

Usage:
    g3x-db-updater login                     # Login to Garmin
    g3x-db-updater list-devices              # List registered devices
    g3x-db-updater list-databases            # List available databases
    g3x-db-updater download [database_ids]   # Download databases
    g3x-db-updater write [taw_files]         # Write to SD card
    g3x-db-updater auto-update               # Full automatic update
    g3x-db-updater extract <taw_file>        # Extract TAW file contents
"""

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path
from typing import Optional, List

from .garmin_auth import GarminAuth, FlyGarminAPI, GarminAuthError, GarminAPIError
from .taw_parser import TAWExtractor, TAWParser, print_taw_info, TAWParseError
from .sdcard_writer import (
    SDCardDetector, G3XDatabaseWriter, AutoDatabaseUpdater,
    SDCardError, SDCardNotFoundError
)


# Default directories
DEFAULT_DOWNLOAD_DIR = Path.home() / ".g3x_db_updater" / "downloads"
DEFAULT_CONFIG_DIR = Path.home() / ".g3x_db_updater"


def setup_logging(verbose: bool = False, log_file: Optional[Path] = None):
    """Configure logging"""
    level = logging.DEBUG if verbose else logging.INFO
    
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def cmd_login(args):
    """Handle login command"""
    auth = GarminAuth()
    
    if auth.is_authenticated():
        print(f"Already logged in as {auth.tokens.display_name}")
        if not args.force:
            print("Use --force to re-login")
            return 0
    
    email = args.email or input("Email: ")
    password = args.password or getpass.getpass("Password: ")
    
    try:
        auth.login(email, password)
        print(f"Successfully logged in as {email}")
        return 0
    except GarminAuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1


def cmd_logout(args):
    """Handle logout command"""
    auth = GarminAuth()
    auth.logout()
    print("Logged out successfully")
    return 0


def cmd_list_devices(args):
    """Handle list-devices command"""
    auth = GarminAuth()
    
    if not auth.ensure_authenticated():
        print("Not logged in. Run 'g3x-db-updater login' first.", file=sys.stderr)
        return 1
    
    try:
        api = FlyGarminAPI(auth)
        devices = api.get_devices()
        
        if not devices:
            print("No devices found")
            return 0
        
        if args.json:
            print(json.dumps([d.to_dict() for d in devices], indent=2))
        else:
            print(f"Found {len(devices)} device(s):\n")
            for i, device in enumerate(devices):
                print(f"  [{i}] {device.device_name}")
                print(f"      Type: {device.device_type}")
                print(f"      Serial: {device.serial_number}")
                print(f"      System ID: {device.system_id}")
                if device.aircraft_tail:
                    print(f"      Aircraft: {device.aircraft_tail}")
                print()
        
        return 0
        
    except GarminAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1


def cmd_list_databases(args):
    """Handle list-databases command"""
    auth = GarminAuth()
    
    if not auth.ensure_authenticated():
        print("Not logged in. Run 'g3x-db-updater login' first.", file=sys.stderr)
        return 1
    
    try:
        api = FlyGarminAPI(auth)
        devices = api.get_devices()
        
        if not devices:
            print("No devices found", file=sys.stderr)
            return 1
        
        # Use specified device or first device
        device_idx = args.device if args.device is not None else 0
        if device_idx >= len(devices):
            print(f"Invalid device index: {device_idx}", file=sys.stderr)
            return 1
        
        device = devices[device_idx]
        print(f"Databases for: {device.device_name}\n")
        
        databases = api.get_available_databases(device)
        
        if not databases:
            print("No databases available")
            return 0
        
        if args.json:
            print(json.dumps([d.to_dict() for d in databases], indent=2))
        else:
            for i, db in enumerate(databases):
                status = "Current" if args.current_only else ""
                print(f"  [{i}] {db.name}")
                print(f"      Type: {db.db_type}")
                print(f"      Coverage: {db.coverage}")
                print(f"      Version: {db.version} (Cycle {db.cycle})")
                print(f"      Valid: {db.start_date} - {db.end_date}")
                print(f"      File: {db.file_name} ({db.file_size / 1024 / 1024:.1f} MB)")
                print()
        
        return 0
        
    except GarminAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1


def cmd_download(args):
    """Handle download command"""
    auth = GarminAuth()
    
    if not auth.ensure_authenticated():
        print("Not logged in. Run 'g3x-db-updater login' first.", file=sys.stderr)
        return 1
    
    try:
        api = FlyGarminAPI(auth)
        devices = api.get_devices()
        
        if not devices:
            print("No devices found", file=sys.stderr)
            return 1
        
        device = devices[args.device if args.device is not None else 0]
        databases = api.get_available_databases(device)
        
        # Parse database IDs to download
        if args.database_ids:
            if args.database_ids == 'all':
                to_download = databases
            else:
                indices = [int(x.strip()) for x in args.database_ids.split(',')]
                to_download = [databases[i] for i in indices if i < len(databases)]
        else:
            # Default: download all current databases
            to_download = databases
        
        if not to_download:
            print("No databases to download")
            return 0
        
        output_dir = Path(args.output) if args.output else DEFAULT_DOWNLOAD_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Downloading {len(to_download)} database(s) to {output_dir}\n")
        
        downloaded = []
        for db in to_download:
            print(f"Downloading: {db.name}...")
            
            def progress(current, total):
                pct = (current / total * 100) if total else 0
                print(f"\r  Progress: {pct:.1f}%", end='', flush=True)
            
            try:
                path = api.download_database(db, output_dir, progress)
                print(f"\n  Saved: {path}")
                downloaded.append(path)
            except GarminAPIError as e:
                print(f"\n  Error: {e}")
        
        print(f"\nDownloaded {len(downloaded)} database(s)")
        return 0
        
    except GarminAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1


def cmd_extract(args):
    """Handle extract command"""
    taw_file = Path(args.taw_file)
    
    if not taw_file.exists():
        print(f"File not found: {taw_file}", file=sys.stderr)
        return 1
    
    if args.list_only:
        try:
            print_taw_info(taw_file)
            return 0
        except TAWParseError as e:
            print(f"Parse error: {e}", file=sys.stderr)
            return 1
    
    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        extractor = TAWExtractor()
        extracted = extractor.extract_to_directory(
            taw_file, output_dir,
            preserve_paths=not args.flat,
            overwrite=args.overwrite
        )
        
        print(f"Extracted {len(extracted)} file(s) to {output_dir}")
        for f in extracted:
            print(f"  {f}")
        
        return 0
        
    except TAWParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1


def cmd_scan_sdcards(args):
    """Handle scan-sdcards command"""
    detector = SDCardDetector()
    cards = detector.scan_for_cards()
    
    if not cards:
        print("No SD cards found")
        return 0
    
    if args.json:
        card_data = []
        for card in cards:
            suitable, reason = card.is_suitable()
            card_data.append({
                'device': card.device_path,
                'mount': card.mount_point,
                'label': card.label,
                'filesystem': card.filesystem,
                'size_gb': card.size_gb,
                'free_gb': card.free_gb,
                'volume_id': card.volume_id,
                'is_garmin': card.is_garmin,
                'suitable': suitable,
                'reason': reason,
            })
        print(json.dumps(card_data, indent=2))
    else:
        print(f"Found {len(cards)} SD card(s):\n")
        for card in cards:
            suitable, reason = card.is_suitable()
            status = "✓" if suitable else "✗"
            
            print(f"  {status} {card.device_path}")
            print(f"      Size: {card.size_gb:.1f} GB ({card.free_gb:.1f} GB free)")
            print(f"      Mount: {card.mount_point or 'Not mounted'}")
            print(f"      Filesystem: {card.filesystem}")
            print(f"      Volume ID: {card.volume_id or 'Unknown'}")
            print(f"      Garmin card: {'Yes' if card.is_garmin else 'No'}")
            if not suitable:
                print(f"      Issue: {reason}")
            print()
    
    return 0


def cmd_write(args):
    """Handle write command"""
    # Get TAW files
    taw_files = [Path(f) for f in args.taw_files]
    for f in taw_files:
        if not f.exists():
            print(f"File not found: {f}", file=sys.stderr)
            return 1
    
    # Find SD card
    detector = SDCardDetector()
    
    if args.device:
        # Use specified device
        cards = [c for c in detector.scan_for_cards() 
                 if c.device_path == args.device or c.mount_point == args.device]
        if not cards:
            print(f"Device not found: {args.device}", file=sys.stderr)
            return 1
        card = cards[0]
    else:
        # Auto-detect
        cards = detector.scan_for_cards()
        suitable = [c for c in cards if c.is_suitable()[0]]
        if not suitable:
            print("No suitable SD card found", file=sys.stderr)
            return 1
        card = suitable[0]
    
    if not card.mount_point:
        print(f"Mounting {card.device_path}...")
        try:
            card.mount_point = detector.mount_card(card.device_path)
        except SDCardError as e:
            print(f"Mount failed: {e}", file=sys.stderr)
            return 1
    
    print(f"Writing to: {card.mount_point}")
    print(f"Files: {len(taw_files)}")
    
    if not args.yes:
        confirm = input("\nProceed? [y/N] ")
        if confirm.lower() != 'y':
            print("Aborted")
            return 0
    
    try:
        updater = AutoDatabaseUpdater(DEFAULT_DOWNLOAD_DIR, detector)
        
        def progress(op, current, total):
            pct = (current / total * 100) if total else 0
            print(f"\r{op}: {pct:.0f}%", end='', flush=True)
        
        results = updater.update_sd_card(card, taw_files, progress)
        print()  # Newline after progress
        
        # Summary
        total_written = sum(r.bytes_written for r in results)
        total_errors = sum(len(r.errors) for r in results)
        
        print(f"\nWrite complete:")
        print(f"  Files written: {sum(len(r.files_written) for r in results)}")
        print(f"  Bytes written: {total_written / 1024 / 1024:.1f} MB")
        print(f"  Errors: {total_errors}")
        
        return 0 if total_errors == 0 else 1
        
    except SDCardError as e:
        print(f"Write error: {e}", file=sys.stderr)
        return 1


def cmd_auto_update(args):
    """Handle auto-update command - full automated workflow"""
    auth = GarminAuth()
    
    # Step 1: Ensure logged in
    if not auth.ensure_authenticated():
        print("Not logged in. Run 'g3x-db-updater login' first.", file=sys.stderr)
        return 1
    
    print("=== G3X Automatic Database Update ===\n")
    
    try:
        # Step 2: Get devices and databases
        api = FlyGarminAPI(auth)
        devices = api.get_devices()
        
        if not devices:
            print("No registered devices found", file=sys.stderr)
            return 1
        
        # Use first G3X device
        device = devices[0]
        print(f"Device: {device.device_name}")
        
        databases = api.get_available_databases(device)
        if not databases:
            print("No databases available")
            return 0
        
        print(f"Available databases: {len(databases)}")
        
        # Step 3: Find SD card
        detector = SDCardDetector()
        cards = detector.scan_for_cards()
        suitable = [c for c in cards if c.is_suitable()[0]]
        
        if not suitable:
            print("\nNo suitable SD card found. Please insert an SD card.")
            return 1
        
        card = suitable[0]
        print(f"\nSD Card: {card.device_path} ({card.size_gb:.1f} GB)")
        
        if not card.mount_point:
            print(f"Mounting {card.device_path}...")
            card.mount_point = detector.mount_card(card.device_path)
        
        # Step 4: Download databases
        output_dir = DEFAULT_DOWNLOAD_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\nDownloading databases to {output_dir}...")
        
        taw_files = []
        for db in databases:
            print(f"  Downloading: {db.name}...")
            path = api.download_database(db, output_dir)
            taw_files.append(path)
        
        # Step 5: Write to SD card
        print(f"\nWriting to SD card...")
        
        updater = AutoDatabaseUpdater(output_dir, detector)
        results = updater.update_sd_card(card, taw_files)
        
        # Step 6: Summary
        success = all(r.success for r in results)
        total_bytes = sum(r.bytes_written for r in results)
        
        print(f"\n{'='*40}")
        print(f"Update {'COMPLETE' if success else 'FAILED'}")
        print(f"  Databases written: {len([r for r in results if r.success])}/{len(results)}")
        print(f"  Total data: {total_bytes / 1024 / 1024:.1f} MB")
        
        if not success:
            for r in results:
                for err in r.errors:
                    print(f"  Error: {err}")
        
        return 0 if success else 1
        
    except (GarminAuthError, GarminAPIError, SDCardError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='g3x-db-updater',
        description='G3X Touch Aviation Database Updater'
    )
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--log-file', type=Path,
                       help='Write logs to file')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Login command
    login_parser = subparsers.add_parser('login', help='Login to Garmin')
    login_parser.add_argument('-e', '--email', help='Garmin account email')
    login_parser.add_argument('-p', '--password', help='Garmin account password')
    login_parser.add_argument('-f', '--force', action='store_true',
                             help='Force re-login even if already logged in')
    
    # Logout command
    subparsers.add_parser('logout', help='Logout from Garmin')
    
    # List devices command
    devices_parser = subparsers.add_parser('list-devices', 
                                           help='List registered devices')
    devices_parser.add_argument('--json', action='store_true',
                               help='Output as JSON')
    
    # List databases command
    db_parser = subparsers.add_parser('list-databases',
                                      help='List available databases')
    db_parser.add_argument('-d', '--device', type=int, default=None,
                          help='Device index (default: 0)')
    db_parser.add_argument('--current-only', action='store_true',
                          help='Show only current databases')
    db_parser.add_argument('--json', action='store_true',
                          help='Output as JSON')
    
    # Download command
    dl_parser = subparsers.add_parser('download', help='Download databases')
    dl_parser.add_argument('database_ids', nargs='?', default=None,
                          help='Database IDs (comma-separated, or "all")')
    dl_parser.add_argument('-d', '--device', type=int, default=None,
                          help='Device index (default: 0)')
    dl_parser.add_argument('-o', '--output', help='Output directory')
    
    # Extract command
    ext_parser = subparsers.add_parser('extract', help='Extract TAW file')
    ext_parser.add_argument('taw_file', help='TAW/AWP file to extract')
    ext_parser.add_argument('-o', '--output', help='Output directory')
    ext_parser.add_argument('-l', '--list-only', action='store_true',
                           help='List contents without extracting')
    ext_parser.add_argument('--flat', action='store_true',
                           help='Extract files without subdirectories')
    ext_parser.add_argument('--overwrite', action='store_true',
                           help='Overwrite existing files')
    
    # Scan SD cards command
    scan_parser = subparsers.add_parser('scan-sdcards',
                                        help='Scan for SD cards')
    scan_parser.add_argument('--json', action='store_true',
                            help='Output as JSON')
    
    # Write command
    write_parser = subparsers.add_parser('write', help='Write to SD card')
    write_parser.add_argument('taw_files', nargs='+', help='TAW files to write')
    write_parser.add_argument('-d', '--device', help='Device path or mount point')
    write_parser.add_argument('-y', '--yes', action='store_true',
                             help='Skip confirmation')
    
    # Auto-update command
    auto_parser = subparsers.add_parser('auto-update',
                                        help='Full automatic update')
    
    args = parser.parse_args()
    
    # Set up logging
    setup_logging(args.verbose, args.log_file)
    
    # Dispatch to command handler
    commands = {
        'login': cmd_login,
        'logout': cmd_logout,
        'list-devices': cmd_list_devices,
        'list-databases': cmd_list_databases,
        'download': cmd_download,
        'extract': cmd_extract,
        'scan-sdcards': cmd_scan_sdcards,
        'write': cmd_write,
        'auto-update': cmd_auto_update,
    }
    
    if args.command in commands:
        return commands[args.command](args)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())
