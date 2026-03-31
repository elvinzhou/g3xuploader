"""
Processed files database for deduplication.

Tracks which files have been processed to avoid duplicate processing.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ProcessedFilesDatabase:
    """
    Track which files have been processed to avoid duplicates.

    Files are identified by their SHA256 hash, ensuring that the same file
    is never processed twice even if copied or renamed.
    """

    def __init__(self, db_path: Path):
        """
        Initialize the processed files database.

        Args:
            db_path: Path to JSON database file
        """
        self.db_path = Path(db_path)
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Ensure the database file and directory exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.db_path.exists():
            self._save({'processed': {}, 'version': 1})

    def _load(self) -> Dict[str, Any]:
        """Load database from disk."""
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Could not load processed files database: {e}")
            return {'processed': {}, 'version': 1}

    def _save(self, data: Dict[str, Any]):
        """Save database to disk."""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save processed files database: {e}")

    def is_processed(self, file_hash: str) -> bool:
        """
        Check if a file has already been processed by its hash.

        Args:
            file_hash: SHA256 hash of the file

        Returns:
            True if file has been processed
        """
        data = self._load()
        return file_hash in data.get('processed', {})

    def is_duplicate_flight(self, fingerprint: str) -> bool:
        """
        Check whether a flight with this fingerprint has already been uploaded.

        Used to deduplicate flights recorded by multiple G3X display units
        on the same aircraft. Each unit produces a different file (different
        name, slightly different data), but they share a fingerprint derived
        from aircraft_ident, system_id, and the UTC start minute.

        Args:
            fingerprint: Value returned by FlightData.flight_fingerprint()

        Returns:
            True if a flight with this fingerprint was already uploaded
        """
        data = self._load()
        for record in data.get('processed', {}).values():
            if record.get('flight_fingerprint') == fingerprint:
                # Match uploaded flights AND historical skips — both mean
                # this physical flight should not be uploaded again.
                if record.get('is_flight') or record.get('historical'):
                    return True
        return False

    def get_record(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get the processing record for a file.

        Args:
            file_hash: SHA256 hash of the file

        Returns:
            Processing record dict, or None if not found
        """
        data = self._load()
        return data.get('processed', {}).get(file_hash)

    def mark_processed(
        self,
        file_hash: str,
        file_path: Path,
        aircraft_ident: str,
        is_flight: bool,
        upload_results: Optional[Dict[str, Any]] = None,
        flight_fingerprint: Optional[str] = None
    ):
        """
        Mark a file as processed.

        Args:
            file_hash: SHA256 hash of the file
            file_path: Path to the file
            aircraft_ident: Aircraft identifier
            is_flight: Whether this was detected as a flight
            upload_results: Optional dict of upload results
            flight_fingerprint: Optional fingerprint from FlightData.flight_fingerprint()
        """
        data = self._load()

        if 'processed' not in data:
            data['processed'] = {}

        record = {
            'filename': file_path.name,
            'file_path': str(file_path),
            'aircraft': aircraft_ident,
            'is_flight': is_flight,
            'processed_at': datetime.now().isoformat(),
            'uploads': upload_results or {}
        }
        if flight_fingerprint:
            record['flight_fingerprint'] = flight_fingerprint

        data['processed'][file_hash] = record

        self._save(data)
        logger.info(f"Marked file as processed: {file_path.name} ({file_hash[:8]}...)")

    def mark_historical(self, file_hash: str, file_path: Path, flight_fingerprint: Optional[str] = None):
        """
        Mark a file as historical — it existed before the first run and
        was intentionally skipped rather than uploaded.

        Storing the flight_fingerprint is important for multi-display setups:
        if card A is inserted first and its files are marked historical, cards B
        and C (which have different hashes but the same fingerprint) will still
        be caught as duplicates when they are inserted later.

        Args:
            file_hash: SHA256 hash of the file
            file_path: Path to the file
            flight_fingerprint: Optional fingerprint from FlightData.flight_fingerprint()
        """
        data = self._load()

        if 'processed' not in data:
            data['processed'] = {}

        record = {
            'filename': Path(file_path).name,
            'file_path': str(file_path),
            'aircraft': None,
            'is_flight': False,
            'historical': True,
            'processed_at': datetime.now().isoformat(),
            'uploads': {}
        }
        if flight_fingerprint:
            record['flight_fingerprint'] = flight_fingerprint

        data['processed'][file_hash] = record
        self._save(data)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about processed files.

        Returns:
            Dict with statistics
        """
        data = self._load()
        processed = data.get('processed', {})

        total = len(processed)
        historical = sum(1 for record in processed.values() if record.get('historical', False))
        flights = sum(1 for record in processed.values() if record.get('is_flight', False))
        non_flights = total - flights - historical

        # Count uploads by service
        upload_counts = {}
        for record in processed.values():
            for service, result in record.get('uploads', {}).items():
                if service not in upload_counts:
                    upload_counts[service] = {'success': 0, 'failed': 0}
                if result.get('success', False):
                    upload_counts[service]['success'] += 1
                else:
                    upload_counts[service]['failed'] += 1

        return {
            'total_processed': total,
            'flights': flights,
            'non_flights': non_flights,
            'historical': historical,
            'uploads_by_service': upload_counts
        }

    def clear(self):
        """Clear all processed file records."""
        self._save({'processed': {}, 'version': 1})
        logger.info("Cleared processed files database")
