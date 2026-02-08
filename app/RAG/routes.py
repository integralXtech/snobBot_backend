import sys
import asyncio
import os
import logging
import certifi
from playwright.async_api import async_playwright

# Define logger
logger = logging.getLogger(__name__)

# Fix for asyncio NotImplementedError on Windows when using subprocesses (Playwright)
if sys.platform == 'win32':
    try:
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Header, Body
from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import secrets
import string

from app.RAG.rag_helper import generate_response
from app.RAG.pdf_processor import process_and_index_data
from app.RAG.auth_utils import get_current_user, validate_api_key, get_api_key
from app.RAG.link_finder import get_internal_links
from app.helpers.db_helper import (
    create_conversation, 
    add_message, 
    get_chatbot_conversations, 
    get_conversation_messages
)
from app.RAG.enums import Theme, Position
from app.RAG.token_tracker import update_tokens, get_user_total_tokens
from app.helpers.agency_helper import (
    check_usage_limits, 
    track_and_log_usage, 
    validate_api_key_v2
)

_browser = None
_playwright = None


from app.payments import stripe_service

rag_router = APIRouter(prefix="/rag", tags=["RAG"])
os.environ["SSL_CERT_FILE"] = certifi.where()

@rag_router.get("/settings/billing")
async def get_user_billing_settings(
    user_id: str = Header(..., description="User ID"),
    email: Optional[str] = Header(None, description="User Email")
):
    """Get user's billing/auto-recharge settings."""
    try:
        settings = await stripe_service.get_billing_preferences(user_id, email)
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@rag_router.post("/settings/billing")
async def save_user_billing_settings(
    settings: Dict = Body(...),
    user_id: str = Header(..., description="User ID"),
    email: Optional[str] = Header(None, description="User Email")
):
    """Save user's billing/auto-recharge settings."""
    try:
        success = await stripe_service.save_billing_preferences(user_id, email, settings)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save settings")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ------------------ MODELS ------------------ #

class CreateChatbotRequest(BaseModel):
    chatbot_title: str



class CreateChatbotRequest(BaseModel):
    chatbot_title: str
    category: str = Field(..., min_length=1, max_length=100)
    language: Optional[str] = None
    description: Optional[str] = None


class UpdateChatbotRequest(BaseModel):
    chatbot_title: str
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None


class PreviewUpgradeRequest(BaseModel):
    plan_id: str


@rag_router.post("/payments/preview-upgrade")
async def preview_upgrade(
    body: PreviewUpgradeRequest,
    current_user: dict = Depends(get_current_user)
):
    """Preview upgrade proration costs."""
    try:
        user_id = current_user["id"]
        preview = await stripe_service.preview_upgrade_proration(user_id, body.plan_id)
        return preview
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AppearanceRequest(BaseModel):
    chatbot_title: str
    bot_title: Optional[str] = None
    theme: Optional[Theme] = None
    primary_color_rgb: Optional[str] = Field(None, pattern=r'^rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)$|^#[0-9A-Fa-f]{6}$')
    border_radius_px: Optional[int] = Field(None, ge=0, le=50)
    position: Optional[Position] = None


class AppearanceResponse(BaseModel):
    id: str
    user_id: str
    chatbot_title: str
    bot_avatar_url: Optional[str]
    theme: Optional[str]
    primary_color_rgb: Optional[str]
    border_radius_px: Optional[int]
    position: Optional[str]
    height: Optional[int]
    width: Optional[int]
    created_at: str
    updated_at: str

class QueryRequest(BaseModel):
    query: str
    api_key: str
    conversation_id: Optional[str] = None

class ApiKeyRequest(BaseModel):
    api_key: str

class QAPair(BaseModel):
    question: str
    answer: str

class RawTextRequest(BaseModel):
    chatbot_title: str
    raw_text: str

class QARequest(BaseModel):
    chatbot_title: str
    qa_pairs: List[QAPair]

class FileRequest(BaseModel):
    chatbot_title: str
    filename: str
    file_bytes: str
    
class DiscoverRequest(BaseModel):
    url: str

class FetchRequest(BaseModel):
    base_url: str
    endpoint: str
    chatbot_title: str
    
class FlushRequest(BaseModel):
    chatbot_title: str
    
    



# ------------------ CREATE CHATBOT ------------------ #

