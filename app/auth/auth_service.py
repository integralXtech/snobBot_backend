"""Authentication service using Supabase."""

from typing import Dict, Any, Optional
import logging
from app.supabase import get_supabase_client, get_admin_supabase_client
from app.core.config import settings
from .models import RegisterRequest, LoginRequest, UserResponse
from app.helpers.response_helper import success_response, error_response
from app.helpers.supabase_helper import handle_supabase_error
from app.supabase.supabase_client import get_admin_supabase_client, get_supabase_client

logger = logging.getLogger(__name__)


async def ensure_user_in_database(user_data: Dict[str, Any]) -> Dict[str, Any]:
    supabase = get_admin_supabase_client()
    try:
        response = (
            supabase.table('registered_users')
            .select('id, email')
            .eq('email', user_data['email'])
            .execute()
        )

        # Check for actual Supabase errors first
        if hasattr(response, "error") and response.error:
            return error_response(
                message=response.error.message,
                code="SUPABASE_ERROR"
            )

        # Debug logging
        logger.info(f"Database check for email {user_data['email']}: found {len(response.data) if response.data else 0} users")
        if response.data:
            logger.info(f"Existing user data: {response.data[0]}")
        
        # Handle empty results (new user) vs existing user
        if not response.data:
            user_to_insert = {
                'id': user_data['id'],
                'email': user_data['email'],
                'name': user_data['name'],
                'approved': user_data.get('approved', True)
            }

            insert_response = supabase.table('registered_users').insert([user_to_insert]).execute()
            
            # Check for insert errors
            if hasattr(insert_response, "error") and insert_response.error:
                return error_response(
                    message=insert_response.error.message,
                    code="INSERT_ERROR"
                )
            
            if not insert_response.data:
                return error_response(
                    message="Failed to insert user",
                    code="INSERT_NO_DATA"
                )

            return success_response(
                "User inserted successfully",
                {"user": insert_response.data[0], "inserted": True}
            )

        return success_response(
            "User already exists",
            {"user": response.data[0], "inserted": False}
        )

    except Exception as e:
        logger.error(f"Error ensuring user in database: {str(e)}")
        return error_response(str(e), code="DB_ERROR")


async def register_user(register_data: RegisterRequest) -> Dict[str, Any]:
    supabase = get_supabase_client()
    try:
        # Check BOTH Supabase Auth AND your database to prevent duplicate registrations
        supabase_admin = get_admin_supabase_client()
        
        # Check your custom table by EMAIL only
        existing_user = supabase_admin.table('registered_users').select('email').eq('email', register_data.email).execute()
        
        # Check Supabase Auth users table by EMAIL only
        auth_users = supabase_admin.auth.admin.list_users()
        
        # Check if email exists in either place
        email_exists_in_db = existing_user.data and len(existing_user.data) > 0
        email_exists_in_auth = any(user.email == register_data.email for user in auth_users)
        
        if email_exists_in_db or email_exists_in_auth:
            return error_response(
                "User with this email already exists. Please log in or reset your password.",
                code="USER_EXISTS"
            )
        
        # Step 1: Sign up user in Supabase Auth
        auth_response = supabase.auth.sign_up({
            'email': register_data.email,
            'password': register_data.password,
            'options': {'data': {'name': register_data.name}}
        })

        # ✅ Check for explicit auth errors first
        if hasattr(auth_response, 'error') and auth_response.error:
            error_message = str(auth_response.error.message).lower()
            if any(phrase in error_message for phrase in [
                "already registered", "already exists", "duplicate", "user already", "email already"
            ]):
                return error_response(
                    "User with this email already exists. Please log in or reset your password.",
                    code="USER_EXISTS"
                )
            return error_response(
                f"Signup failed: {auth_response.error.message}",
                code="AUTH_SIGNUP_FAILED"
            )

        # ✅ Then handle the case where no user object was returned
        if not auth_response.user or not getattr(auth_response.user, "id", None):
            return error_response(
                "User with this email already exists. Please log in or reset your password.",
                code="USER_EXISTS"
            )

        # Step 3: Insert user into database (regardless of email confirmation status)
        user_id = auth_response.user.id
        email_confirmed = getattr(auth_response.user, "email_confirmed_at", None)

        user_result = await ensure_user_in_database({
            'id': user_id,
            'email': register_data.email,
            'name': register_data.name,
            'approved': True
        })
        if not user_result["success"]:
            return user_result

        # Step 4: Return standard API response
        return {
            "success": True,
            "message": "Please confirm your email to complete signup",
            "error": None,
            "user": {
                "id": user_id,
                "email": register_data.email,
                "name": register_data.name,
                "approved": True
            }
        }

    except Exception as e:
        error_msg = str(e).lower()
        if any(x in error_msg for x in [
            "already registered", "already exists", "duplicate", "user already"
        ]):
            return error_response(
                "User with this email already exists. Please log in or reset your password.",
                code="USER_EXISTS"
            )
        return error_response(str(e), code="REGISTER_ERROR")

