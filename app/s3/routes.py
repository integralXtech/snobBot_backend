from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel, model_validator
import json
from typing import List, Dict, Any, Optional, Union
import re
from urllib.parse import urlparse

from app.s3.s3_helper import (
    upload_file_to_s3,
    list_files_in_s3,
    get_file_from_s3,
    generate_presigned_url,
    delete_file_from_s3,
)
from app.RAG.auth_utils import get_current_user, get_api_key
from pinecone import Pinecone
import os
# Internal RAG imports (internal calls, Option 1)
from app.RAG.pdf_processor import process_and_index_data,sanitize_id
from app.RAG.token_tracker import update_tokens
from app.RAG import routes as rag_routes  # to call fetch_and_index internally (async)
from app.helpers.agency_helper import check_usage_limits, track_and_log_usage, get_user_agency_info
# NOTE: rag_routes.fetch_and_index is async; we'll await it in crawl flow below

s3_router = APIRouter(prefix="/s3", tags=["S3"])

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
pc = Pinecone(api_key=PINECONE_API_KEY)

# ------------------ MODELS ------------------ #
class UploadRawTextRequest(BaseModel):
    chatbot_title: str
    raw_text: str
    retrain_flag : bool =False

class QARequest(BaseModel):
    chatbot_title: str
    qa_pairs: List[Dict[str, str]]  # [{"question": "...", "answer": "..."}]
    retrain_flag: bool = False

class CrawlRequest(BaseModel):
    chatbot_title: str
    url_list: List[str]  # Primary field - always use url_list from frontend
    
    # Keep url and urls for backward compatibility (optional)
    url: Optional[str] = None  # deprecated - use url_list instead
    urls: Optional[List[str]] = None  # deprecated - use url_list instead
    
    @model_validator(mode='after')
    def validate_urls(self):
        """Validate URLs and ensure url_list is populated."""
        # If url_list is provided, validate it
        if self.url_list is not None:
            if not isinstance(self.url_list, list) or len(self.url_list) == 0:
                raise ValueError("url_list must be a non-empty list")
            return self
        
        # Backward compatibility: convert url or urls to url_list
        if self.url is not None:
            self.url_list = [self.url]
        elif self.urls is not None:
            self.url_list = self.urls
        else:
            raise ValueError("Either 'url_list', 'url', or 'urls' must be provided")
        
        if not self.url_list or len(self.url_list) == 0:
            raise ValueError("At least one URL must be provided")
        
        return self

class FetchRequest(BaseModel):
    chatbot_title: str

class RemoveRequest(BaseModel):
    chatbot_title: str
    filename: str

    
class RemoveCrawlRequest(BaseModel):
    chatbot_title: str
    url: str
    

def url_to_filename(url: str) -> str:
    """
    Converts a URL to a safe filename for S3.
    Example: https://www.axeonic.com/ -> www.axeonic.com.txt
             https://www.axeonic.com/page -> www.axeonic.com_page.txt
    """
    parsed = urlparse(url)
    # Combine netloc + path
    path = parsed.netloc + parsed.path
    # Remove trailing slash
    path = path.rstrip("/")
    # Replace any non-alphanumeric character with _
    filename = re.sub(r"[^A-Za-z0-9\-\.]", "_", path)
    return f"{filename}.txt"
    
# ------------------------------------------------------------------------------------------- #
# =========================================================================================== #
# -------------------------------------- UPLOAD APIs ---------------------------------------- #
# =========================================================================================== #
# ------------------------------------------------------------------------------------------- #