@rag_router.post("/create-chatbot")
async def create_chatbot_api(
    chatbot_title: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    avatar: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """Create (or return existing) API key for a chatbot with category and description."""
    user_id = current_user["id"]
    chatbot_title_lower = chatbot_title.lower()

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        # Check if chatbot config exists
        existing_config = (
            supabase.table("chatbot_configs")
            .select("api_key, category, description")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title_lower)
            .execute()
        )

        if existing_config.data:
            # If config exists, fetch appearance data and return
            appearance_data = (
                supabase.table("chatbot_appearance")
                .select("language, bot_avatar_url")
                .eq("user_id", user_id)
                .eq("chatbot_title", chatbot_title_lower)
                .execute()
            )
            
            appearance = appearance_data.data[0] if appearance_data.data else {}

            return {
                "api_key": existing_config.data[0]["api_key"],
                "message": "API key already exists",
                "category": existing_config.data[0].get("category"),
                "description": existing_config.data[0].get("description"),
                "language": appearance.get("language"),
                "bot_avatar_url": appearance.get("bot_avatar_url"),
            }

        # Agencies Feature: Check Agency-Level limits and handle agency assignment
        from app.helpers.agency_helper import get_user_agency_info
        agency_info = get_user_agency_info(user_id)
        agency_id = agency_info["id"] if agency_info else None
        
        if agency_id:
            # Check agency-level chatbot limit (Soft limit with overage)
            pool = agency_info.get("agency_pools")
            if pool and pool[0]["current_chatbots"] >= pool[0]["limit_chatbots"]:
                # We track overage but allow creation (per requirements)
                pass

        # Check if user already has 5 bots (Original Hard Limit - can be customized later per agency plan)
        all_user_bots = (
            supabase.table("chatbot_configs")
            .select("chatbot_title")
            .eq("user_id", user_id)
            .execute()
        )

        current_bot_count = len(all_user_bots.data)
        if current_bot_count >= 5:
            raise HTTPException(
                status_code=403,
                detail=f"You already have {current_bot_count} chatbots. Maximum limit is 5. Please delete a chatbot before creating a new one."
            )

        api_key = "snb_" + "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(32)
        )

        # Handle avatar upload
        bot_avatar_url = None
        if avatar:
            if not avatar.content_type.startswith('image/'):
                raise HTTPException(status_code=400, detail="Avatar must be an image file")
            
            file_content = await avatar.read()
            if len(file_content) > 2 * 1024 * 1024:  # 2MB limit
                raise HTTPException(status_code=400, detail="Avatar file too large. Maximum size is 2MB.")
            
            import base64
            file_extension = avatar.filename.split('.')[-1] if '.' in avatar.filename else 'png'
            base64_data = base64.b64encode(file_content).decode('utf-8')
            bot_avatar_url = f"data:image/{file_extension};base64,{base64_data}"

        # Insert into chatbot_configs
        config_data = {
            "user_id": user_id,
            "chatbot_title": chatbot_title_lower,
            "api_key": api_key,
            "is_active": True,
            "category": category,
            "description": description,
            "agency_id": agency_id # Assign agency_id
        }
        supabase.table("chatbot_configs").insert(config_data).execute()
        
        # Increment Agency Pool chatbot count
        if agency_id:
            supabase.rpc("increment_agency_chatbots", {"agency_id_param": agency_id}).execute()

        # Insert into chatbot_appearance
        appearance_data = {
            "user_id": user_id,
            "chatbot_title": chatbot_title_lower,
            "language": language,
            "bot_avatar_url": bot_avatar_url,
        }
        supabase.table("chatbot_appearance").insert(appearance_data).execute()

        return {
            "api_key": api_key,
            "message": "API key created successfully",
            "category": category,
            "description": description,
            "language": language,
            "bot_avatar_url": bot_avatar_url,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"API key creation failed: {str(e)}")


