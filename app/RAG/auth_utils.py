import logging
from fastapi import Header, HTTPException
from app.supabase import get_supabase_client
from app.core.config import settings
import requests
from typing import Optional

logger = logging.getLogger(__name__)

def get_current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        logger.warning("Invalid authorization header format")
        raise HTTPException(status_code=401, detail="Invalid auth header")

    token = authorization.split(" ")[1]
    
    # Use settings directly instead of trying to read from client object
    # to avoid attribute errors and ensure we use the correct keys.
    resp = requests.get(
        f"{settings.supabase_url}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}", 
            "apikey": settings.supabase_anon_key
        }
    )

    if resp.status_code != 200:
        logger.warning(f"Token validation failed with status {resp.status_code}: {resp.text}")
        raise HTTPException(status_code=401, detail="Invalid token")

    return resp.json()

def validate_api_key(api_key: str) -> Optional[dict]:
    """Validate API key and return user_id and chatbot_title."""
    supabase = get_supabase_client()
    
    result = supabase.table('chatbot_configs').select('user_id, chatbot_title, is_active').eq('api_key', api_key).eq('is_active', True).execute()
    
    if not result.data:
        return None
    
    return {
        'user_id': result.data[0]['user_id'],
        'chatbot_title': result.data[0]['chatbot_title']
    }
    
    
def get_api_key(user_id: str, chatbot_title: str) -> Optional[str]:
    """Fetch API key using user_id and chatbot_title."""
    supabase = get_supabase_client()
    
    result = (
        supabase
        .table('chatbot_configs')
        .select('api_key')
        .eq('user_id', user_id)
        .eq('chatbot_title', chatbot_title)
        .eq('is_active', True)   # keep same check for active key
        .execute()
    )
    
    if not result.data:
        return None
    
    return result.data[0]['api_key']