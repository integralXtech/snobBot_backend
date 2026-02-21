import logging
import json
import os
from typing import List, Dict
from datetime import datetime

from app.supabase import get_admin_supabase_client
from app.helpers.spider_client import scrape_url_spider
from app.Blog.models import GeneratedBlog
from app.helpers.credit_manager import CreditManager
from openai import OpenAI

logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def generate_blog_ideas(user_id: str, url: str) -> (str, List[str], int):
    """
    Scrapes URL and generates blog topic ideas.
    Returns: (db_record_id, list_of_titles, tokens_used)
    """
    # 0. Check Credits
    allowed, reason = CreditManager.has_sufficient_credits(user_id, "blog_ideas", 1)
    if not allowed:
        raise ValueError(reason)

    supabase = get_admin_supabase_client()
    
    # 1. Scrape Content
    try:
        scrape_result = scrape_url_spider(url)
        content = scrape_result.get("content", "")
        # Truncate for safety
        safe_content = content[:15000]
    except Exception as e:
        logger.error(f"Scraping failed for {url}: {e}")
        # Fallback: Just generate based on URL string if scraping fails
        safe_content = f"Website URL: {url}. Content could not be scraped."

    # 2. AI Ideation
    prompt = f"""
You are an SEO Content Strategist. Analyze the provided website content to understand the business's core services, industry, and target audience.

Task: Generate 5-10 high-quality, SEO-aligned blog title ideas that would help this business attract organic traffic.

Strict Rules:
1. Relevance: Titles must be directly related to the business's actual services and expertise found in the content.
2. Avoid Generic Titles: Do not use 'clickbait' or overly generic titles. Be specific to the niche.
3. SEO Aware: Each title should target potential user search intents.
4. Output Format: Return ONLY a valid JSON object with a key 'title_ideas' containing an array of strings.

Example Output: {{ "title_ideas": ["Specific Industry Trend 2024", "How [Service Name] Solves [Specific Problem]"] }}

Content:
{safe_content}
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert content strategist."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        
        result_text = response.choices[0].message.content
        tokens = response.usage.total_tokens
        
        parsed = json.loads(result_text)
        titles = []
        if isinstance(parsed, list):
            titles = parsed
        elif isinstance(parsed, dict):
            # Extact list from dict (keys like "titles", "ideas" etc)
            for val in parsed.values():
                if isinstance(val, list):
                    titles = val
                    break
        
        # 3. Save to DB
        db_res = supabase.table("blog_ideation").insert({
            "user_id": user_id,
            "target_url": url,
            "generated_titles": titles,
            "tokens_used": tokens
        }).execute()
        
        record_id = db_res.data[0]["id"]
        
        # 4. Consume Credit
        CreditManager.consume_credits(user_id, "blog_ideas", 1)
        
        return record_id, titles, tokens

    except Exception as e:
        logger.error(f"Ideation failed: {e}")
        raise e

async def generate_full_blog(user_id: str, title: str, custom_prompt: str = None) -> (str, GeneratedBlog, int):
    """
    Generates a full blog post.
    Returns: (db_record_id, GeneratedBlog, tokens_used)
    """
    # 0. Check Credits
    allowed, reason = CreditManager.has_sufficient_credits(user_id, "blog_creation", 1)
    if not allowed:
        raise ValueError(reason)

    supabase = get_admin_supabase_client()
    
    instructions = custom_prompt if custom_prompt else f"Write a comprehensive blog post about: {title}"
    
    prompt = f"""
    You are an expert SEO copywriter. Write a full blog post based on the following topic.
    
    Topic: {title}
     Additional Instructions: {instructions}
    
    Requirements:
    1.  **Title**: Catchy and SEO-optimized.
    2.  **Description**: A short excerpt (1-2 sentences).
    3.  **Meta Description**: SEO meta description (max 160 chars).
    4.  **Keywords**: List of 5-10 target keywords.
    5.  **Content**: Full article in Markdown format. Use H1 for title, H2/H3 for subsections. engaging tone. at least 600 words.
    
    Output Format: JSON object with keys: "title", "description", "meta_description", "keywords" (list), "content" (markdown string).
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional blog writer."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        
        result_text = response.choices[0].message.content
        tokens = response.usage.total_tokens
        
        data = json.loads(result_text)
        
        # Verify keys
        required_keys = ["title", "description", "meta_description", "keywords", "content"]
        for k in required_keys:
            if k not in data:
                # Basic fallback/fix logic could go here, or just fail
                if k == "keywords": data[k] = []
                else: data[k] = ""
                
        blog_obj = GeneratedBlog(**data)
        
        # Save to DB
        db_res = supabase.table("blog_contents").insert({
            "user_id": user_id,
            "title": blog_obj.title,
            "input_prompt": instructions,
            "description": blog_obj.description,
            "meta_description": blog_obj.meta_description,
            "keywords": blog_obj.keywords,
            "content": blog_obj.content,
            "tokens_used": tokens
        }).execute()
        
        record_id = db_res.data[0]["id"]
        
        # 4. Consume Credit
        CreditManager.consume_credits(user_id, "blog_creation", 1)
        
        return record_id, blog_obj, tokens
        
    except Exception as e:
        logger.error(f"Blog generation failed: {e}")
        raise e
