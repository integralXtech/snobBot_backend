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
                'approved': user_data.get('approved', True),
                'agency_id': user_data.get('agency_id'),
                'user_type': user_data.get('user_type')
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

            new_user = insert_response.data[0]

            # NEW: Assign default agency plan if user is a customer of an agency
            if new_user.get('agency_id') and new_user.get('user_type') == 'user':
                try:
                    # 1. Find a default plan for this agency (e.g. cheapest monthly or containing 'Starter/Free' in name)
                    plans_res = supabase.table("agency_plans").select("*").eq("agency_id", new_user['agency_id']).eq("interval", "month").order("price").limit(1).execute()
                    if plans_res.data:
                        default_plan = plans_res.data[0]
                        # 2. Create subscription
                        from datetime import datetime, timedelta
                        expiry = datetime.now() + timedelta(days=30)
                        sub_data = {
                            "agency_id": new_user['agency_id'],
                            "user_id": new_user['id'],
                            "plan_id": default_plan['id'],
                            "status": "active",
                            "current_period_end": expiry.isoformat()
                        }
                        supabase.table("agency_subscriptions").insert(sub_data).execute()
                        logger.info(f"Assigned default plan {default_plan['name']} to new customer {new_user['email']}")
                except Exception as sub_err:
                    logger.error(f"Failed to assign default agency plan: {str(sub_err)}")
                    # Don't fail the whole registration if sub assignment fails

            return success_response(
                data={"user": new_user, "inserted": True},
                message="User inserted successfully"
            )

        # User exists: Check if we need to force-update fields (fixing the Trigger Race Condition)
        existing_user = response.data[0]
        updates = {}
        
        # 1. Check User Type
        incoming_type = user_data.get('user_type')
        if incoming_type and existing_user.get('user_type') != incoming_type:
            updates['user_type'] = incoming_type
            
        # 2. Check Agency ID
        incoming_agency = user_data.get('agency_id')
        if incoming_agency and existing_user.get('agency_id') != incoming_agency:
            updates['agency_id'] = incoming_agency
            
        if updates:
            logger.info(f"Force-updating user {user_data['email']} with: {updates}")
            update_res = supabase.table('registered_users').update(updates).eq('id', existing_user['id']).execute()
            if hasattr(update_res, "data") and update_res.data:
                existing_user.update(updates)

        return success_response(
            data={"user": existing_user, "inserted": False},
            message="User already exists"
        )

    except Exception as e:
        logger.error(f"Error ensuring user in database: {str(e)}")
        return error_response(str(e), code="DB_ERROR")


async def register_user(register_data: RegisterRequest) -> Dict[str, Any]:
    try:
        logger.info(f"Registering user: {register_data.email}, User Type: {register_data.user_type}")
        supabase_admin = get_admin_supabase_client()
        
        # Step 1: Attempt to create user with auto-confirmation
        user_id = None
        try:
            # Fix: Supabase-py v2 Admin API expects a single dictionary of attributes
            auth_response = supabase_admin.auth.admin.create_user({
                "email": register_data.email,
                "password": register_data.password,
                "user_metadata": {"name": register_data.name},
                "email_confirm": True
            })
            user_id = auth_response.id if hasattr(auth_response, 'id') else auth_response.user.id
            
            # EXPLICIT CONFIRMATION (Robustness)
            try:
                supabase_admin.auth.admin.update_user_by_id(
                    user_id, 
                    attributes={"email_confirm": True}
                )
                logger.info(f"Explicitly confirmed new user {register_data.email}")
            except Exception as confirm_err:
                logger.warning(f"Failed explicit confirm for new user (creation might have handled it): {confirm_err}")

            logger.info(f"User {register_data.email} created via Admin API with id {user_id}")
        except Exception as auth_error:
            error_msg = str(auth_error).lower()
            if any(phrase in error_msg for phrase in ["already registered", "already exists", "duplicate", "email already"]):
                logger.info(f"User {register_data.email} already exists in Auth. Ensuring confirmation.")
                
                # If user exists, force-confirm them
                existing_users = supabase_admin.auth.admin.list_users()
                target_user = next((u for u in existing_users if u.email == register_data.email), None)
                
                if target_user:
                    user_id = target_user.id
                    try:
                        # Use dict for metadata and attributes
                        supabase_admin.auth.admin.update_user_by_id(
                            user_id, 
                            {"email_confirm": True}
                        )
                        logger.info(f"Force-confirmed existing user {register_data.email}")
                    except Exception as confirm_err:
                        logger.warning(f"Failed to force-confirm existing user: {confirm_err}")
                else:
                    return error_response(
                        "User with this email already exists. Please log in.",
                        code="USER_EXISTS"
                    )
            else:
                logger.error(f"Auth signup failed: {auth_error}")
                return error_response(f"Signup failed: {str(auth_error)}", code="AUTH_SIGNUP_FAILED")

        # Step 2: Ensure user is in our custom database table
        if user_id:
            user_result = await ensure_user_in_database({
                'id': user_id,
                'email': register_data.email,
                'name': register_data.name,
                'approved': True,
                'agency_id': register_data.agency_id,
                'user_type': register_data.user_type
            })
            if not user_result["success"]:
                return user_result

            return {
                "success": True,
                "message": "User registered and confirmed successfully. You can now log in.",
                "user": {
                    "id": user_id,
                    "email": register_data.email,
                    "name": register_data.name,
                    "approved": True,
                    "user_type": register_data.user_type
                }
            }
        
        return error_response("Unknown registration error", code="REGISTER_ERROR")

    except Exception as e:
        logger.error(f"Unexpected error during registration: {str(e)}")
        return error_response("Registration failed. Please try again later.", code="REGISTER_ERROR")


