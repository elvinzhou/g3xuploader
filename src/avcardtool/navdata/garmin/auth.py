#!/usr/bin/env python3
"""
Garmin Aviation Authentication Module

Handles authentication with Garmin's flyGarmin portal for downloading
aviation databases. This implementation is inspired by the jdmtool
garmin_login branch approach.

Authentication Flow:
1. Initial login via Garmin SSO (sso.garmin.com)
2. OAuth token exchange for flyGarmin portal
3. Token refresh for long-running sessions
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

# Garmin SSO endpoints
GARMIN_SSO_BASE = "https://sso.garmin.com/sso"
GARMIN_SSO_SIGNIN = f"{GARMIN_SSO_BASE}/signin"
GARMIN_SSO_EMBED = f"{GARMIN_SSO_BASE}/embed"

# flyGarmin endpoints
FLYGARMIN_BASE = "https://fly.garmin.com"
FLYGARMIN_API = f"{FLYGARMIN_BASE}/fly-garmin"

# Token storage location
DEFAULT_TOKEN_DIR = Path.home() / ".g3x_db_updater"
TOKEN_FILE = "garmin_tokens.json"


@dataclass
class GarminTokens:
    """Container for Garmin authentication tokens"""
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0
    sso_guid: str = ""
    display_name: str = ""
    
    def is_expired(self) -> bool:
        """Check if the access token is expired (with 60s buffer)"""
        return time.time() >= (self.expires_at - 60)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "sso_guid": self.sso_guid,
            "display_name": self.display_name,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GarminTokens':
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=data.get("expires_at", 0),
            sso_guid=data.get("sso_guid", ""),
            display_name=data.get("display_name", ""),
        )


@dataclass 
class GarminDevice:
    """Represents a registered Garmin device"""
    device_id: str
    device_name: str
    system_id: str
    serial_number: str
    device_type: str
    aircraft_tail: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "system_id": self.system_id,
            "serial_number": self.serial_number,
            "device_type": self.device_type,
            "aircraft_tail": self.aircraft_tail,
        }


@dataclass
class DatabaseInfo:
    """Information about an available database"""
    db_id: str
    name: str
    coverage: str
    version: str
    cycle: str
    start_date: str
    end_date: str
    file_name: str
    file_size: int
    download_url: str
    db_type: str  # navdata, terrain, obstacles, safetaxi, chartview, flitecharts
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_id": self.db_id,
            "name": self.name,
            "coverage": self.coverage,
            "version": self.version,
            "cycle": self.cycle,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "download_url": self.download_url,
            "db_type": self.db_type,
        }


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails"""
    pass


class GarminAPIError(Exception):
    """Raised when a Garmin API call fails"""
    pass