# ------------------ FILE UPLOAD ------------------ #
@s3_router.post("/upload/file")
async def upload_file_to_s3_api(
    file: UploadFile = File(...),
    chatbot_title: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload file to S3 and then index it via internal RAG call (process_and_index_data).
    Returns upload metadata + indexing result (if indexing succeeded/failed).
    """
    try:
        user_id = current_user["id"]
        chatbot_title = chatbot_title.lower()

        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        # 1. Quota Check (Hard Limit for user)
        limit_check = check_usage_limits(user_id)
        if not limit_check["allowed"]:
            raise HTTPException(403, limit_check["reason"])

        file_bytes = await file.read()
        s3_key = f"{user_id}/{chatbot_title}/files/{file.filename}"

        result = upload_file_to_s3(file_bytes, s3_key, file.content_type)
        if result["status"] == "error":
            raise HTTPException(500, result["message"])

        # --- Call internal indexing (single call for this file) ---
        indexing_result = None
        indexing_errors = None
        try:
            proc_result = process_and_index_data(
                user_id=user_id,
                filename=file.filename,
                file_bytes=file_bytes,
                chatbot_title=chatbot_title,
            )

            # update token tracker
            try:
                update_tokens(
                    user_id=user_id,
                    chatbot_title=chatbot_title,
                    operation_type="file_upload",
                    tokens_used=proc_result.get("tokens_used", 0),
                )
            except Exception:
                # non-fatal if token update fails
                pass
            
            # --- Agencies Feature: Dual Usage Tracking ---
            chatbot_data = (
                get_admin_supabase_client()
                .table("chatbot_configs")
                .select("id")
                .eq("user_id", user_id)
                .eq("chatbot_title", chatbot_title)
                .execute()
            )
            chatbot_id = chatbot_data.data[0]["id"] if chatbot_data.data else None
            
            await track_and_log_usage(
                user_id=user_id,
                chatbot_id=chatbot_id,
                type="training",
                amount=1 # Count as 1 training event
            )

            indexing_result = {
                "chunks_indexed": proc_result.get("chunks_indexed"),
                "tokens_used": proc_result.get("tokens_used"),
            }

        except Exception as e:
            indexing_errors = str(e)

        return {
            "url": result["url"],
            "filename": file.filename,
            "uploaded_by": user_id,
            "chatbot_title": chatbot_title,
            "source": "file",
            "indexing": {
                "result": indexing_result,
                "error": indexing_errors,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------ RAW TEXT UPLOAD ------------------ #
@s3_router.post("/upload/raw")
async def upload_raw_to_s3_api(
    request: UploadRawTextRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Upload raw text to S3 and index it.

    NEW FEATURE:
    - If request.force_refresh == True:
        1) Calls /remove/raw
        2) Uploads new file + indexes it
    - If request.force_refresh == False:
        → Direct upload + index (previous behavior)
    """

    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()
        retrain_flag = getattr(request, "retrain_flag", False)

        # Validate API key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key for '{chatbot_title}'")

        # ---------------------------------------------------------------
        # STEP 0 — If retrain_flag=True, delete old RAW + vectors
        # ---------------------------------------------------------------
        print(  f"Retrain flag is set to {retrain_flag}"  )
        if retrain_flag:
            remove_payload = RemoveRequest(
                chatbot_title=chatbot_title,
                filename=f"{chatbot_title}.txt"
            )

            # Call the remove endpoint programmatically
            await remove_raw_and_vectors_api(remove_payload, current_user)

        # 1. Quota Check
        limit_check = check_usage_limits(user_id)
        if not limit_check["allowed"]:
            raise HTTPException(403, limit_check["reason"])

        # ---------------------------------------------------------------
        # STEP 1 — Upload RAW text to S3
        # ---------------------------------------------------------------
        s3_key = f"{user_id}/{chatbot_title}/raw/{chatbot_title}.txt"
        file_bytes = request.raw_text.encode("utf-8")

        result = upload_file_to_s3(file_bytes, s3_key, "text/plain")
        if result["status"] == "error":
            raise HTTPException(500, result["message"])

        # ---------------------------------------------------------------
        # STEP 2 — Process + Index the text
        # ---------------------------------------------------------------
        indexing_result = None
        indexing_errors = None

        try:
            proc_result = process_and_index_data(
                user_id=user_id,
                raw_text=request.raw_text,
                chatbot_title=chatbot_title,
            )

            # Update token usage
            try:
                update_tokens(
                    user_id=user_id,
                    chatbot_title=chatbot_title,
                    operation_type="raw_text",
                    tokens_used=proc_result.get("tokens_used", 0),
                )
            except Exception:
                pass
            
            # --- Agencies Feature: Dual Usage Tracking ---
            chatbot_data = (
                get_admin_supabase_client()
                .table("chatbot_configs")
                .select("id")
                .eq("user_id", user_id)
                .eq("chatbot_title", chatbot_title)
                .execute()
            )
            chatbot_id = chatbot_data.data[0]["id"] if chatbot_data.data else None

            await track_and_log_usage(
                user_id=user_id,
                chatbot_id=chatbot_id,
                type="training",
                amount=1
            )

            indexing_result = {
                "chunks_indexed": proc_result.get("chunks_indexed"),
                "tokens_used": proc_result.get("tokens_used"),
            }

        except Exception as e:
            indexing_errors = str(e)

        # ---------------------------------------------------------------
        # STEP 3 — Return metadata
        # ---------------------------------------------------------------
        return {
            "url": result["url"],
            "chatbot_title": chatbot_title,
            "uploaded_by": user_id,
            "source": "raw_text",
            "force_refresh": retrain_flag,
            "indexing": {
                "result": indexing_result,
                "error": indexing_errors,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ------------------ QA PAIRS UPLOAD ------------------ #
@s3_router.post("/upload/qa")
async def upload_qa_to_s3_api(
    request: QARequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Upload QA pairs JSON to S3 and index them (process_and_index_data with qa_json).
    If replace_existing=True → remove old QA + vectors first.
    Returns upload metadata + indexing result.
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # Validate API Key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        # --------------------------------------------------------------------
        # NEW LOGIC: If replace_existing=True, call /remove/qa first
        # --------------------------------------------------------------------
        if getattr(request, "retrain_flag", False):
            remove_payload = RemoveRequest(
                chatbot_title=request.chatbot_title,
                filename=f"{chatbot_title}.json"
            )
            await remove_qa_and_vectors_api(remove_payload, current_user)
        # --------------------------------------------------------------------

        # Upload new QA file to S3
        s3_key = f"{user_id}/{chatbot_title}/qa/{chatbot_title}.json"
        file_bytes = json.dumps(request.qa_pairs, indent=2).encode("utf-8")

        result = upload_file_to_s3(file_bytes, s3_key, "application/json")
        if result["status"] == "error":
            raise HTTPException(500, result["message"])

        # Index QA pairs
        indexing_result = None
        indexing_errors = None
        try:
            proc_result = process_and_index_data(
                user_id=user_id,
                qa_json=request.qa_pairs,
                chatbot_title=chatbot_title,
            )

            try:
                update_tokens(
                    user_id=user_id,
                    chatbot_title=chatbot_title,
                    operation_type="qa_pairs",
                    tokens_used=proc_result.get("tokens_used", 0),
                )
            except Exception:
                pass

            indexing_result = {
                "chunks_indexed": proc_result.get("chunks_indexed"),
                "tokens_used": proc_result.get("tokens_used"),
            }
        except Exception as e:
            indexing_errors = str(e)

        return {
            "url": result["url"],
            "chatbot_title": chatbot_title,
            "uploaded_by": user_id,
            "source": "qa_pairs",
            "indexing": {
                "result": indexing_result,
                "error": indexing_errors,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    
# ------------------ CRAWL URLS UPLOAD ------------------ #
@s3_router.post("/upload/crawl")
async def upload_crawl_to_s3_api(
    request: CrawlRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Crawl and index URLs, then save to S3 only if successful.
    Also persists discovered links in Supabase for tracking.
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # Check API key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        urls_list = request.url_list
        if not urls_list:
            raise HTTPException(400, "At least one URL must be provided")

        # ============================================================
        # STEP 1: Persist URLs in Supabase (before crawling)
        # ============================================================
        try:
            from app.helpers.db_helper import upsert_discovered_links
            
            links_to_store = [{"url": url, "title": None} for url in urls_list]
            upsert_result = upsert_discovered_links(user_id, chatbot_title, links_to_store)
            print(f"📊 Stored {upsert_result['total']} links in database")
        except Exception as db_error:
            print(f"⚠️ Failed to persist links in database: {db_error}")
            # Non-fatal, continue with crawling

        # ============================================================
        # STEP 2: Crawl and Index Each URL
        # ============================================================
        indexing_summary = {
            "success_count": 0,
            "failed": [],
            "details": []
        }

        saved_files = []

        for url in urls_list:

            # ------------------------------
            # TRY CRAWLING & INDEXING
            # ------------------------------
            try:
                fetch_req = rag_routes.FetchRequest(
                    base_url=url,
                    endpoint="",
                    chatbot_title=chatbot_title,
                )

                resp = await rag_routes.fetch_and_index(fetch_req, current_user)

            except Exception as e:
                indexing_summary["failed"].append({
                    "url": url,
                    "error": str(e)
                })
                continue     # ❌ Don't save to S3 if crawl/index fails

            # If indexing response is empty or contains known fail markers
            if not resp or resp == {} or ("error" in resp):
                indexing_summary["failed"].append({
                    "url": url,
                    "error": "Indexing returned no meaningful data"
                })
                continue    # ❌ Also don't save

            # ------------------------------
            # ONLY SAVE TO S3 IF CRAWL SUCCEEDED
            # ------------------------------
            filename = url_to_filename(url)
            s3_key = f"{user_id}/{chatbot_title}/crawls/{filename}"
            file_bytes = url.encode("utf-8")

            result = upload_file_to_s3(file_bytes, s3_key, "text/plain")

            if result["status"] == "error":
                indexing_summary["failed"].append({
                    "url": url,
                    "error": result["message"]
                })
                continue

            # ---------------------------------------
            # SUCCESS CASE
            # ---------------------------------------
            saved_files.append(filename)
            indexing_summary["success_count"] += 1
            indexing_summary["details"].append({
                "url": url,
                "result": resp
            })

        return {
            "chatbot_title": chatbot_title,
            "uploaded_by": user_id,
            "saved_files": saved_files,
            "source": "web_crawling",
            "indexing_summary": indexing_summary
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

    
# ------------------------------------------------------------------------------------------- #
# =========================================================================================== #
# -------------------------------------- FETCH APIs ----------------------------------------- #
# =========================================================================================== #
# ------------------------------------------------------------------------------------------- #
    
# ------------------ FETCH FILES ------------------ #
@s3_router.post("/fetch/files")
async def fetch_files_api(
    request: FetchRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # 🔐 Check API key before fetching
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        prefix = f"{user_id}/{chatbot_title}/files/"
        objects = list_files_in_s3(prefix)
        files = []

        for obj in objects:
            key = obj["key"]
            presigned_url = generate_presigned_url(key, expires_in=3600)
            files.append({
                "filename": key.split("/")[-1],
                "url": presigned_url
            })

        return {"chatbot_title": chatbot_title, "files": files}
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------ FETCH RAW TEXTS ------------------ #
@s3_router.post("/fetch/raw")
async def fetch_raw_api(
    request: FetchRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # 🔐 Check API key before fetching
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        prefix = f"{user_id}/{chatbot_title}/raw/"
        objects = list_files_in_s3(prefix)

        raws = []
        for obj in objects:
            key = obj["key"]
            content = get_file_from_s3(key).decode("utf-8")
            raws.append({"filename": key.split("/")[-1], "content": content})

        return {"chatbot_title": chatbot_title, "raw_texts": raws}
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------ FETCH QA PAIRS ------------------ #
@s3_router.post("/fetch/qa")
async def fetch_qa_api(
    request: FetchRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # 🔐 Check API key before fetching
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        prefix = f"{user_id}/{chatbot_title}/qa/"
        objects = list_files_in_s3(prefix)

        qa_files = []
        for obj in objects:
            key = obj["key"]
            content = get_file_from_s3(key).decode("utf-8")
            try:
                qa_pairs = json.loads(content)
            except Exception:
                qa_pairs = []
            qa_files.append({"filename": key.split("/")[-1], "qa_pairs": qa_pairs})

        return {"chatbot_title": chatbot_title, "qa_data": qa_files}
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------ FETCH CRAWLED URLS ------------------ #
@s3_router.post("/fetch/crawl")
async def fetch_crawl_api(
    request: FetchRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch all crawled URLs for a chatbot.
    Each URL is stored in its own file under /crawls.
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # 🔐 Validate API key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        prefix = f"{user_id}/{chatbot_title}/crawls/"

        # This returns objects, not strings
        s3_objects = list_files_in_s3(prefix)
        print(s3_objects)
        crawls = []

        for obj in s3_objects:
            # ⛔ obj is dict → extract its 'Key'
            key = obj.get("key", "")

            if not key.endswith(".txt"):
                continue

            # Extract filename after the prefix
            filename = key.replace(prefix, "")

            # Read URL inside the file
            content = get_file_from_s3(key).decode("utf-8").strip()

            crawls.append({
                "filename": filename,
                "url": content
            })

        return {
            "chatbot_title": chatbot_title,
            "crawls": crawls
        }

    except Exception as e:
        raise HTTPException(500, str(e))
    
# ------------------------------------------------------------------------------------------- #
# =========================================================================================== #
# -------------------------------------- REMOVE APIs ---------------------------------------- #
# =========================================================================================== #
# ------------------------------------------------------------------------------------------- #

@s3_router.post("/remove/file")
async def remove_file_and_vectors_api(
    request: RemoveRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Remove file from S3 AND delete all Pinecone vectors whose metadata.source == filename.
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()
        filename = request.filename

        # 🔐 Validate API key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(
                403,
                f"No active API key found for chatbot '{chatbot_title}'"
            )

        # ---------------------------------------------------------
        # DELETE FILE FROM S3
        # ---------------------------------------------------------
        key = f"{user_id}/{chatbot_title}/files/{filename}"
        s3_result = delete_file_from_s3(key)

        if s3_result["status"] == "error":
            raise HTTPException(404, s3_result["message"])

        # ---------------------------------------------------------
        # DELETE CORRESPONDING PINECONE VECTORS
        # ---------------------------------------------------------
        INDEX_NAME = f"snobbots-{sanitize_id(user_id.lower().replace(' ', '_'))}"
        namespace = sanitize_id(chatbot_title.strip().lower().replace(" ", "_"))

        index = pc.Index(INDEX_NAME)
        dimension = 3072  # match embedding dimension

        ids_to_delete = []
        top_k = 1000

        while True:
            response = index.query(
                vector=[0.0] * dimension,           # dummy vector
                top_k=top_k,
                include_metadata=True,
                filter={"source": filename, "user_id": user_id},
                namespace=namespace
            )

            matches = response.get("matches", [])
            if not matches:
                break

            ids_to_delete.extend([m["id"] for m in matches])

            if len(matches) < top_k:
                break

        # Delete in Pinecone
        deleted_count = 0
        if ids_to_delete:
            batch_size = 500
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i:i + batch_size]
                index.delete(ids=batch, namespace=namespace)
                deleted_count += len(batch)

        return {
            "success": True,
            "removed_file": filename,
            "vectors_deleted": deleted_count,
            "message": f"Removed file '{filename}' and deleted {deleted_count} vectors."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    
    
@s3_router.post("/remove/qa")
async def remove_qa_and_vectors_api(
    request: RemoveRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    1) Deletes the QA file from S3.
    2) Deletes all Pinecone vectors where:
        metadata.source == "qa_json"
        metadata.filename == request.filename
        metadata.user_id == current_user.id
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # Validate API Key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(
                403,
                f"No active API key found for chatbot '{chatbot_title}'"
            )

        # ----------- STEP 1: DELETE FILE FROM S3 -----------
        key = f"{user_id}/{chatbot_title}/qa/{request.filename}"
        result = delete_file_from_s3(key)

        if result["status"] == "error":
            raise HTTPException(404, result["message"])

        # ----------- STEP 2: DELETE MATCHING PINECONE VECTORS -----------

        INDEX_NAME = f"snobbots-{sanitize_id(str(user_id).lower())}"
        namespace = sanitize_id(chatbot_title.strip().lower())

        index = pc.Index(INDEX_NAME)
        dimension = 3072
        top_k = 1000
        ids_to_delete = []

        # Query in loops until no more matches
        while True:
            response = index.query(
                vector=[0.0] * dimension,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace,
                filter={
                    "source": "qa_json",
                    "user_id": user_id
                }
            )

            matches = response.get("matches", [])
            if not matches:
                break

            ids_to_delete.extend([m["id"] for m in matches])

            if len(matches) < top_k:
                break

        # Perform deletion
        if ids_to_delete:
            batch_size = 500
            for i in range(0, len(ids_to_delete), batch_size):
                index.delete(
                    ids=ids_to_delete[i:i + batch_size],
                    namespace=namespace
                )

        return {
            "success": True,
            "removed_file": request.filename,
            "deleted_vectors": len(ids_to_delete)
        }

    except Exception as e:
        raise HTTPException(500, str(e))
    
@s3_router.post("/remove/raw")
async def remove_raw_and_vectors_api(
    request: RemoveRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    1) Delete RAW file from S3.
    2) Delete all Pinecone vectors that match:
        metadata.source == "raw_text"
        metadata.filename == request.filename
        metadata.user_id == current_user.id
    """
    try:
        user_id = current_user["id"]
        chatbot_title = request.chatbot_title.lower()

        # Validate chatbot API key
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(
                403, f"No active API key found for chatbot '{chatbot_title}'"
            )

        # ---------------------- STEP 1: DELETE FROM S3 ----------------------
        key = f"{user_id}/{chatbot_title}/raw/{request.filename}"
        result = delete_file_from_s3(key)

        if result["status"] == "error":
            raise HTTPException(404, result["message"])

        # ---------------------- STEP 2: DELETE FROM PINECONE ----------------------
        INDEX_NAME = f"snobbots-{sanitize_id(str(user_id).lower())}"
        namespace = sanitize_id(chatbot_title.strip().lower())

        index = pc.Index(INDEX_NAME)
        dimension = 3072  # Your index dimension
        top_k = 1000

        ids_to_delete = []

        # Loop to collect ALL matching vectors
        while True:
            response = index.query(
                vector=[0.0] * dimension,      # Dummy vector
                top_k=top_k,
                include_metadata=True,
                namespace=namespace,
                filter={
                    "source": "raw_text",
                    "user_id": user_id
                }
            )

            matches = response.get("matches", [])
            if not matches:
                break

            ids_to_delete.extend([m["id"] for m in matches])

            if len(matches) < top_k:
                break

        # No matching vectors?
        if not ids_to_delete:
            return {
                "success": True,
                "removed_file": request.filename,
                "deleted_vectors": 0
            }

        # Batch delete
        batch_size = 500
        for i in range(0, len(ids_to_delete), batch_size):
            index.delete(
                ids=ids_to_delete[i:i + batch_size],
                namespace=namespace
            )

        return {
            "success": True,
            "removed_file": request.filename,
            "deleted_vectors": len(ids_to_delete)
        }

    except Exception as e:
        raise HTTPException(500, str(e))
    
    
@s3_router.post("/remove/crawl")
async def remove_crawl_and_vectors(
    request: RemoveCrawlRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["id"]
        removed_files = []
        removed_vectors = []

        chatbot_title = request.chatbot_title.lower()
        api_key = get_api_key(user_id, chatbot_title)
        if not api_key:
            raise HTTPException(403, f"No active API key found for chatbot '{chatbot_title}'")

        # Remove file from S3 if filename is provided
        if request.url:
            filename= url_to_filename(request.url)
            key = f"{user_id}/{chatbot_title}/crawls/{filename}"
            result = delete_file_from_s3(key)
            if result["status"] == "error":
                raise HTTPException(404, result["message"])
            removed_files.append(filename)

        # Remove vectors from Pinecone if URL is provided
        if request.url:
            INDEX_NAME = f"snobbots-{sanitize_id(user_id.lower().replace(' ', '_'))}"
            namespace = sanitize_id(chatbot_title.strip().lower().replace(" ", "_"))

            if INDEX_NAME not in pc.list_indexes().names():
                raise HTTPException(404, f"Pinecone index '{INDEX_NAME}' not found")

            index = pc.Index(INDEX_NAME)
            source_str = request.url.rstrip("/")

            resp = index.delete(
                namespace=namespace,
                filter={
                    "source": source_str,
                    "user_id": user_id
                }
            )

            removed_vectors.append({
                "source": source_str,
                "namespace": namespace,
                "removed_count": resp.get("deletedCount", "unknown")
            })

        return {
            "success": True,
            "removed_files": removed_files,
            "removed_vectors": removed_vectors,
        }

    except Exception as e:
        raise HTTPException(500, str(e))