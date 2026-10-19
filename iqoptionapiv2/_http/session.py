"""
HTTP Session Management for IQ Option.
Handles cookies, headers, and session persistence.
"""

import requests
import threading
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import logging
logger = logging.getLogger(__name__)


class IQSession:
    """
    Manages HTTP session with cookie persistence and thread safety.
    
    Features:
        - Automatic cookie handling
        - Thread-safe session access
        - Session validation
        - Cookie persistence across requests
    """
    
    def __init__(self):
        """Initialize a new session with default headers."""
        self._session = requests.Session()
        self._lock = threading.RLock()
        self._setup_default_headers()
    
    def _setup_default_headers(self):
        """Set default headers that mimic a real browser."""
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://iqoption.com',
            'Referer': 'https://iqoption.com/',
            'Connection': 'keep-alive',
        })
    
    # ═══════════════════════════════════════════════════════════════════
    #  Core Session Methods
    # ═══════════════════════════════════════════════════════════════════
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """Perform GET request with thread safety."""
        with self._lock:
            return self._session.get(url, **kwargs)
    
    def post(self, url: str, data: Dict = None, **kwargs) -> requests.Response:
        """Perform POST request with thread safety."""
        with self._lock:
            return self._session.post(url, data=data, **kwargs)
    
    def get_cookie(self, name: str) -> Optional[str]:
        """Get a specific cookie value."""
        with self._lock:
            if name in self._session.cookies:
                return self._session.cookies[name]
            return None
    
    def set_cookie(self, name: str, value: str, domain: str = None):
        """Set a cookie manually."""
        with self._lock:
            self._session.cookies.set(name, value, domain=domain)
    
    def get_all_cookies(self) -> Dict[str, str]:
        """Get all cookies as dictionary."""
        with self._lock:
            return self._session.cookies.get_dict()
    
    def clear_cookies(self):
        """Clear all session cookies."""
        with self._lock:
            self._session.cookies.clear()
    
    def is_session_valid(self) -> bool:
        """Check if session has a valid SSID."""
        ssid = self.get_cookie('ssid')
        return ssid is not None and len(ssid) > 0
    
    def update_headers(self, headers: Dict[str, str]):
        """Update session headers."""
        with self._lock:
            self._session.headers.update(headers)
    
    def close(self):
        """Close the session and release resources."""
        with self._lock:
            self._session.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures session is closed."""
        self.close()