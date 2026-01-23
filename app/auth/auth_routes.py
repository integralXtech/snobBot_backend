"""Authentication routes for the FastAPI application."""

from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse
import logging
import requests
from .models import (
    RegisterRequest, 
    LoginRequest, 
    ResetPasswordRequest,
    AuthResponse,
    UpdatePasswordRequest,
    UpdatePasswordResponse,
)
from .auth_service import (
    register_user,
    login_user,
    reset_user_password,
    update_user_password,
    ensure_user_in_database
)
from app.supabase.supabase_client import get_supabase_client
from app.core.config import settings

logger = logging.getLogger(__name__)

# Create router for auth endpoints
auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

# Get Supabase client
supabase = get_supabase_client()

@auth_router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Register a new user with email, password, and name"
)
async def register(user_data: RegisterRequest):
    """Register a new user."""
    try:
        result = await register_user(user_data)
        
        if not result.get("success", True):
            logger.warning(f"Registration failed for {user_data.email}: {result.get('message', 'Unknown error')}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get('message', 'Registration failed')
            )
        
        logger.info(f"User {user_data.email} registered successfully")
        return AuthResponse(
            success=True,
            message="Please confirm your email to complete signup",
            user=result.get('user')
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during registration: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during registration"
        )

@auth_router.post(
    "/update-password",
    response_model=UpdatePasswordResponse,
    summary="Update password after reset",
    description="Update user password using tokens from the reset email link"
)
async def update_password(data: UpdatePasswordRequest):
    """Update user password after reset link click."""
    if data.password != data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match"
        )

    result = await update_user_password(
        data.access_token,
        data.refresh_token,
        data.password
    )

    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"]
        )

    return UpdatePasswordResponse(
        success=True,
        message="Password updated successfully"
    )

# ---------- routes/auth.py ----------
@auth_router.post(
    "/login",
    response_model=AuthResponse,
    summary="Login user",
    description="Login user with email and password"
)
async def login(user_data: LoginRequest):
    """Login a user with Supabase Auth."""
    result = await login_user(user_data)

    # ✅ Only raise if actual error string present
    if result.get("error"):
        logger.warning(f"Login failed for {user_data.email}: {result['error']}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result["error"]
        )

    logger.info(f"User {user_data.email} logged in successfully")

    return AuthResponse(
        success=True,
        message="Login successful",
        user=result["user"]
    )

@auth_router.post(
    "/reset-password",
    response_model=AuthResponse,
    summary="Reset user password",
    description="Send password reset email to user"
)
async def reset_password(reset_data: ResetPasswordRequest):
    """Reset user password."""
    try:
        result = await reset_user_password(reset_data.email)

        # Log internally if there’s an error, but always respond 200
        if result.get('error'):
            logger.warning(f"Password reset attempted for {reset_data.email}: {result['error']}")

        # Always return success to the client
        return AuthResponse(
            success=True,
            message="A password reset link has been sent to your email."
        )

    except Exception as e:
        logger.error(f"Unexpected error during password reset: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during password reset"
        )

# Google OAuth Authentication Routes
@auth_router.get(
    "/login/google",
    summary="Start Google OAuth login",
    description="Redirects user to Google OAuth for authentication"
)
async def login_google():
    """Start Google OAuth login process."""
    try:
        # redirect_url = "http://localhost:8000/api/auth/callback"
        redirect_url = f"{settings.backend_url}/api/auth/callback" if hasattr(settings, 'backend_url') else "http://localhost:8000/api/auth/callback"
        res = supabase.auth.sign_in_with_oauth(
            {
                "provider": "google",
                "options": {"redirect_to": redirect_url},
            }
        )
        return RedirectResponse(res.url)
    except Exception as e:
        logger.error(f"Google OAuth login failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate Google OAuth login"
        )

@auth_router.get(
    "/callback",
    summary="OAuth callback handler",
    description="Handles OAuth callback from Google with authorization code"
)
async def auth_callback(request: Request):
    """Handle OAuth callback from Google."""
    try:
        code = request.query_params.get("code")
        if not code:
            logger.warning("OAuth callback received without authorization code")
            # Use frontend URL from settings
            frontend_login = f"{settings.frontend_url}/login?error=no_code"
            return RedirectResponse(frontend_login)

        # Exchange code for session
        auth_response = supabase.auth.exchange_code_for_session({"auth_code": code})

        if not auth_response or not auth_response.session:
            logger.error("Failed to exchange authorization code for session")
            frontend_login = f"{settings.frontend_url}/login?error=auth_failed"
            return RedirectResponse(frontend_login)

        session = auth_response.session  

        # Ensure user exists in DB
        if session.user:
            try:
                user_data = {
                    "id": session.user.id,
                    "email": session.user.email,
                    "name": session.user.user_metadata.get("full_name", session.user.email),
                    "approved": True,
                }
                await ensure_user_in_database(user_data)
                logger.info(f"Google user ensured in DB: {user_data['email']}")
            except Exception as e:
                logger.error(f"DB insert failed for Google user: {str(e)}")
                # continue anyway

        # Redirect to frontend dashboard with tokens
        frontend_dashboard = (
            f"{settings.frontend_url}/google/auth?"
            f"access_token={session.access_token}&refresh_token={session.refresh_token}"
        )
        logger.info(f"OAuth success, redirecting user {session.user.email} to dashboard")
        return RedirectResponse(url=frontend_dashboard, status_code=303)

    except Exception as e:
        logger.error(f"Unexpected error during OAuth callback: {str(e)}")
        frontend_login = f"{settings.frontend_url}/login?error=server_error"
        return RedirectResponse(frontend_login)


@auth_router.get(
    "/profile/{user_id}",
    summary="Get user profile",
    description="Get user profile from registered_users table"
)
async def get_user_profile(user_id: str):
    """Get user profile from registered_users table."""
    try:
        from .auth_service import get_user_profile
        
        user_profile = await get_user_profile(user_id)
        if not user_profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found"
            )
        
        return user_profile
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error while retrieving user profile: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while retrieving user profile"
        )

@auth_router.get(
    "/me",
    summary="Get current user info",
    description="Protected endpoint to get current authenticated user information"
)
async def me(request: Request):
    """Get current authenticated user information."""
    try:
        # Extract Authorization header
        authorization: str = request.headers.get("Authorization")

        if not authorization:
            logger.warning("Protected endpoint accessed without authorization header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization token"
            )

        # Remove "Bearer " prefix
        token = authorization.replace("Bearer ", "").strip()

        # Call Supabase Auth API to validate the token and fetch user
        resp = requests.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": settings.supabase_service_role_key
            },
        )

        if resp.status_code != 200:
            logger.warning(f"Invalid token provided: {resp.status_code} {resp.text}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )

        user_data = resp.json()
        logger.info(f"User info retrieved successfully for user ID: {user_data.get('id')}")
        return user_data

    except HTTPException:
        # re-raise HTTP errors without wrapping
        raise
    except Exception as e:
        logger.error(f"Unexpected error while retrieving user info: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while retrieving user information"
        )
