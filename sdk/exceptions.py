# ==========================
# 📁 sdk/exceptions.py
# ==========================
from typing import Optional # Added for type hinting if not already present globally

class ForgeIQSDKError(Exception):
    """Base exception for ForgeIQ SDK errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_body: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_body = error_body

    def __str__(self):
        return f"{self.message} (Status: {self.status_code}, Body: {self.error_body if self.error_body else 'N/A'})"

class APIError(ForgeIQSDKError):
    """Raised for general API errors."""
    pass

class AuthenticationError(ForgeIQSDKError):
    """Raised for authentication failures."""
    def __init__(self, message: str = "Authentication failed. Check API key or credentials.", status_code: int = 401, error_body: Optional[str] = None):
        super().__init__(message, status_code, error_body)

class NotFoundError(ForgeIQSDKError):
    """Raised when a resource is not found (404)."""
    def __init__(self, message: str = "Resource not found.", status_code: int = 404, error_body: Optional[str] = None):
        super().__init__(message, status_code, error_body)

class RequestTimeoutError(APIError):
    """Raised for request timeouts."""
    def __init__(self, message: str = "Request timed out.", status_code: int = 408, error_body: Optional[str] = None):
        super().__init__(message, status_code, error_body)