# ---------- services/auth_service.py ----------
async def login_user(login_data: LoginRequest) -> Dict[str, Any]:
    try:
        supabase = get_supabase_client()

        # Step 1: Authenticate with Supabase Auth
        auth_response = supabase.auth.sign_in_with_password({
            "email": login_data.email,
            "password": login_data.password
        })

        # Debugging (optional):
        # print("Auth response:", auth_response)

        # Step 2: Check if login worked
        if not auth_response or not auth_response.user:
            return {
                "success": False,
                "message": "Invalid email or password",
                "error": "INVALID_CREDENTIALS",
                "user": None
            }

        user_id = auth_response.user.id

        # Step 3: Fetch user profile with admin client (bypass RLS)
        admin_supabase = get_admin_supabase_client()
        user_result = (
            admin_supabase.table("registered_users")
            .select("*")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )

        if getattr(user_result, "error", None) or not user_result.data:
            return {
                "success": False,
                "message": "Failed to fetch user profile",
                "error": "DB_ERROR",
                "user": None
            }

        # Step 4: Success response
        db_user = user_result.data
        user_dict = {
            "id": user_id,
            "email": auth_response.user.email,
            "name": db_user.get("name"),
            "approved": db_user.get("approved", True),  # ✅ fill required field
            "created_at": db_user.get("created_at"),    # optional
            "access_token": getattr(auth_response.session, "access_token", None),
            "refresh_token": getattr(auth_response.session, "refresh_token", None)
        }

        return {
            "success": True,
            "message": "Login successful",
            "error": None,
            "user": user_dict
        }

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return {
            "success": False,
            "message": "Login error",
            "error": str(e),
            "user": None
        }

async def reset_user_password(email: str) -> Dict[str, Any]:
    supabase = get_supabase_client()
    try:
        supabase.auth.reset_password_for_email(  
            email,
            {"redirect_to": f"{settings.frontend_url}/reset-password"}
        )
        # keep it minimal: route owns the response
        return {"error": None}

    except Exception as e:
        logger.error(f"Password reset error: {str(e)}")
        return {"error": str(e)}


async def get_user_profile(user_id: str) -> Optional[UserResponse]:
    supabase = get_admin_supabase_client()
    try:
        response = (
            supabase.table('registered_users')
            .select('*')
            .eq('id', user_id)
            .single()
            .execute()
        )

        result = handle_supabase_error(response, default_error="User not found")
        if not result["success"] or not response.data:
            return None

        return UserResponse(**response.data)

    except Exception as e:
        logger.error(f"Error fetching user profile: {str(e)}")
        return None


# async def update_user_password(access_token: str, refresh_token: str, new_password: str) -> Dict[str, Any]:
#     supabase = get_supabase_client()
#     try:
#         response = supabase.auth.update_user(
#             {'password': new_password},
#             {'access_token': access_token, 'refresh_token': refresh_token}
#         )

#         result = handle_supabase_error(response, default_error="Failed to update password")
#         if not result["success"] or not getattr(response, "user", None):
#             return error_response("Failed to update password", code="PASSWORD_UPDATE_FAILED")

#         return success_response("Password updated successfully")

#     except Exception as e:
#         logger.error(f"Password update error: {str(e)}")
#         return error_response(str(e), code="PASSWORD_UPDATE_ERROR")

async def update_user_password(access_token: str, refresh_token: str, new_password: str) -> Dict[str, Any]:
    supabase = get_supabase_client()
    try:
        # 1. Set the session using tokens from reset email
        session = supabase.auth.set_session(
            access_token=access_token,
            refresh_token=refresh_token
        )
        if not session or not session.user:
            return error_response("Invalid or expired tokens", code="INVALID_SESSION")

        # 2. Update the password for that session’s user
        response = supabase.auth.update_user({"password": new_password})

        result = handle_supabase_error(response, default_error="Failed to update password")
        if not result["success"] or not response.user:
            return error_response("Failed to update password", code="PASSWORD_UPDATE_FAILED")

        return success_response("Password updated successfully")

    except Exception as e:
        logger.error(f"Password update error: {str(e)}")
        return error_response(str(e), code="PASSWORD_UPDATE_ERROR")