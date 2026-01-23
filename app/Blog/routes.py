from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.RAG.auth_utils import get_current_user
from app.Blog.models import (
    BlogIdeationRequest, BlogIdeationResponse, 
    BlogGenerationRequest, BlogGenerationResponse,
    BlogHistoryItem
)
from app.Blog.service import generate_blog_ideas, generate_full_blog
from app.supabase import get_admin_supabase_client

blog_router = APIRouter(prefix="/blog", tags=["Blog Generator"])

@blog_router.post(
    "/ideation",
    response_model=BlogIdeationResponse,
    summary="Generate Blog Titles from URL"
)
async def create_ideation(
    request: BlogIdeationRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        rec_id, titles, tokens = await generate_blog_ideas(current_user["id"], request.url)
        return {
            "id": rec_id,
            "titles": titles,
            "tokens_used": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from datetime import datetime

# ... (imports)

@blog_router.post(
    "/generate",
    response_model=BlogGenerationResponse,
    summary="Generate Full Blog Post"
)
async def create_blog(
    request: BlogGenerationRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        rec_id, blog, tokens = await generate_full_blog(
            current_user["id"], 
            request.title, 
            request.custom_prompt
        )
        # Construct response merging DB info and Blog object
        return {
            "id": rec_id,
            "title": blog.title,
            "description": blog.description,
            "meta_description": blog.meta_description,
            "keywords": blog.keywords,
            "content": blog.content,
            "tokens_used": tokens,
            # We don't have created_at from service return, but we can query or just use current time for response
            "created_at": datetime.utcnow() 
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@blog_router.get(
    "/history",
    response_model=List[BlogHistoryItem],
    summary="Get Blog Generation History"
)
async def get_history(current_user: dict = Depends(get_current_user)):
    try:
        supabase = get_admin_supabase_client()
        result = (
            supabase.table("blog_contents")
            .select("*")
            .eq("user_id", current_user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
