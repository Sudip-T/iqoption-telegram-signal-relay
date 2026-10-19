"""
HTTP Authentication module for IQ Option.
Handles login, logout, and session management.
"""

import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IQAuthError(Exception):
    """Base exception for authentication errors."""
    
    @property
    def title(self) -> str:
        """Short title for display."""
        return "AUTHENTICATION ERROR"
    
    @property
    def message(self) -> str:
        """User-friendly error message."""
        return str(self)


class IQAuthCredentialsError(IQAuthError):
    """Invalid credentials."""
    
    @property
    def title(self) -> str:
        return "INVALID IQ OPTION CREDENTIALS"
    
    @property
    def message(self) -> str:
        return "Please check your email and password in the .env file"


class IQAuthRateLimitError(IQAuthError):
    """Too many login attempts."""
    
    @property
    def title(self) -> str:
        return "TOO MANY LOGIN ATTEMPTS"
    
    @property
    def message(self) -> str:
        return "IQ Option is rate limiting your login attempts\nPlease wait 10-15 minutes before restarting"


class IQAuthBlockedError(IQAuthError):
    """IP blocked or account restricted."""
    
    @property
    def title(self) -> str:
        return "IQ OPTION ACCOUNT BLOCKED"
    
    @property
    def message(self) -> str:
        return "Your IP or account has been blocked by IQ Option\nPlease check if you can login via web browser"


class IQAuthAPIError(IQAuthError):
    """API returned an error."""
    
    def __init__(self, message: str, api_message: str = None):
        super().__init__(message)
        self.api_message = api_message
    
    @property
    def title(self) -> str:
        return "IQ OPTION API ERROR"
    
    @property
    def message(self) -> str:
        if self.api_message:
            return f"API Error: {self.api_message}"
        return str(self)
    

class IQAuthSession:
    """
    Handles IQ Option authentication using requests.
    Simple, stable, and proven.
    """
    
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        # self._setup_session()
    
    def _setup_session(self):
        """Setup session with default headers."""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://iqoption.com',
            'Referer': 'https://iqoption.com/',
        })
    
    def get_ssid(self) -> str:
        """
        Authenticate with IQ Option and retrieve SSID.
        
        Returns:
            Session ID (SSID) string
            
        Raises:
            IQAuthCredentialsError: Invalid email/password
            IQAuthRateLimitError: Too many login attempts
            IQAuthBlockedError: IP or account blocked
            IQAuthAPIError: API returned an error
            ConnectionError: Network issues
        """
        if not all([self.email, self.password]):
            raise IQAuthCredentialsError("Email and password are required!")
        
        try:
            response = self.session.post(
                url='https://api.iqoption.com/v2/login',
                data={'identifier': self.email, 'password': self.password},
                timeout=30
            )
            
            # Handle different status codes
            if response.status_code == 200:
                # Try to parse response
                try:
                    data = response.json()
                    
                    # Check for error in response
                    if data.get('code') == 'error' or data.get('isError'):
                        error_msg = data.get('message', 'Unknown error')
                        logger.error(f"Login API error: {error_msg}")
                        raise IQAuthAPIError(f"Login failed: {error_msg}")
                    
                except ValueError:
                    # Not JSON - but login might still be successful
                    pass
                
                # Check for SSID
                ssid = self.get_session_id()
                if ssid:
                    logger.info(f"Successfully logged in as {self.email}")
                    return ssid
                else:
                    raise IQAuthAPIError("Login successful but no SSID received")
            
            elif response.status_code == 400:
                raise IQAuthCredentialsError("Invalid request format (400)")
            
            elif response.status_code == 401:
                raise IQAuthCredentialsError("Invalid email or password (401)")
            
            elif response.status_code == 403:
                raise IQAuthBlockedError("Access denied - IP or account blocked (403)")
            
            elif response.status_code == 429:
                raise IQAuthRateLimitError("Too many login attempts - please wait (429)")
            
            else:
                raise IQAuthAPIError(f"HTTP {response.status_code}: {response.text[:100]}")
                
        except requests.Timeout:
            raise ConnectionError("Login request timed out after 30 seconds")
        
        except requests.ConnectionError as e:
            raise ConnectionError(f"Network error: {e}")
        
        except requests.RequestException as e:
            raise ConnectionError(f"Request failed: {e}")
    
    def logout(self) -> bool:
        """Log out from IQ Option."""
        try:
            response = self.session.post(
                url="https://auth.iqoption.com/api/v1.0/logout", timeout=10)
            if response.status_code == 200:
                self.session.cookies.clear()
                logger.info("Logged out successfully")
                return True
        except Exception as e:
            logger.warning(f"Logout error: {e}")
        return False
    
    def get_session_id(self) -> Optional[str]:
        """Get current session ID from cookies."""
        return self.session.cookies.get('ssid')
    
    def clear_session(self):
        """Clear all session cookies."""
        self.session.cookies.clear()
    
    def close(self):
        """Close the session."""
        self.session.close()