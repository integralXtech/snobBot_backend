"""Authentication package."""

from .auth_service import (
    register_user,
    login_user,
    reset_user_password,
    get_user_profile,
    update_user_password,
    ensure_user_in_database
)

from .auth_routes import auth_router

from .models import (
    RegisterRequest,
    LoginRequest,
    ResetPasswordRequest,
    UserResponse,
    AuthResponse,
    ErrorResponse,
)

__all__ = [
    # Services
    "register_user",
    "login_user", 
    "reset_user_password",
    "get_user_profile",
    "ensure_user_in_database",
    "update_user_password",
    # Routes
    "auth_router",
    # Models
    "RegisterRequest",
    "LoginRequest",
    "ResetPasswordRequest", 
    "UserResponse",
    "AuthResponse",
    "ErrorResponse"
]