@rag_router.put("/update-chatbot")
def update_chatbot_api(
    request: UpdateChatbotRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update chatbot category and description."""
    user_id = current_user["id"]
    chatbot_title = request.chatbot_title.lower()

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        # Check if chatbot exists and get current data
        existing = (
            supabase.table("chatbot_configs")
            .select("id, category, description, api_key")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Chatbot not found")

        # Prepare update data - only include fields that are provided
        update_data = {}
        if request.category is not None:
            update_data["category"] = request.category
        if request.description is not None:
            update_data["description"] = request.description

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Update the chatbot
        result = (
            supabase.table("chatbot_configs")
            .update(update_data)
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        # Get updated data
        updated = (
            supabase.table("chatbot_configs")
            .select("category, description, api_key")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        return {
            "message": "Chatbot updated successfully",
            "api_key": updated.data[0]["api_key"],
            "category": updated.data[0]["category"],
            "description": updated.data[0]["description"],
            "updated_fields": list(update_data.keys())
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chatbot update failed: {str(e)}")


# ------------------ APPEARANCE MANAGEMENT ------------------ #
# @rag_router.post("/create-appearance")
# async def create_appearance(
#     chatbot_title: str = Form(...),
#     theme: Optional[Theme] = Form(None),
#     primary_color_rgb: Optional[str] = Form(None),
#     border_radius_px: Optional[int] = Form(None),
#     position: Optional[Position] = Form(None),
#     current_user: dict = Depends(get_current_user),
# ):
#     """Sets non-avatar appearance settings. If settings exist, they are updated. If not, they are created."""
#     user_id = current_user["id"]
#     chatbot_title = chatbot_title.lower()
#
#     try:
#         from app.supabase import get_admin_supabase_client
#         supabase = get_admin_supabase_client()
#
#         # Check if chatbot config exists first
#         chatbot_exists = supabase.table("chatbot_configs").select("id").eq("user_id", user_id).eq("chatbot_title", chatbot_title).execute()
#         if not chatbot_exists.data:
#             raise HTTPException(status_code=404, detail="Chatbot not found")
#
#         # Prepare data for update/insert
#         update_data = {}
#         if theme is not None:
#             update_data["theme"] = theme.value
#         if primary_color_rgb is not None:
#             update_data["primary_color_rgb"] = primary_color_rgb
#         if border_radius_px is not None:
#             update_data["border_radius_px"] = border_radius_px
#         if position is not None:
#             update_data["position"] = position.value
#
#         if not update_data:
#             raise HTTPException(status_code=400, detail="No fields to update")
#
#         # Check if appearance record exists
#         existing_appearance = supabase.table("chatbot_appearance").select("id").eq("user_id", user_id).eq("chatbot_title", chatbot_title).execute()
#
#         if existing_appearance.data:
#             # Update existing appearance record
#             (supabase.table("chatbot_appearance").update(update_data).eq("user_id", user_id).eq("chatbot_title", chatbot_title).execute())
#             message = "Appearance settings updated successfully."
#         else:
#             # This case is for older bots made before the logic change
#             update_data["user_id"] = user_id
#             update_data["chatbot_title"] = chatbot_title
#             (supabase.table("chatbot_appearance").insert(update_data).execute())
#             message = "Appearance settings created successfully."
#
#         return {
#             "message": message,
#             "updated_fields": list(update_data.keys())
#         }
#
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Appearance update failed: {str(e)}")


@rag_router.put("/update-appearance")
async def update_appearance(
    chatbot_title: str = Form(...),
    avatar: Optional[UploadFile] = File(None),
    theme: Optional[Theme] = Form(None),
    primary_color_rgb: Optional[str] = Form(None),
    border_radius_px: Optional[int] = Form(None),
    position: Optional[Position] = Form(None),
    language: Optional[str] = Form(None),
    height: Optional[int] = Form(None),
    width: Optional[int] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Update chatbot appearance settings."""
    user_id = current_user["id"]
    chatbot_title = chatbot_title.lower()

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        # Check if chatbot exists
        chatbot_exists = (
            supabase.table("chatbot_configs")
            .select("id")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        if not chatbot_exists.data:
            raise HTTPException(status_code=404, detail="Chatbot not found")

        # Check if appearance exists
        existing = (
            supabase.table("chatbot_appearance")
            .select("id")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Appearance settings not found. Use create-appearance first.")

        # Handle avatar upload if provided
        bot_avatar_url = None
        if avatar:
            # Validate file type
            if not avatar.content_type.startswith('image/'):
                raise HTTPException(status_code=400, detail="Avatar must be an image file")
            
            # Validate file size (max 2MB)
            file_content = await avatar.read()
            if len(file_content) > 2 * 1024 * 1024:  # 2MB limit
                raise HTTPException(status_code=400, detail="Avatar file too large. Maximum size is 2MB.")
            
            # Convert to base64 and store in database
            import base64
            file_extension = avatar.filename.split('.')[-1] if '.' in avatar.filename else 'png'
            base64_data = base64.b64encode(file_content).decode('utf-8')
            bot_avatar_url = f"data:image/{file_extension};base64,{base64_data}"

        # Prepare update data - only include fields that are provided
        update_data = {}
        
        if bot_avatar_url is not None:
            update_data["bot_avatar_url"] = bot_avatar_url
        if theme is not None:
            update_data["theme"] = theme.value
        if primary_color_rgb is not None:
            update_data["primary_color_rgb"] = primary_color_rgb
        if border_radius_px is not None:
            update_data["border_radius_px"] = border_radius_px
        if position is not None:
            update_data["position"] = position.value
        if language is not None:
            normalized_language = language.strip()
            if not normalized_language:
                raise HTTPException(status_code=400, detail="Language cannot be empty if provided")
            update_data["language"] = normalized_language
        if height is not None:
             update_data["height"] = height
        if width is not None:
             update_data["width"] = width

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Update appearance
        result = (
            supabase.table("chatbot_appearance")
            .update(update_data)
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        return {
            "message": "Appearance updated successfully",
            "updated_fields": list(update_data.keys())
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Appearance update failed: {str(e)}")


# @rag_router.get("/appearance/{chatbot_title}")
# def get_appearance(
#     chatbot_title: str,
#     current_user: dict = Depends(get_current_user),
# ):
#     """Get current chatbot appearance settings."""
#     user_id = current_user["id"]
#     chatbot_title = chatbot_title.lower()

#     try:
#         from app.supabase import get_admin_supabase_client
#         supabase = get_admin_supabase_client()

#         result = (
#             supabase.table("chatbot_appearance")
#             .select("*")
#             .eq("user_id", user_id)
#             .eq("chatbot_title", chatbot_title)
#             .execute()
#         )

#         if not result.data:
#             # Return default values if no appearance settings exist
#             return {
#                 "chatbot_title": chatbot_title,
#                 "bot_avatar_url": None,
#                 "theme": None,
#                 "primary_color_rgb": None,
#                 "border_radius_px": None,
#                 "position": None,
#                 "message": "No appearance settings found - using defaults"
#             }

#         return AppearanceResponse(**result.data[0])

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to fetch appearance: {str(e)}")


@rag_router.post("/get-appearance")
def get_appearance_public(request: ApiKeyRequest):
    """Get chatbot appearance settings using API key (no authentication required)."""
    api_data = validate_api_key(request.api_key)
    if not api_data:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    user_id = api_data["user_id"]
    chatbot_title = api_data["chatbot_title"].lower()

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        result = (
            supabase.table("chatbot_appearance")
            .select("*")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )

        if not result.data:
            # Return default values if no appearance settings exist
            return {
                "chatbot_title": chatbot_title,
                "bot_avatar_url": None,
                "theme": None,
                "primary_color_rgb": None,
                "border_radius_px": None,
                "position": None,
                "message": "No appearance settings found - using defaults"
            }

        # Return all fields from the table
        return result.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch appearance: {str(e)}")


@rag_router.get("/agency/config")
async def get_agency_config(domain: str):
    """Public endpoint to get agency branding by domain."""
    from app.helpers.agency_helper import get_agency_config_by_domain
    agency = get_agency_config_by_domain(domain)
    if not agency:
        return {"agency": None}
    
    return {
        "agency": {
            "name": agency["name"],
            "logo_url": agency["logo_url"],
            "primary_color": agency["primary_color"],
            "secondary_color": agency["secondary_color"],
            "custom_domain": agency["custom_domain"]
        }
    }


# ------------------ DOCS SEPARATED ------------------ #
# DEPRECATED: These endpoints have been replaced by S3 upload endpoints
# /rag/docs/file → /s3/upload/file
# /rag/docs/raw → /s3/upload/raw
# /rag/docs/qa → /s3/upload/qa

# @rag_router.post("/docs/file")
# def docs_file(
#     file: UploadFile = File(...),
#     chatbot_title: str = Form(...),
#     current_user: dict = Depends(get_current_user),
# ):
#     """Upload a document file (.pdf/.docx/.txt) and index it into the chatbot."""
#     user_id = current_user["id"]
#
#     chatbot_title = chatbot_title.lower()
#     api_key = get_api_key(user_id, chatbot_title)
#     if not api_key:
#         raise HTTPException(
#             status_code=403,
#             detail=f"No active API key found for chatbot '{chatbot_title}'"
#         )
#
#     if not file.filename.lower().endswith((".pdf", ".docx", ".txt")):
#         raise HTTPException(
#             status_code=400, detail="Only .pdf, .docx, and .txt files are supported"
#         )
#
#     file_bytes = file.file.read()
#     filename = file.filename
#
#     result = process_and_index_data(
#         user_id=user_id,
#         filename=filename,
#         file_bytes=file_bytes,
#         chatbot_title=chatbot_title,
#     )
#
#     # Save tokens to database (we already have the value from result)
#     update_tokens(
#         user_id=user_id,
#         chatbot_title=chatbot_title,
#         operation_type="file_upload",
#         tokens_used=result["tokens_used"]
#     )
#
#     return {
#         "message": f"File '{filename}' processed successfully",
#         "chunks_indexed": result["chunks_indexed"],
#         "tokens_used": result["tokens_used"],
#         "api_key": api_key,
#     }


# @rag_router.post("/docs/raw")
# def upload_raw_text(request: RawTextRequest, current_user: dict = Depends(get_current_user)):
#     """Upload and index raw text input."""
#     user_id = current_user["id"]
#     chatbot_title = request.chatbot_title.lower()
#
#     api_key = get_api_key(user_id, chatbot_title)
#     if not api_key:
#         raise HTTPException(status_code=403, detail=f"No active API key found for chatbot '{chatbot_title}'")
#
#     result = process_and_index_data(
#         user_id=user_id,
#         raw_text=request.raw_text,
#         chatbot_title=chatbot_title,
#     )
#
#     # Save tokens to database (we already have the value from result)
#     update_tokens(
#         user_id=user_id,
#         chatbot_title=chatbot_title,
#         operation_type="raw_text",
#         tokens_used=result["tokens_used"]
#     )
#
#     return result


# @rag_router.post("/docs/qa")
# def upload_qa_pairs(request: QARequest, current_user: dict = Depends(get_current_user)):
#     """Upload and index QA pairs."""
#     user_id = current_user["id"]
#     chatbot_title = request.chatbot_title.lower()
#
#     api_key = get_api_key(user_id, chatbot_title)
#     if not api_key:
#         raise HTTPException(status_code=403, detail=f"No active API key found for chatbot '{chatbot_title}'")
#
#     qa_data = [{"question": qa.question, "answer": qa.answer} for qa in request.qa_pairs]
#
#     result = process_and_index_data(
#         user_id=user_id,
#         qa_json=qa_data,
#         chatbot_title=chatbot_title,
#     )
#
#     # Save tokens to database (we already have the value from result)
#     update_tokens(
#         user_id=user_id,
#         chatbot_title=chatbot_title,
#         operation_type="qa_pairs",
#         tokens_used=result["tokens_used"]
#     )
#
#     return result


# ------------------ WEB CRAWLING ------------------ #

from playwright_stealth import Stealth

@rag_router.post("/crawl/discover")
async def discover_links(request: DiscoverRequest, current_user: dict = Depends(get_current_user)):
    """
    Discover all internal endpoints from the given website.
    Uses internal Playwright crawler first, falls back to Spider.cloud if it fails.
    Stores all discovered links in Supabase.
    """
    if not current_user or "id" not in current_user:
        raise HTTPException(status_code=401, detail="Invalid or unauthorized user")

    user_id = current_user["id"]
    base_url = request.url
    discovered_links = []
    discovery_method = "internal"
    error_message = None

    # ============================================================
    # STEP 1: Try Internal Playwright Discovery (Primary Method)
    # ============================================================
    global _browser
    if _browser:
        try:
            page = await _browser.new_page()
            
            # Apply Stealth
            stealth = Stealth()
            await stealth.apply_stealth_async(page)
            
            # Navigate with timeout
            await page.goto(request.url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)  # brief pause for dynamic content
            
            html_content = await page.content()
            await page.close()
            
            # Extract internal links
            endpoints = get_internal_links(request.url, html_content)
            
            # Convert to full URLs
            from urllib.parse import urljoin
            discovered_links = [
                {"url": urljoin(base_url, endpoint), "title": None}
                for endpoint in endpoints
            ]
            
            print(f"✅ Internal discovery found {len(discovered_links)} links")
            
        except Exception as e:
            error_message = str(e)
            print(f"⚠️ Internal discovery failed: {error_message}")
            discovered_links = []

    # ============================================================
    # STEP 2: Fallback to Spider.cloud if Internal Failed
    # ============================================================
    if len(discovered_links) == 0:
        try:
            from app.helpers.spider_client import discover_links_spider
            
            print(f"🕷️ Falling back to Spider.cloud for {base_url}")
            spider_links = discover_links_spider(base_url, limit=0)
            
            discovered_links = [
                {"url": link, "title": None}
                for link in spider_links
            ]
            
            discovery_method = "spider"
            print(f"✅ Spider.cloud found {len(discovered_links)} links")
            
        except Exception as spider_error:
            print(f"❌ Spider.cloud also failed: {spider_error}")
            raise HTTPException(
                status_code=400,
                detail=f"Both internal and Spider.cloud discovery failed. Internal: {error_message}, Spider: {str(spider_error)}"
            )

    # ============================================================
    # STEP 3: Store Links in Supabase (if chatbot_title provided)
    # ============================================================
    # Note: We need chatbot_title to store links, but the current DiscoverRequest
    # doesn't include it. For now, we'll return the links without storing.
    # The frontend should call this with chatbot context or we modify the model.
    
    # If you want to store immediately, add chatbot_title to DiscoverRequest model
    # and uncomment below:
    # from app.helpers.db_helper import upsert_discovered_links
    # if hasattr(request, 'chatbot_title') and request.chatbot_title:
    #     upsert_discovered_links(user_id, request.chatbot_title, discovered_links)

    return {
        "base_url": base_url,
        "endpoints": [link["url"] for link in discovered_links],
        "total_found": len(discovered_links),
        "discovery_method": discovery_method
    }



@rag_router.on_event("startup")
async def startup_event():
    """Launch a single global Playwright browser asynchronously with stealth args."""
    global _playwright, _browser
    
    try:
        # Re-apply loop policy fix if needed for the current thread/loop
        if sys.platform == 'win32':
            try:
                if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                pass
                
        os.environ["SSL_CERT_FILE"] = certifi.where()
        _playwright = await async_playwright().start()
        
        # Launch with stealth arguments to avoid detection
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        print("✅ Playwright browser started globally (Stealth Mode).")
    except Exception as e:
        logger.error(f"❌ Failed to start Playwright: {e}")
        print(f"⚠️ Warning: Playwright could not start. Web scraping features will be disabled. Error: {e}")


@rag_router.on_event("shutdown")
async def shutdown_event():
    """Close the global browser on app shutdown."""
    global _playwright, _browser
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    print("🧹 Playwright browser closed.")


# DEPRECATED: Public endpoint removed - use /s3/upload/crawl instead for batch processing
async def fetch_and_index(
    request: FetchRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch a webpage using Spider.cloud Scrape API (fallback to Playwright if needed)
    → Extract markdown content
    → Batch embed + index once (optimized for performance)
    → Update is_crawled status in database
    
    NOTE: This is now an internal function used by /s3/upload/crawl.
    Use /s3/upload/crawl endpoint for crawling multiple URLs.
    """
    source_for_indexing = f"{request.base_url.rstrip('/')}{request.endpoint}"
    full_url = urljoin(request.base_url, request.endpoint)
    
    user_id = current_user["id"]
    chatbot_title = request.chatbot_title.lower()

    # ✅ Validate API key
    api_key = get_api_key(user_id, chatbot_title)
    if not api_key:
        raise HTTPException(
            status_code=403,
            detail=f"No active API key found for chatbot '{chatbot_title}'"
        )

    # ============================================================
    # STEP 1: Scrape Content using Spider.cloud (Primary Method)
    # ============================================================
    raw_text = None
    scrape_method = "spider"
    
    try:
        from app.helpers.spider_client import scrape_url_spider
        
        print(f"🕷️ Scraping {full_url} with Spider.cloud")
        scrape_result = scrape_url_spider(full_url, return_format="markdown")
        
        raw_text = scrape_result.get('content', '')
        page_title = scrape_result.get('title', '')
        
        if not raw_text or len(raw_text.strip()) < 50:
            raise Exception("Spider.cloud returned insufficient content")
            
        print(f"✅ Spider.cloud scraped {len(raw_text)} characters")
        
    except Exception as spider_error:
        print(f"⚠️ Spider.cloud scraping failed: {spider_error}")
        
        # ============================================================
        # STEP 2: Fallback to Internal Playwright Scraping
        # ============================================================
        global _browser
        if not _browser:
            raise HTTPException(status_code=500, detail="Both Spider.cloud and Playwright unavailable")
        
        try:
            print(f"🔄 Falling back to Playwright for {full_url}")
            
            os.environ["SSL_CERT_FILE"] = certifi.where()
            
            context = await _browser.new_context()
            await stealth_async(context)
            page = await context.new_page()
            
            await page.goto(full_url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_selector("body", timeout=15000)
            html_content = await page.content()
            await page.close()
            await context.close()
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            grouped_chunks = []
            current_heading = None
            current_block = []

            for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
                text = el.get_text(" ", strip=True)
                if not text:
                    continue
                if el.name in ["h1", "h2", "h3", "h4"]:
                    if current_heading or current_block:
                        grouped_chunks.append({
                            "heading": current_heading,
                            "content": " ".join(current_block).strip()
                        })
                        current_block = []
                    current_heading = text
                else:
                    current_block.append(text)

            if current_heading or current_block:
                grouped_chunks.append({
                    "heading": current_heading,
                    "content": " ".join(current_block).strip()
                })

            if not grouped_chunks:
                raise HTTPException(status_code=400, detail=f"No meaningful text found on {full_url}")

            # Combine chunks
            texts_to_index = []
            for block in grouped_chunks:
                combined_text = (
                    f"{block['heading']}\n{block['content']}" if block["heading"] else block["content"]
                )
                texts_to_index.append(combined_text)

            raw_text = "\n\n".join(texts_to_index)
            scrape_method = "playwright"
            print(f"✅ Playwright scraped {len(raw_text)} characters")
            
        except Exception as playwright_error:
            raise HTTPException(
                status_code=400,
                detail=f"Both Spider.cloud and Playwright failed. Spider: {spider_error}, Playwright: {str(playwright_error)}"
            )

    # ============================================================
    # STEP 3: Index the Content into Pinecone
    # ============================================================
    try:
        result = process_and_index_data(
            user_id=user_id,
            raw_text=raw_text,
            filename=request.endpoint.strip("/"),
            source_type=source_for_indexing,
            chatbot_title=chatbot_title,
        )

        update_tokens(
            user_id=user_id,
            chatbot_title=chatbot_title,
            operation_type="web_crawl",
            tokens_used=result["tokens_used"]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")

    # ============================================================
    # STEP 4: Mark Link as Crawled in Database
    # ============================================================
    try:
        from app.helpers.db_helper import mark_link_as_crawled
        mark_link_as_crawled(user_id, chatbot_title, full_url)
        print(f"✅ Marked {full_url} as crawled in database")
    except Exception as db_error:
        print(f"⚠️ Failed to update database: {db_error}")
        # Non-fatal, continue

    return {
        "base_url": request.base_url,
        "endpoint": request.endpoint,
        "chunks_indexed": result["chunks_indexed"],
        "tokens_used": result["tokens_used"],
        "scrape_method": scrape_method,
        "message": f"✅ Crawled and indexed successfully using {scrape_method}.",
    }

    
    
# ------------------ ASK ------------------ #

@rag_router.post("/ask")
async def ask(request: QueryRequest):
    """
    Query endpoint for chatbots.
    Implements White-Label dual-tracking (User Hard Limit + Agency Soft Limit).
    """
    # 1. Validate API Key and get Context
    context = validate_api_key_v2(request.api_key)
    if not context:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    
    user_id = context["user_id"]
    chatbot_title = context["chatbot_title"].lower()
    chatbot_id = context["chatbot_id"]
    agency_id = context["agency_id"]

    # 2. Check Quotas (User Level)
    limit_check = check_usage_limits(user_id, chatbot_id)
    if not limit_check["allowed"]:
        raise HTTPException(status_code=403, detail=limit_check["reason"])

    try:
        # Generate RAG outcome
        result = generate_response(
            query=request.query,
            user_id=user_id,
            chatbot_title=chatbot_title
        )

        # --- CONVERSATION HISTORY LOGIC ---
        conversation_id = request.conversation_id
        if not conversation_id:
            # Create a new conversation if none provided
            conv = create_conversation(user_id, chatbot_title, title=request.query[:50])
            conversation_id = conv["id"]
        
        # Add messages to history
        add_message(conversation_id, "user", request.query)
        add_message(conversation_id, "bot", result["response"])

        # 3. Track and Log Usage (Dual-Level)
        await track_and_log_usage(
            user_id=user_id,
            chatbot_id=chatbot_id,
            type="query",
            amount=1
        )

        return {
            "response": result["response"],
            "conversation_id": conversation_id,
            "tokens_used": result.get("tokens_used", 0)
        }

    except Exception as e:
        logger.error(f"Error in ask endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    



# ------------------ CONVERSATION HISTORY ENDPOINTS ------------------ #

@rag_router.get("/conversations/{chatbot_title}")
def get_conversations(
    chatbot_title: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all conversation threads for a chatbot."""
    user_id = current_user["id"]
    return get_chatbot_conversations(user_id, chatbot_title)


@rag_router.get("/conversations/history/{conversation_id}")
def get_history(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get message history for a specific conversation."""
    user_id = current_user["id"]
    return get_conversation_messages(user_id, conversation_id)


# ------------------ TOKEN TRACKING ------------------ #

@rag_router.get("/tokens")
def get_all_user_tokens(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    
    summary = get_user_total_tokens(user_id)
    
    if "error" in summary:
        raise HTTPException(status_code=500, detail=summary["error"])
    
    return summary


# ------------------ FLUSH ------------------ #

@rag_router.post("/flush")
def flush_namespace(
    request: FlushRequest,
    current_user: dict = Depends(get_current_user)
):
    """Flush all vectors for a chatbot's namespace."""
    user_id = current_user["id"]
    chatbot_title = request.chatbot_title.lower()
    namespace = chatbot_title.strip().replace(" ", "_")

    INDEX_NAME = f"snobbots-{user_id.lower().replace(' ', '_')}"

    try:
        from app.RAG.pdf_processor import pc  # reuse Pinecone client

        if INDEX_NAME not in pc.list_indexes().names():
            raise HTTPException(status_code=404, detail=f"Index '{INDEX_NAME}' not found")

        index = pc.Index(INDEX_NAME)

        # delete all vectors in namespace
        index.delete(delete_all=True, namespace=namespace)

        return {
            "message": f"Namespace '{namespace}' flushed successfully from index '{INDEX_NAME}'",
            "namespace": namespace,
            "index_name": INDEX_NAME
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Flush failed: {str(e)}")
    


# ------------------ GET STORED LINKS ------------------ #
@rag_router.get("/links/{chatbot_title}")
def get_stored_links(
    chatbot_title: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieve all stored links for a chatbot from the database.
    Returns format compatible with /crawl/discover for frontend reuse.
    """
    user_id = current_user["id"]
    normalized_title = chatbot_title.strip().lower()

    if not normalized_title:
        raise HTTPException(status_code=400, detail="chatbot_title cannot be empty")

    try:
        from app.helpers.db_helper import get_links_for_chatbot, get_crawl_stats
        
        # Get all links for this chatbot
        links = get_links_for_chatbot(user_id, normalized_title, only_uncrawled=False)
        
        # Get statistics
        stats = get_crawl_stats(user_id, normalized_title)
        
        # Format response to match /crawl/discover structure
        endpoints = [link["url"] for link in links]
        
        # Additional detailed links with metadata
        detailed_links = [
            {
                "url": link["url"],
                "title": link.get("title"),
                "is_crawled": link.get("is_crawled", False),
                "last_updated": link.get("last_updated"),
                "created_at": link.get("created_at")
            }
            for link in links
        ]
        
        return {
            "chatbot_title": normalized_title,
            "endpoints": endpoints,  # Compatible with /crawl/discover
            "total_found": len(links),
            "discovery_method": "database",
            "stats": stats,
            "links": detailed_links  # Detailed metadata for advanced use
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve links: {str(e)}")


# ------------------ Get All Chatbots ------------------ #

@rag_router.get("/all-chatbots")
def get_user_chatbots(current_user: dict = Depends(get_current_user)):
    """Get all chatbots of the current user with their details, token usage, and query counts."""
    user_id = current_user["id"]

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        # Fetch all chatbots for this user
        chatbots_response = (
            supabase.table("chatbot_configs")
            .select("chatbot_title, api_key, is_active, category, description, created_at, updated_at")
            .eq("user_id", user_id)
            .execute()
        )
        
        chatbots_data = chatbots_response.data

        if not chatbots_data:
            return {
                "total_count": 0,
                "total_queries_all_bots": 0,
                "chatbots": []
            }

        chatbot_titles = [bot["chatbot_title"] for bot in chatbots_data]

        # Fetch appearance data for all chatbots
        appearance_response = (
            supabase.table("chatbot_appearance")
            .select("chatbot_title, language, bot_avatar_url")
            .in_("chatbot_title", chatbot_titles)
            .eq("user_id", user_id)
            .execute()
        )
        
        appearance_map = {item["chatbot_title"]: item for item in appearance_response.data}

        # Fetch token usage and query count summary for each bot
        from app.RAG.token_tracker import get_user_total_tokens
        token_summary = get_user_total_tokens(user_id)
        
        if "error" in token_summary:
            # If there's an error fetching token data, proceed without it
            token_data = {}
            total_queries_all_bots = 0
        else:
            token_data = token_summary.get("bots", {})
            total_queries_all_bots = token_summary.get("total_queries_all_bots", 0)

        # Attach token count, query count, and appearance data per bot
        chatbot_list = []
        for bot in chatbots_data:
            chatbot_title = bot["chatbot_title"]
            appearance = appearance_map.get(chatbot_title, {})
            bot_token_data = token_data.get(chatbot_title, {})
            
            chatbot_list.append({
                **bot,
                "language": appearance.get("language"),
                "bot_avatar_url": appearance.get("bot_avatar_url"),
                "total_tokens_used": bot_token_data.get("total_tokens", 0),
                "query_count": bot_token_data.get("query_count", 0),
                "token_breakdown": bot_token_data.get("operations", {})
            })

        return {
            "total_count": len(chatbot_list),
            "total_queries_all_bots": total_queries_all_bots,
            "chatbots": chatbot_list
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chatbots: {str(e)}")

# ------------------ Get Single Chatbot ------------------ #
@rag_router.post("/chatbots/details")
def get_user_chatbot_details(
    request: FlushRequest,
    current_user: dict = Depends(get_current_user)
):
    """Get detailed information for a single chatbot, including appearance and usage data."""
    user_id = current_user["id"]
    normalized_title = request.chatbot_title.strip()

    if not normalized_title:
        raise HTTPException(status_code=400, detail="chatbot_title cannot be empty")

    try:
        from app.supabase import get_admin_supabase_client
        supabase = get_admin_supabase_client()

        # Fetch chatbot config
        chatbot_response = (
            supabase.table("chatbot_configs")
            .select("chatbot_title, api_key, is_active, category, description, created_at, updated_at")
            .eq("user_id", user_id)
            .eq("chatbot_title", normalized_title)
            .single()
            .execute()
        )

        chatbot_data = chatbot_response.data

        if not chatbot_data:
            raise HTTPException(status_code=404, detail=f"Chatbot '{normalized_title}' not found for this user")

        # Fetch appearance data
        appearance_response = (
            supabase.table("chatbot_appearance")
            .select("language, bot_avatar_url")
            .eq("user_id", user_id)
            .eq("chatbot_title", normalized_title)
            .maybe_single()
            .execute()
        )

        appearance_data = appearance_response.data or {}

        # Fetch token usage and query count summary for this bot
        from app.RAG.token_tracker import get_user_total_tokens
        token_summary = get_user_total_tokens(user_id)

        if "error" in token_summary:
            token_data = {}
        else:
            token_data = token_summary.get("bots", {}).get(normalized_title, {})

        chatbot_details = {
            **chatbot_data,
            "language": appearance_data.get("language"),
            "bot_avatar_url": appearance_data.get("bot_avatar_url"),
            "total_tokens_used": token_data.get("total_tokens", 0),
            "query_count": token_data.get("query_count", 0),
            "token_breakdown": token_data.get("operations", {}),
        }

        return {"chatbot": chatbot_details}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chatbot '{normalized_title}': {str(e)}")
