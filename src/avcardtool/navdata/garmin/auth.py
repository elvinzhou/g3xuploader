#!/usr/bin/env python3
"""
Garmin Aviation Authentication Module

Handles authentication with Garmin's flyGarmin portal for downloading
aviation databases.

Authentication Flow:
1. GET Garmin SSO signin page to obtain CSRF token
2. POST credentials to Garmin SSO to obtain a service ticket
3. POST service ticket to services.garmin.com/api/oauth/token to obtain
   an OAuth Bearer access token (client_id=FLY_GARMIN_DESKTOP)
4. Use Bearer token for all subsequent flyGarmin API calls

OAuth endpoint discovered by jdmtool (https://github.com/dimaryaz/jdmtool).
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, Tuple
from urllib.parse import urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

# Garmin SSO endpoints
GARMIN_SSO_BASE = "https://sso.garmin.com/sso"
GARMIN_SSO_SIGNIN = f"{GARMIN_SSO_BASE}/signin"

# OAuth token exchange endpoint (discovered by jdmtool)
GARMIN_OAUTH_TOKEN = "https://services.garmin.com/api/oauth/token"
GARMIN_OAUTH_CLIENT_ID = "FLY_GARMIN_DESKTOP"

# flyGarmin API
FLYGARMIN_BASE = "https://fly.garmin.com"
FLYGARMIN_API = f"{FLYGARMIN_BASE}/fly-garmin/api"

# Token storage
TOKEN_FILE = "garmin_tokens.json"

# SSO signin parameters — matched to FLY_GARMIN_DESKTOP client (from jdmtool)
# service/source/gauthHost point to FLYGARMIN_BASE for headless redirect (vs
# localhost in jdmtool's browser flow).
_SSO_PARAMS = {
    "service": FLYGARMIN_BASE,
    "source": FLYGARMIN_BASE,
    "gauthHost": GARMIN_SSO_BASE,
    "locale": "en_US",
    "id": "gauth-widget",
    "cssUrl": "https://static.garmin.com/apps/fly/files/desktop/flygarmin-desktop-gauth-v3.css",
    "reauth": "false",
    "clientId": "FLY_GARMIN_DESKTOP",
    "rememberMeShown": "false",
    "rememberMeChecked": "false",
    "createAccountShown": "true",
    "openCreateAccount": "false",
    "displayNameShown": "false",
    "consumeServiceTicket": "false",
    "initialFocus": "true",
    "embedWidget": "true",
    "socialEnabled": "false",
    "generateExtraServiceTicket": "false",
    "generateTwoExtraServiceTickets": "false",
    "generateNoServiceTicket": "false",
    "globalOptInShown": "false",
    "globalOptInChecked": "false",
    "mobile": "false",
    "connectLegalTerms": "false",
    "showTermsOfUse": "false",
    "showPrivacyPolicy": "false",
    "showConnectLegalAge": "false",
    "locationPromptShown": "false",
    "showPassword": "true",
    "useCustomHeader": "false",
    "mfaRequired": "false",
    "performMFACheck": "false",
    "permanentMFA": "false",
    "rememberMyBrowserShown": "false",
    "rememberMyBrowserChecked": "false",
}


@dataclass
class GarminTokens:
    """Container for Garmin authentication tokens"""
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0
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
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GarminTokens':
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=data.get("expires_at", 0),
            display_name=data.get("display_name", ""),
        )


@dataclass
class GarminDevice:
    """Represents a registered Garmin aircraft/device"""
    aircraft_id: str
    aircraft_name: str
    tail_number: str
    avdbs: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aircraft_id": self.aircraft_id,
            "aircraft_name": self.aircraft_name,
            "tail_number": self.tail_number,
            "avdbs": self.avdbs,
        }


@dataclass
class DatabaseInfo:
    """Information about an available database series"""
    series_id: int
    name: str
    issue_name: str
    db_type: str
    start_date: str
    end_date: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "series_id": self.series_id,
            "name": self.name,
            "issue_name": self.issue_name,
            "db_type": self.db_type,
            "start_date": self.start_date,
            "end_date": self.end_date,
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

    Performs the full SSO + OAuth flow to obtain a Bearer token suitable
    for use with the flyGarmin API.
    """

    def __init__(self, token_dir: Optional[Path] = None):
        self.token_dir = Path(token_dir) if token_dir else (
            Path.home() / ".local" / "share" / "avcardtool"
        )
        self.token_file = self.token_dir / TOKEN_FILE
        self.tokens = GarminTokens()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._load_tokens()

    def _ensure_token_dir(self):
        self.token_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.token_dir, 0o700)

    def _load_tokens(self) -> bool:
        try:
            if self.token_file.exists():
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                self.tokens = GarminTokens.from_dict(data)
                logger.debug(f"Loaded tokens for {self.tokens.display_name}")
                return True
        except Exception as e:
            logger.warning(f"Could not load tokens: {e}")
        return False

    def _save_tokens(self):
        try:
            self._ensure_token_dir()
            with open(self.token_file, 'w') as f:
                json.dump(self.tokens.to_dict(), f, indent=2)
            os.chmod(self.token_file, 0o600)
            logger.debug("Tokens saved")
        except Exception as e:
            logger.warning(f"Could not save tokens: {e}")

    def _get_csrf_token(self, html: str) -> Optional[str]:
        for pattern in [
            r'name="_csrf"\s+value="([^"]+)"',
            r'name="csrf_token"\s+value="([^"]+)"',
            r'"_csrf":\s*"([^"]+)"',
        ]:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def _extract_ticket(self, response: requests.Response) -> Optional[Tuple[str, str]]:
        """
        Extract (service_ticket, service_url) from a response.

        Garmin's embedWidget=true success page embeds the ticket in JS variables:
            var response_url = "https:\\/\\/fly.garmin.com?ticket=ST-...";
            var service_url  = "https:\\/\\/fly.garmin.com";

        Falls back to checking redirects and query params.
        Returns (ticket, service_url) or None.
        """
        # Primary: parse JS variables from Garmin's casEmbedSuccess.html
        response_url_match = re.search(
            r'var\s+response_url\s*=\s*"([^"]+)"', response.text
        )
        service_url_match = re.search(
            r'var\s+service_url\s*=\s*"([^"]+)"', response.text
        )
        if response_url_match:
            response_url = response_url_match.group(1).replace(r'\/', '/')
            service_url = (
                service_url_match.group(1).replace(r'\/', '/')
                if service_url_match
                else FLYGARMIN_BASE
            )
            qs = parse_qs(urlparse(response_url).query)
            if 'ticket' in qs:
                ticket = qs['ticket'][0]
                logger.debug(f"Ticket extracted from JS response_url (service_url={service_url})")
                return ticket, service_url

        # Fallback: redirect chain and final URL
        for r in response.history:
            location = r.headers.get('Location', '')
            if 'ticket=' in location:
                qs = parse_qs(urlparse(location).query)
                if 'ticket' in qs:
                    return qs['ticket'][0], FLYGARMIN_BASE

        if 'ticket=' in response.url:
            qs = parse_qs(urlparse(response.url).query)
            if 'ticket' in qs:
                return qs['ticket'][0], FLYGARMIN_BASE

        return None

    def login(self, email: str, password: str,
              mfa_callback: Optional[Callable[[], str]] = None) -> bool:
        """
        Perform the full SSO + OAuth login flow.

        Args:
            email: Garmin account email
            password: Garmin account password
            mfa_callback: Optional callable that returns an MFA code string

        Returns:
            True on success; raises GarminAuthError on failure.
        """
        try:
            # Step 1: GET SSO signin page → CSRF token
            response = self.session.get(
                GARMIN_SSO_SIGNIN,
                params=_SSO_PARAMS,
                headers={"Referer": FLYGARMIN_BASE},
                timeout=30,
            )
            response.raise_for_status()
            csrf_token = self._get_csrf_token(response.text)
            logger.debug(f"CSRF token found: {bool(csrf_token)}")

            # Step 2: POST credentials → service ticket
            login_data: Dict[str, str] = {
                "username": email,
                "password": password,
                "embed": "false",
            }
            if csrf_token:
                login_data["_csrf"] = csrf_token
            else:
                logger.warning("No CSRF token found in login page — POST may be rejected")

            response = self.session.post(
                GARMIN_SSO_SIGNIN,
                params=_SSO_PARAMS,
                data=login_data,
                headers={
                    "Referer": response.url,
                    "Origin": "https://sso.garmin.com",
                },
                timeout=30,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait_msg = f" Try again in {retry_after} seconds." if retry_after else " Wait a few minutes and try again."
                raise GarminAuthError(
                    f"Garmin is rate-limiting logins from your IP (HTTP 429).{wait_msg}"
                )
            if response.status_code >= 400:
                raise GarminAuthError(
                    f"Garmin SSO returned HTTP {response.status_code}"
                )

            if "AUTHENTICATION" in response.text and "FAILED" in response.text:
                raise GarminAuthError("Invalid email or password")

            # Garmin MFA challenge pages contain a one-time code input field
            mfa_required = (
                'name="mfa"' in response.text
                or 'id="mfa"' in response.text
                or '"MFACode"' in response.text
                or 'one-time' in response.text.lower()
            )
            if mfa_required:
                if not mfa_callback:
                    raise GarminAuthError(
                        "Multi-factor authentication required. "
                        "Use interactive login to provide the code."
                    )
                mfa_code = mfa_callback()
                # Re-submit with MFA code
                mfa_data = {"mfa": mfa_code, "embed": "false"}
                if csrf_token:
                    mfa_data["_csrf"] = csrf_token
                response = self.session.post(
                    GARMIN_SSO_SIGNIN,
                    params=_SSO_PARAMS,
                    data=mfa_data,
                    timeout=30,
                )

            result = self._extract_ticket(response)
            if not result:
                raise GarminAuthError("Login failed — could not obtain service ticket")
            ticket, service_url = result

            # Step 3: Exchange service ticket for OAuth Bearer token
            token_response = requests.post(
                GARMIN_OAUTH_TOKEN,
                data={
                    "grant_type": "service_ticket",
                    "client_id": GARMIN_OAUTH_CLIENT_ID,
                    "service_url": service_url,
                    "service_ticket": ticket,
                },
                timeout=30,
            )
            token_response.raise_for_status()
            token_data = token_response.json()

            self.tokens = GarminTokens(
                access_token=token_data.get("access_token", ""),
                refresh_token=token_data.get("refresh_token", ""),
                token_type=token_data.get("token_type", "Bearer"),
                expires_at=time.time() + token_data.get("expires_in", 3600),
                display_name=email.split('@')[0],
            )
            self._save_tokens()
            logger.debug(f"Login successful for {email}")
            return True

        except GarminAuthError:
            raise
        except requests.RequestException as e:
            raise GarminAuthError(f"Network error during login: {e}")
        except Exception as e:
            raise GarminAuthError(f"Login failed: {e}")

    def is_authenticated(self) -> bool:
        return bool(self.tokens.access_token) and not self.tokens.is_expired()

    def ensure_authenticated(self) -> bool:
        if self.is_authenticated():
            return True
        if not self.tokens.access_token:
            logger.warning(f"No Garmin tokens found (looked in {self.token_file}). Run 'avcardtool navdata login' first.")
            return False
        if self.tokens.is_expired():
            expires_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.tokens.expires_at))
            logger.warning(f"Garmin access token expired at {expires_str} (now {time.strftime('%H:%M:%S')})")
            if self.tokens.refresh_token:
                try:
                    return self._refresh_tokens()
                except Exception as e:
                    logger.warning(f"Token refresh failed: {e}")
                logger.warning("Token refresh failed. Re-run 'avcardtool navdata login'.")
            else:
                logger.warning("No refresh token available. Re-run 'avcardtool navdata login'.")
        return False

    def _refresh_tokens(self) -> bool:
        """Attempt to refresh the access token using the refresh token."""
        try:
            response = requests.post(
                GARMIN_OAUTH_TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "client_id": GARMIN_OAUTH_CLIENT_ID,
                    "refresh_token": self.tokens.refresh_token,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            self.tokens.access_token = data.get("access_token", "")
            self.tokens.expires_at = time.time() + data.get("expires_in", 3600)
            if "refresh_token" in data:
                self.tokens.refresh_token = data["refresh_token"]
            self._save_tokens()
            logger.debug("Tokens refreshed")
            return True
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
            return False

    def get_auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"{self.tokens.token_type} {self.tokens.access_token}"}

    def logout(self):
        self.tokens = GarminTokens()
        if self.token_file.exists():
            self.token_file.unlink()
        logger.debug("Logged out")
