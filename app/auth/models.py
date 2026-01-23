"""Pydantic models for authentication request/response validation."""

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime
from pydantic_core import PydanticCustomError
class RegisterRequest(BaseModel):
    """User registration request model."""
    email: EmailStr
    password: str = Field(...)
    name: str = Field(..., min_length=1, max_length=100)

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            # Clean custom error—no "Value error" prefix
            raise PydanticCustomError(
                "password_too_short",  # custom type
                "Password should have at least 6 characters"
            )
        return v

class LoginRequest(BaseModel):
    """User login request model."""
    email: EmailStr
    password: str


class ResetPasswordRequest(BaseModel):
    """Password reset request model."""
    email: EmailStr


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    name: str
    approved: bool
    created_at: Optional[datetime] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None


class AuthResponse(BaseModel):
    """Authentication response model."""
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    user: Optional[UserResponse] = None


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str
    detail: Optional[str] = None
class UpdatePasswordRequest(BaseModel):
    """Model for updating password after reset link is clicked."""
    access_token: str = Field(..., description="Access token from the reset email link")
    refresh_token: str = Field(..., description="Refresh token from the reset email link")
    password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


class UpdatePasswordResponse(BaseModel):
    success: bool
    message: Optional[str] = None
