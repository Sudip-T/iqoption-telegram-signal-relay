"""
Centralized error handling for the application.
"""

import logging
from typing import Dict, Type, Tuple, Callable

logger = logging.getLogger(__name__)


class ErrorHandler:
    """Centralized error handling with registration."""
    
    def __init__(self):
        self._handlers: Dict[Type[Exception], Tuple[str, str, Callable]] = {}
    
    def register(self, exception_type: Type[Exception], title: str, message: str, 
                 post_handler: Callable = None):
        """Register an exception handler."""
        self._handlers[exception_type] = (title, message, post_handler)
    
    def handle(self, e: Exception, log_func=logger.critical) -> int:
        """Handle exception and return exit code."""
        log_func("=" * 70)
        
        for exc_type, (title, message, post_handler) in self._handlers.items():
            if isinstance(e, exc_type):
                log_func(f"❌ FATAL: {title}")
                log_func("=" * 70)
                log_func(message)
                log_func("=" * 70)
                
                if post_handler:
                    try:
                        post_handler(e)
                    except Exception:
                        pass
                
                return 1
        
        # Default handler
        log_func("❌ FATAL: UNEXPECTED ERROR")
        log_func("=" * 70)
        log_func(f"Error Type: {type(e).__name__}")
        log_func(f"Error Message: {e}")
        log_func("=" * 70)
        
        # Optional: Log full traceback in debug mode
        import traceback
        log_func(traceback.format_exc())
        
        return 1


# Create global instance
error_handler = ErrorHandler()


# Helper function to register all error handlers
def register_iq_option_error_handlers():
    """Register all IQ Option related error handlers."""
    from iqoptionapiv2._http.auth import (
        IQAuthCredentialsError, IQAuthRateLimitError,
        IQAuthBlockedError, IQAuthAPIError
    )
    
    error_handler.register(
        IQAuthCredentialsError,
        "INVALID IQ OPTION CREDENTIALS",
        "Please check your email and password in the .env file"
    )
    
    error_handler.register(
        IQAuthBlockedError,
        "IQ OPTION ACCOUNT BLOCKED",
        "Your IP or account has been blocked by IQ Option\nPlease check if you can login via web browser"
    )
    
    error_handler.register(
        IQAuthRateLimitError,
        "TOO MANY LOGIN ATTEMPTS",
        "IQ Option is rate limiting your login attempts\nPlease wait 10-15 minutes before restarting"
    )
    
    error_handler.register(
        IQAuthAPIError,
        "IQ OPTION API ERROR",
        "System cannot continue without IQ Option connection"
    )
    
    error_handler.register(
        ConnectionError,
        "CONNECTION ERROR",
        "System cannot continue without IQ Option connection"
    )