from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.RAG.auth_utils import get_current_user
from app.helpers.spider_client import discover_links_spider
from app.SEO.models import GenerateFAQSRequest, SEOJobResponse, SEOJobDetailResponse, FAQResult, FAQItem
from app.SEO.service import start_seo_job
from app.supabase import get_admin_supabase_client
from app.helpers.credit_manager import CreditManager

seo_router = APIRouter(prefix="/seo", tags=["SEO Generator"])

from datetime import datetime

# ... (imports)

@seo_router.post(
    "/generate-faqs",
    response_model=SEOJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start SEO FAQ Generation Job"
)
async def generate_faqs(
    request: GenerateFAQSRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Starts a background job to crawl the URL and generate FAQs.
    If URL ends in '/', it scans the website (limited scope).
    Otherwise, it scans the single page.
    """
    try:
        # 0. Check Credits
        allowed, reason = CreditManager.has_sufficient_credits(current_user["id"], "faq", 1)
        if not allowed:
             raise HTTPException(status_code=403, detail=reason)

        job_id = await start_seo_job(current_user["id"], request.url)
        
        # Return initial pending state
        return {
            "id": job_id,
            "user_id": current_user["id"],
            "target_url": request.url,
            "scope": "full" if request.url.endswith("/") else "single",
            "status": "pending",
            "created_at": datetime.utcnow(), 
            "completed_at": None,
            "tokens_used": 0,
            "error_message": None
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )

@seo_router.get(
    "/history",
    response_model=List[SEOJobResponse],
    summary="Get Job History"
)
async def get_history(current_user: dict = Depends(get_current_user)):
    """Fetch previous FAQ generation jobs."""
    try:
        supabase = get_admin_supabase_client()
        result = (
            supabase.table("seo_jobs")
            .select("*")
            .eq("user_id", current_user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        return result.data
    except Exception as e:
         raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )

@seo_router.get(
    "/jobs/{job_id}",
    response_model=SEOJobDetailResponse,
    summary="Get Job Details and Results"
)
async def get_job_details(job_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch status and generated FAQs for a specific job."""
    try:
        supabase = get_admin_supabase_client()
        
        # 1. Get Job Info
        job_res = (
            supabase.table("seo_jobs")
            .select("*")
            .eq("id", job_id)
            .eq("user_id", current_user["id"])
            .execute()
        )
        
        if not job_res.data:
             raise HTTPException(status_code=404, detail="Job not found")
        
        job_data = job_res.data[0]
        
        # 2. Get FAQs
        faq_res = (
            supabase.table("seo_faqs")
            .select("page_url, faq_data")
            .eq("job_id", job_id)
            .execute()
        )
        
        results = []
        for row in faq_res.data:
            faqs = [FAQItem(**item) for item in row["faq_data"]]
            results.append(FAQResult(page_url=row["page_url"], faqs=faqs))
            
        # Merge response
        response_data = job_data.copy()
        response_data["results"] = results
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
         raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )
