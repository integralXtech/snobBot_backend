
import asyncio
import logging
from datetime import datetime
from typing import List
from urllib.parse import urlparse
import json
import os

from app.supabase import get_admin_supabase_client
from app.helpers.spider_client import discover_links_spider, scrape_url_spider
from app.SEO.models import FAQItem
from openai import OpenAI

logger = logging.getLogger(__name__)

# Initialize OpenAI Client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def start_seo_job(user_id: str, url: str) -> str:
    """
    Initialize an SEO job and start background processing.
    """
    supabase = get_admin_supabase_client()
    
    # 1. Determine Scope
    parsed = urlparse(url)
    # If path is empty or just '/', it's likely a full site scan.
    # Otherwise, it's a single page.
    is_root = parsed.path in ("", "/")
    scope = "full" if is_root else "single"
    
    # 2. Create Job Record
    job_data = {
        "user_id": user_id,
        "target_url": url,
        "scope": scope,
        "status": "pending"
    }
    
    result = supabase.table("seo_jobs").insert(job_data).execute()
    if not result.data:
        raise Exception("Failed to create SEO job record")
        
    job_id = result.data[0]["id"]
    
    # 3. Start Background Task (Fire and Forget)
    asyncio.create_task(process_seo_job(job_id, url, scope, user_id))
    
    return job_id

async def process_seo_job(job_id: str, url: str, scope: str, user_id: str):
    """
    Background worker to crawl and generate FAQs.
    """
    supabase = get_admin_supabase_client()
    
    try:
        # Update status to processing
        supabase.table("seo_jobs").update({"status": "processing"}).eq("id", job_id).execute()
        
        urls_to_process = []
        
        if scope == "single":
            urls_to_process = [url]
        else:
            # Full scan: Discover links
            try:
                # Limit discovery to prevent overload (e.g., 50 pages max for now logic-wise)
                discovered = discover_links_spider(url, limit=50)
                # Filter to ensure they belong to the same domain (spider usually does this, but safely)
                base_domain = urlparse(url).netloc
                urls_to_process = [u for u in discovered if urlparse(u).netloc == base_domain]
                # Default to at least the root URL if discovery fails or returns empty
                if not urls_to_process:
                    urls_to_process = [url]
            except Exception as e:
                logger.error(f"Discovery failed for job {job_id}: {e}")
                # Fallback to single page if discovery fails
                urls_to_process = [url]

        total_tokens = 0
        
        for target_page in urls_to_process:
            try:
                # 1. Scrape Content
                scrape_result = scrape_url_spider(target_page)
                content = scrape_result.get("content", "")
                
                if not content or len(content) < 50: # Skip empty/thin pages
                    continue
                    
                # 2. Generate FAQs
                faqs, usage = await generate_faqs_from_content(content)
                total_tokens += usage
                
                if faqs:
                   # 3. Save FAQs
                   supabase.table("seo_faqs").insert({
                       "job_id": job_id,
                       "page_url": target_page,
                       "faq_data": [f.dict() for f in faqs]
                   }).execute()
                   
            except Exception as e:
                logger.warning(f"Failed to process page {target_page} in job {job_id}: {e}")
                continue

        # Update Job Completion
        supabase.table("seo_jobs").update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(),
            "tokens_used": total_tokens
        }).eq("id", job_id).execute()

    except Exception as e:
        logger.error(f"SEO Job {job_id} failed: {e}")
        supabase.table("seo_jobs").update({
            "status": "failed",
            "error_message": str(e)
        }).eq("id", job_id).execute()

async def generate_faqs_from_content(content: str) -> (List[FAQItem], int):
    """
    Uses OpenAI to extract/generate FAQs from text content.
    Returns: (List[FAQItem], total_tokens_used)
    """
    
    # Truncate content to avoid context limit (approx 20k chars ~ 5k tokens safe for mini)
    safe_content = content
    
    prompt = f"""
System Prompt: "You are an expert SEO Content Strategist and Copywriter. Your task is to analyze the provided webpage content and generate a set of highly relevant, page-specific FAQs.

Strict Rules:
1. Page Specificity: Every Question and Answer must be derived directly from the content of the provided page. Generic, site-wide FAQs are strictly prohibited unless that specific information is on the page. 
2. SEO Intent: Focus on high-value keywords and user search intent related to the page's topic structure. 
3. Tone: Maintain a professional yet helpful tone that aligns with the business's identity.
4. Independent Units: Treat this page as an independent unit. Do not reference other pages or the website as a whole. 
5. Output Format: Return ONLY a valid JSON array of objects. Each object must have a 'question' and an 'answer' key. 

Content:
{safe_content}"
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an SEO expert assistant."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" } # Force JSON mode
        )
        
        result_text = response.choices[0].message.content
        usage = response.usage.total_tokens
        
        # Parse JSON
        # Expecting {"faqs": [...]} or just [...] depending on model behavior, strict json mode usually requires schema in prompt or handles object wrapper
        # Let's adjust prompt to ensure specific schema or robustly parse.
        # Actually gpt-4o-mini json mode enforces valid JSON, usually wrapped in an object if instructed.
        
        parsed = json.loads(result_text)
        
        # Handle cases where it wraps in a key like "faqs" or "data"
        items = []
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
             # Try common keys
             for key in ["faqs", "questions", "pairs", "items"]:
                 if key in parsed and isinstance(parsed[key], list):
                     items = parsed[key]
                     break
             if not items: # Fallback: assume dict values might be the list? unlikely. 
                 # If only one dict returned?
                 pass

        valid_faqs = []
        for i in items:
            if "question" in i and "answer" in i:
                valid_faqs.append(FAQItem(question=i["question"], answer=i["answer"]))
                
        return valid_faqs, usage
        
    except Exception as e:
        logger.error(f"OpenAI Generation failed: {e}")
        return [], 0