async def login_user(login_data: LoginRequest) -> Dict[str, Any]:
    try:
        supabase = get_supabase_client()

        # Step 1: Authenticate with Supabase Auth
        try:
            auth_response = supabase.auth.sign_in_with_password({
                "email": login_data.email,
                "password": login_data.password
            })
        except Exception as auth_err:
            error_msg = str(auth_err).lower()
            # If email is not confirmed, fix it via Admin API and retry once
            if any(phrase in error_msg for phrase in ["email not confirmed", "confirmation", "verify your email"]):
                logger.info(f"User {login_data.email} has unconfirmed email. Attempting auto-confirmation...")
                admin_supabase = get_admin_supabase_client()
                
                # Fetch all users to find the ID (Admin API list_users is safer here)
                users = admin_supabase.auth.admin.list_users()
                target_user = next((u for u in users if u.email.lower() == login_data.email.lower()), None)
                
                if target_user:
                    try:
                        admin_supabase.auth.admin.update_user_by_id(
                            target_user.id, 
                            {"email_confirm": True}
                        )
                        logger.info(f"Successfully auto-confirmed {login_data.email} during login flow.")
                        
                        # RETRY LOGIN
                        auth_response = supabase.auth.sign_in_with_password({
                            "email": login_data.email,
                            "password": login_data.password
                        })
                    except Exception as retry_err:
                        logger.error(f"Failed to login after auto-confirmation: {retry_err}")
                        return {
                            "success": False,
                            "message": "Invalid email or password",
                            "error": str(retry_err),
                            "user": None
                        }
                else:
                    return {
                        "success": False,
                        "message": "Invalid email or password",
                        "error": str(auth_err),
                        "user": None
                    }
            else:
                return {
                    "success": False,
                    "message": "Invalid email or password",
                    "error": str(auth_err),
                    "user": None
                }

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
            # Maybe the user is in Auth but not in our table? Try to fix that too.
            user_fix = await ensure_user_in_database({
                'id': user_id,
                'email': auth_response.user.email,
                'name': auth_response.user.user_metadata.get('name', auth_response.user.email),
                'approved': True
            })
            if user_fix["success"]:
                db_user = user_fix["data"]["user"]
            else:
                return {
                    "success": False,
                    "message": "Failed to fetch or create user profile",
                    "error": "DB_ERROR",
                    "user": None
                }
        else:
            db_user = user_result.data

        # Step 4: Success response
        user_dict = {
            "id": user_id,
            "email": auth_response.user.email,
            "name": db_user.get("name"),
            "approved": db_user.get("approved", True),
            "user_type": db_user.get("user_type"),
            "created_at": db_user.get("created_at"),
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
        error_msg = str(e).lower()
        # Ensure no confirmation/verification error ever reaches the user
        if any(phrase in error_msg for phrase in ["confirm", "verify", "not confirmed"]):
            message = "Invalid email or password"
        else:
            message = str(e)
            
        return {
            "success": False,
            "message": message,
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