class GarminAuth:
    """
    Handles authentication with Garmin's flyGarmin portal.
    
    This class manages the OAuth flow with Garmin SSO and provides
    authenticated access to the flyGarmin aviation database API.
    """
    
    def __init__(self, token_dir: Optional[Path] = None):
        self.token_dir = token_dir or DEFAULT_TOKEN_DIR
        self.token_file = self.token_dir / TOKEN_FILE
        self.tokens = GarminTokens()
        self.session = requests.Session()
        
        # Set up session headers
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        
        # Try to load existing tokens
        self._load_tokens()
    
    def _ensure_token_dir(self):
        """Ensure the token directory exists with secure permissions"""
        self.token_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.token_dir, 0o700)
    
    def _load_tokens(self) -> bool:
        """Load tokens from disk if available"""
        try:
            if self.token_file.exists():
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.tokens = GarminTokens.from_dict(data)
                    logger.info(f"Loaded tokens for {self.tokens.display_name}")
                    return True
        except Exception as e:
            logger.warning(f"Could not load tokens: {e}")
        return False
    
    def _save_tokens(self):
        """Save tokens to disk with secure permissions"""
        try:
            self._ensure_token_dir()
            with open(self.token_file, 'w') as f:
                json.dump(self.tokens.to_dict(), f, indent=2)
            os.chmod(self.token_file, 0o600)
            logger.info("Tokens saved successfully")
        except Exception as e:
            logger.warning(f"Could not save tokens: {e}")
    
    def _get_csrf_token(self, html: str) -> Optional[str]:
        """Extract CSRF token from HTML response"""
        # Look for _csrf token in the HTML
        patterns = [
            r'name="_csrf"\s+value="([^"]+)"',
            r'name="csrf_token"\s+value="([^"]+)"',
            r'"_csrf":\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None
    
    def _get_ticket(self, html: str) -> Optional[str]:
        """Extract service ticket from HTML response"""
        patterns = [
            r'ticket=([A-Za-z0-9\-_]+)',
            r'"ticket":\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None
    
    def login(self, email: str, password: str, mfa_callback: Optional[callable] = None) -> bool:
        """
        Login to Garmin SSO and obtain tokens for flyGarmin.
        
        Args:
            email: Garmin account email
            password: Garmin account password
            mfa_callback: Optional function to call for MFA code input
            
        Returns:
            True if login successful
        """
        logger.info(f"Logging in as {email}")
        
        try:
            # ... (Step 1 remains same)
            signin_params = {
                "webhost": FLYGARMIN_BASE,
                "service": FLYGARMIN_BASE,
                "source": GARMIN_SSO_SIGNIN,
                "redirectAfterAccountLoginUrl": FLYGARMIN_BASE,
                "redirectAfterAccountCreationUrl": FLYGARMIN_BASE,
                "gauthHost": GARMIN_SSO_BASE,
                "locale": "en_US",
                "id": "gauth-widget",
                "cssUrl": "https://static.garmincdn.com/com.garmin.connect/ui/css/gauth-custom-v1.2-min.css",
                "clientId": "FlyGarmin",
                "rememberMeShown": "true",
                "rememberMeChecked": "false",
                "createAccountShown": "true",
                "openCreateAccount": "false",
                "displayNameShown": "false",
                "consumeServiceTicket": "false",
                "initialFocus": "true",
                "embedWidget": "false",
                "generateExtraServiceTicket": "true",
                "generateTwoExtraServiceTickets": "true",
                "generateNoServiceTicket": "false",
                "globalOptInShown": "true",
                "globalOptInChecked": "false",
                "mobile": "false",
                "connectLegalTerms": "true",
            }
            
            response = self.session.get(
                GARMIN_SSO_SIGNIN,
                params=signin_params,
                timeout=30
            )
            response.raise_for_status()
            
            csrf_token = self._get_csrf_token(response.text)
            
            # Step 2: Submit login credentials
            login_data = {
                "username": email,
                "password": password,
                "embed": "false",
            }
            if csrf_token:
                login_data["_csrf"] = csrf_token
            
            response = self.session.post(
                GARMIN_SSO_SIGNIN,
                params=signin_params,
                data=login_data,
                timeout=30
            )
            
            # Check for MFA requirement
            if "MFA" in response.text or "verification" in response.text.lower():
                if not mfa_callback:
                    raise GarminAuthError(
                        "Multi-factor authentication required. Use interactive login to provide code."
                    )
                
                # Handle MFA
                mfa_code = mfa_callback()
                # Submit MFA code (this part is simplified and would need the actual Garmin MFA endpoint)
                # For this demo/fix, we acknowledge the need for the interactive flow
                logger.info("MFA code received, continuing authentication...")
                
            # Check for login errors
            if "AUTHENTICATION" in response.text and "FAILED" in response.text:
                raise GarminAuthError("Invalid email or password")
            
            # Step 3: Extract the service ticket
            ticket = self._get_ticket(response.text)
            
            # If no ticket in response, try following redirects
            if not ticket and response.history:
                for r in response.history:
                    if 'ticket=' in r.headers.get('Location', ''):
                        parsed = urlparse(r.headers['Location'])
                        params = parse_qs(parsed.query)
                        if 'ticket' in params:
                            ticket = params['ticket'][0]
                            break
            
            if not ticket:
                # Try to get ticket from response URL
                if 'ticket=' in response.url:
                    parsed = urlparse(response.url)
                    params = parse_qs(parsed.query)
                    if 'ticket' in params:
                        ticket = params['ticket'][0]
            
            if not ticket:
                logger.error("Could not obtain service ticket")
                raise GarminAuthError("Login failed - could not obtain service ticket")
            
            # Step 4: Exchange ticket for OAuth tokens
            # This would typically involve calling the flyGarmin OAuth endpoint
            # For now, we'll use the session cookies that were set during login
            
            # Store basic info
            self.tokens.sso_guid = ticket[:36] if len(ticket) >= 36 else ticket
            self.tokens.display_name = email.split('@')[0]
            self.tokens.access_token = ticket
            self.tokens.expires_at = time.time() + 3600  # Assume 1 hour validity
            
            self._save_tokens()
            logger.info(f"Login successful for {email}")
            return True
            
        except GarminAuthError:
            raise
        except requests.RequestException as e:
            raise GarminAuthError(f"Network error during login: {e}")
        except Exception as e:
            raise GarminAuthError(f"Login failed: {e}")
    
    def is_authenticated(self) -> bool:
        """Check if we have valid authentication"""
        return bool(self.tokens.access_token) and not self.tokens.is_expired()
    
    def ensure_authenticated(self) -> bool:
        """Ensure we have valid authentication, refreshing if needed"""
        if self.is_authenticated():
            return True
        
        if self.tokens.refresh_token:
            try:
                return self._refresh_tokens()
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
        
        return False
    
    def _refresh_tokens(self) -> bool:
        """Refresh expired tokens"""
        # Implementation depends on the specific OAuth flow Garmin uses
        # This is a placeholder for the actual refresh logic
        logger.info("Attempting to refresh tokens")
        # For now, return False to indicate refresh failed
        return False
    
    def get_auth_headers(self) -> Dict[str, str]:
        """Get headers for authenticated API requests"""
        return {
            "Authorization": f"{self.tokens.token_type} {self.tokens.access_token}",
        }
    
    def logout(self):
        """Clear authentication tokens"""
        self.tokens = GarminTokens()
        if self.token_file.exists():
            self.token_file.unlink()
        logger.info("Logged out successfully")


class FlyGarminAPI:
    """
    API client for flyGarmin aviation database services.
    
    Provides methods to:
    - List registered devices
    - Get available databases
    - Download database files
    """
    
    def __init__(self, auth: GarminAuth):
        self.auth = auth
        self.session = auth.session
    
    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an authenticated API request"""
        if not self.auth.ensure_authenticated():
            raise GarminAPIError("Not authenticated - please login first")
        
        headers = kwargs.pop('headers', {})
        headers.update(self.auth.get_auth_headers())
        
        response = self.session.request(method, url, headers=headers, **kwargs)
        
        if response.status_code == 401:
            raise GarminAPIError("Authentication expired - please login again")
        
        return response
    
    def get_devices(self) -> list[GarminDevice]:
        """
        Get list of registered Garmin devices.
        
        Returns:
            List of GarminDevice objects
        """
        logger.info("Fetching registered devices")
        
        try:
            response = self._make_request(
                'GET',
                f"{FLYGARMIN_API}/api/user/devices",
                timeout=30
            )
            response.raise_for_status()
            
            devices = []
            data = response.json()
            
            for item in data.get('devices', []):
                device = GarminDevice(
                    device_id=str(item.get('deviceId', '')),
                    device_name=item.get('deviceName', ''),
                    system_id=item.get('systemId', ''),
                    serial_number=item.get('serialNumber', ''),
                    device_type=item.get('deviceType', ''),
                    aircraft_tail=item.get('aircraftTail', ''),
                )
                devices.append(device)
            
            logger.info(f"Found {len(devices)} registered devices")
            return devices
            
        except requests.RequestException as e:
            raise GarminAPIError(f"Failed to get devices: {e}")
    
    def get_available_databases(self, device: GarminDevice) -> list[DatabaseInfo]:
        """
        Get list of available databases for a device.
        
        Args:
            device: The device to get databases for
            
        Returns:
            List of DatabaseInfo objects
        """
        logger.info(f"Fetching databases for device {device.device_name}")
        
        try:
            response = self._make_request(
                'GET',
                f"{FLYGARMIN_API}/api/databases/available",
                params={
                    'deviceId': device.device_id,
                    'systemId': device.system_id,
                },
                timeout=30
            )
            response.raise_for_status()
            
            databases = []
            data = response.json()
            
            for item in data.get('databases', []):
                db = DatabaseInfo(
                    db_id=str(item.get('databaseId', '')),
                    name=item.get('name', ''),
                    coverage=item.get('coverage', ''),
                    version=item.get('version', ''),
                    cycle=item.get('cycle', ''),
                    start_date=item.get('startDate', ''),
                    end_date=item.get('endDate', ''),
                    file_name=item.get('fileName', ''),
                    file_size=item.get('fileSize', 0),
                    download_url=item.get('downloadUrl', ''),
                    db_type=item.get('databaseType', ''),
                )
                databases.append(db)
            
            logger.info(f"Found {len(databases)} available databases")
            return databases
            
        except requests.RequestException as e:
            raise GarminAPIError(f"Failed to get databases: {e}")
    
    def download_database(
        self, 
        database: DatabaseInfo, 
        output_dir: Path,
        progress_callback=None
    ) -> Path:
        """
        Download a database file.
        
        Args:
            database: The database to download
            output_dir: Directory to save the file
            progress_callback: Optional callback for progress updates
            
        Returns:
            Path to the downloaded file
        """
        logger.info(f"Downloading {database.name} ({database.file_size} bytes)")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / database.file_name
        
        try:
            response = self._make_request(
                'GET',
                database.download_url,
                stream=True,
                timeout=300
            )
            response.raise_for_status()
            
            downloaded = 0
            total = database.file_size or int(response.headers.get('content-length', 0))
            
            with open(output_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total:
                            progress_callback(downloaded, total)
            
            logger.info(f"Downloaded to {output_file}")
            return output_file
            
        except requests.RequestException as e:
            raise GarminAPIError(f"Failed to download database: {e}")
