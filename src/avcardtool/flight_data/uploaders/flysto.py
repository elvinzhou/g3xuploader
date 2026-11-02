"""
FlySto flight data uploader.

Uploads flight data to FlySto using OAuth2 authentication.
"""

import json
import logging
import os
import time
import zipfile
import tempfile
import requests
from pathlib import Path
from typing import Optional, Tuple

from avcardtool.flight_data.base import FlightData, FlightDataUploader, UploadResult

logger = logging.getLogger(__name__)


class FlyStoUploader(FlightDataUploader):
    """
    Upload flight data to FlySto (flysto.net).

    FlySto uses OAuth2 authentication. To use this uploader:
    1. Contact FlySto to register your application and get client_id and client_secret
    2. Complete the OAuth flow once to get refresh_token
    3. The uploader will automatically refresh access tokens as needed

    OAuth Flow (one-time setup):
    1. Open browser: https://www.flysto.net/oauth/authorize?response_type=code&client_id=<app-id>&redirect_uri=<redirect-uri>
    2. User grants permission, redirected to: <redirect-uri>?code=<AUTHORIZATION_CODE>
    3. Exchange code for tokens (see exchange_code_for_tokens method)
    4. Store refresh_token in config

    Configuration:
        enabled: bool - Enable/disable this uploader
        client_id: str - OAuth2 client ID
        client_secret: str - OAuth2 client secret
        redirect_uri: str - OAuth2 redirect URI (default: http://localhost:8080/callback)
        refresh_token: str - OAuth2 refresh token (obtained during initial setup)
        auth_relay_url: str - ADVANCED. Base URL of an auth relay (see relay/).
            Leave empty unless you know you need it. When set, login and token
            refresh go through the relay, which holds the client secret, so
            client_id/client_secret are not needed on this device (useful for
            headless devices such as a Raspberry Pi). Flight logs are still
            uploaded directly to FlySto and never pass through the relay.
            Run `avcardtool flight flysto-login` to pair the device.
    """

    OAUTH_TOKEN_URL = "https://www.flysto.net/oauth/token"
    UPLOAD_URL = "https://www.flysto.net/public-api/log-upload"
    SERVICE_NAME = "FlySto"

    def __init__(self, config: dict):
        """
        Initialize FlySto uploader.

        Args:
            config: Configuration dictionary with OAuth2 credentials
        """
        super().__init__(config)
        self.client_id = config.get('client_id', '')
        self.client_secret = config.get('client_secret', '')
        self.redirect_uri = config.get('redirect_uri', 'http://localhost:8080/callback')
        # ADVANCED: optional auth relay; empty means the direct OAuth flow.
        self.auth_relay_url = (config.get('auth_relay_url') or '').strip().rstrip('/')

        # Tokens - can be provided in config or loaded from token file
        self.access_token = config.get('access_token', '')
        self.refresh_token = config.get('refresh_token', '')
        self.token_expires_at = 0

        # Token storage file
        data_dir = config.get('data_dir', '/var/lib/avcardtool')
        self.token_file = Path(data_dir) / 'flysto_tokens.json'

        # Try to load tokens from file if not in config
        if not self.access_token or not self.refresh_token:
            self._load_tokens()

    def _load_tokens(self):
        """Load tokens from persistent storage"""
        try:
            if self.token_file.exists():
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token', '')
                    self.refresh_token = data.get('refresh_token', '')
                    self.token_expires_at = data.get('expires_at', 0)
        except Exception as e:
            logger.warning(f"Could not load FlySto tokens: {e}")

    def _save_tokens(self):
        """Save tokens to persistent storage"""
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.token_file, 'w') as f:
                json.dump({
                    'access_token': self.access_token,
                    'refresh_token': self.refresh_token,
                    'expires_at': self.token_expires_at
                }, f)

            # Secure the token file
            os.chmod(self.token_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not save FlySto tokens: {e}")

    def _refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token"""
        if not self.refresh_token:
            logger.error("No FlySto refresh token available")
            return False

        try:
            if self.auth_relay_url:
                response = requests.post(
                    f"{self.auth_relay_url}/api/token/refresh",
                    json={'refresh_token': self.refresh_token},
                    timeout=30
                )
            else:
                response = requests.post(
                    self.OAUTH_TOKEN_URL,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    data={
                        'grant_type': 'refresh_token',
                        'client_id': self.client_id,
                        'client_secret': self.client_secret,
                        'refresh_token': self.refresh_token
                    },
                    timeout=30
                )

            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token', '')
                # Update refresh token if a new one is provided
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data['refresh_token']
                # Calculate expiration time (subtract 60 seconds for safety margin)
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60

                self._save_tokens()
                logger.info("Successfully refreshed FlySto access token")
                return True
            else:
                logger.error(f"Failed to refresh FlySto token: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error refreshing FlySto token: {e}")
            return False

    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token, refreshing if necessary"""
        if not self.access_token or time.time() >= self.token_expires_at:
            return self._refresh_access_token()
        return True

    def authenticate(self) -> bool:
        """
        Verify authentication credentials.

        Returns:
            True if credentials are valid and we can obtain a valid access token
        """
        if not self.auth_relay_url and (not self.client_id or not self.client_secret):
            logger.error("FlySto client_id and client_secret not configured")
            return False

        if not self.refresh_token:
            logger.error("FlySto refresh_token not configured. Run OAuth setup first.")
            return False

        return self._ensure_valid_token()

    def get_authorization_url(self) -> str:
        """Get the URL to start the OAuth authorization flow"""
        return (
            f"https://www.flysto.net/oauth/authorize?"
            f"response_type=code&"
            f"client_id={self.client_id}&"
            f"redirect_uri={self.redirect_uri}"
        )

    def exchange_code_for_tokens(self, authorization_code: str) -> Tuple[bool, str]:
        """
        Exchange an authorization code for access and refresh tokens.
        This is used during initial setup.

        Usage:
            1. Open browser to get authorization code
            2. Call this method with the code
            3. Tokens are saved automatically

        Args:
            authorization_code: Authorization code from OAuth callback

        Returns:
            Tuple of (success, message)
        """
        try:
            response = requests.post(
                self.OAUTH_TOKEN_URL,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data={
                    'grant_type': 'authorization_code',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': authorization_code,
                    'redirect_uri': self.redirect_uri
                },
                timeout=30
            )

            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token', '')
                self.refresh_token = token_data.get('refresh_token', '')
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60

                self._save_tokens()
                logger.info("Successfully obtained FlySto tokens")
                return True, "Tokens obtained and saved successfully"
            else:
                error_msg = f"Failed to exchange code: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return False, error_msg

        except Exception as e:
            error_msg = f"Error exchanging code for tokens: {e}"
            logger.error(error_msg)
            return False, error_msg

    def login_via_relay(self, timeout: int = 300, poll_interval: float = 2.0,
                        echo=print) -> Tuple[bool, str]:
        """
        Pair this device through the auth relay (ADVANCED, auth_relay_url).

        Starts a session on the relay, shows a link (and a QR code when the
        optional `qrcode` package is installed), then polls until the user has
        authorized FlySto in a browser on another device.

        Returns:
            Tuple of (success, message)
        """
        if not self.auth_relay_url:
            return False, "auth_relay_url is not configured"

        try:
            response = requests.post(f"{self.auth_relay_url}/api/session/new", timeout=30)
            response.raise_for_status()
            session = response.json()
            session_id = session['session_id']
            auth_url = session['auth_url']
        except Exception as e:
            return False, f"Could not start relay session: {e}"

        try:
            import qrcode
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L,
                               box_size=1, border=2)
            qr.add_data(auth_url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            pass
        echo(f"\nOpen this link on your phone or laptop to authorize FlySto:\n{auth_url}\n")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                poll = requests.get(f"{self.auth_relay_url}/api/session/poll",
                                    params={'session_id': session_id}, timeout=30)
            except requests.RequestException as e:
                logger.debug(f"Relay poll error (will retry): {e}")
                continue
            if poll.status_code == 404:
                return False, "Session expired. Please run login again."
            if poll.status_code != 200:
                continue
            data = poll.json()
            if data.get('status') == 'complete':
                tokens = data.get('tokens', {})
                self.access_token = tokens.get('access_token', '')
                self.refresh_token = tokens.get('refresh_token', '')
                self.token_expires_at = tokens.get('expires_at', time.time() + 3600) - 60
                self._save_tokens()
                return True, "Authorization complete! Device successfully linked."
            if data.get('status') == 'expired':
                return False, "Session expired. Please run login again."
        return False, "Timed out waiting for authorization."

    def _build_zip_payload(self, flight_data: FlightData) -> bytes:
        """Build the ZIP payload that would be sent to FlySto."""
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_zip:
            zip_path = tmp_zip.name
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(flight_data.file_path, Path(flight_data.file_path).name)
            with open(zip_path, 'rb') as f:
                return f.read()
        finally:
            try:
                os.unlink(zip_path)
            except OSError:
                pass

    def upload_flight(
        self,
        flight_data: FlightData,
        analysis_results: Optional[dict] = None
    ) -> UploadResult:
        """
        Upload flight data to FlySto.

        The file must be uploaded as a ZIP (one log file per ZIP).

        Args:
            flight_data: Parsed flight data with file path
            analysis_results: Optional analysis results (not used by FlySto)

        Returns:
            UploadResult with success status and details
        """
        if not self.enabled:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="FlySto upload not enabled"
            )

        # In debug mode, build and save the exact payload regardless of credentials
        if self.debug:
            try:
                zip_content = self._build_zip_payload(flight_data)
                debug_filename = f"flysto_{Path(flight_data.file_path).stem}.zip"
                self._save_debug_payload(debug_filename, zip_content)
                logger.info(f"[DEBUG] FlySto payload saved: {debug_filename} ({len(zip_content)} bytes, contains {Path(flight_data.file_path).name})")
            except Exception as e:
                logger.warning(f"[DEBUG] Could not build FlySto debug payload: {e}")

        if not self.auth_relay_url and (not self.client_id or not self.client_secret):
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="FlySto client_id and client_secret not configured"
            )

        if not self.refresh_token and self.auth_relay_url:
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="FlySto not authorized. Run: avcardtool flight flysto-login"
            )

        if not self.refresh_token:
            # Provide setup instructions
            auth_url = self.get_authorization_url()
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=(
                    f"FlySto not authorized. Complete OAuth setup:\n"
                    f"1. Open: {auth_url}\n"
                    f"2. Grant permission\n"
                    f"3. Copy the 'code' parameter from redirect URL\n"
                    f"4. Run: avcardtool flight flysto-auth <code>"
                )
            )

        # Checked before the token refresh, which is itself a network call
        if self.dry_run:
            return self._dry_run_result(self.SERVICE_NAME, f"payload: debug/flysto_{Path(flight_data.file_path).stem}.zip")

        # Ensure we have a valid access token
        if not self._ensure_valid_token():
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message="Failed to obtain valid FlySto access token"
            )

        try:
            zip_content = self._build_zip_payload(flight_data)

            response = requests.post(
                self.UPLOAD_URL,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/zip'
                },
                data=zip_content,
                timeout=120
            )

            if response.status_code in [200, 201]:
                logger.info(f"Successfully uploaded to FlySto: {flight_data.file_path.name}")
                return UploadResult(
                    success=True,
                    service=self.SERVICE_NAME,
                    message="Upload successful"
                )
            elif response.status_code == 401:
                # Token might have expired, try refreshing
                if self._refresh_access_token():
                    # Retry upload with new token
                    response = requests.post(
                        self.UPLOAD_URL,
                        headers={
                            'Authorization': f'Bearer {self.access_token}',
                            'Content-Type': 'application/zip'
                        },
                        data=zip_content,
                        timeout=120
                    )
                    if response.status_code in [200, 201]:
                        logger.info(f"Successfully uploaded to FlySto (after token refresh): {flight_data.file_path.name}")
                        return UploadResult(
                            success=True,
                            service=self.SERVICE_NAME,
                            message="Upload successful (after token refresh)"
                        )

                error_msg = f"Authentication failed: {response.status_code}"
                logger.error(f"FlySto {error_msg}")
                return UploadResult(
                    success=False,
                    service=self.SERVICE_NAME,
                    message=error_msg
                )
            else:
                error_msg = f"Upload failed: {response.status_code} - {response.text}"
                logger.error(f"FlySto {error_msg}")
                return UploadResult(
                    success=False,
                    service=self.SERVICE_NAME,
                    message=error_msg
                )

        except Exception as e:
            error_msg = f"Upload error: {str(e)}"
            logger.error(f"FlySto {error_msg}")
            return UploadResult(
                success=False,
                service=self.SERVICE_NAME,
                message=error_msg
            )
