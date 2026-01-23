"""Helper functions for White-Label Agency logic."""
from typing import Dict, Optional, Any
from app.supabase import get_admin_supabase_client
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def get_agency_config_by_domain(domain: str) -> Optional[Dict[str, Any]]:
    """Retrieve agency branding configuration based on the host domain."""
    supabase = get_admin_supabase_client()
    try:
        result = (
            supabase.table("agencies")
            .select("*")
            .eq("custom_domain", domain)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error fetching agency by domain {domain}: {e}")
        return None

def get_user_agency_info(user_id: str) -> Optional[Dict[str, Any]]:
    """Get agency information for a specific user."""
    supabase = get_admin_supabase_client()
    try:
        # Get user with their agency_id
        user_res = (
            supabase.table("registered_users")
            .select("agency_id, user_type")
            .eq("id", user_id)
            .execute()
        )
        if not user_res.data or not user_res.data[0].get("agency_id"):
            return None
        
        agency_id = user_res.data[0]["agency_id"]
        
        # Get agency and its pool
        agency_res = (
            supabase.table("agencies")
            .select("*, agency_pools(*)")
            .eq("id", agency_id)
            .execute()
        )
        return agency_res.data[0] if agency_res.data else None
    except Exception as e:
        logger.error(f"Error fetching agency info for user {user_id}: {e}")
        return None

def check_usage_limits(user_id: str, chatbot_id: str = None) -> Dict[str, Any]:
    """
    Check both User and Agency limits before a request.
    Returns: { "allowed": bool, "reason": str, "agency_id": str }
    """
    supabase = get_admin_supabase_client()
    
    # 1. Check User Quota
    user_res = (
        supabase.table("registered_users")
        .select("message_quota, agency_id")
        .eq("id", user_id)
        .execute()
    )
    
    if not user_res.data:
        return {"allowed": False, "reason": "User not found"}
        
    user = user_res.data[0]
    agency_id = user.get("agency_id")
    
    if user["message_quota"] <= 0:
        return {"allowed": False, "reason": "User message quota reached", "agency_id": agency_id}
    
    # 2. Check Agency Pool
    if agency_id:
        pool_res = (
            supabase.table("agency_pools")
            .select("*")
            .eq("agency_id", agency_id)
            .execute()
        )
        
        if pool_res.data:
            pool = pool_res.data[0]
            # Agency tracking is "soft limit" - we don't stop service, but we'll track overage later.
            # However, for chatbots count, it might be a hard limit if specified.
            pass

    return {"allowed": True, "agency_id": agency_id}

async def track_and_log_usage(
    user_id: str, 
    chatbot_id: str, 
    type: str, 
    amount: int = 1,
    metadata: Dict = None
):
    """
    Core dual-tracking logic. Updates user quota and agency pool.
    """
    supabase = get_admin_supabase_client()
    
    # 1. Identify Agency
    user_res = (
        supabase.table("registered_users")
        .select("agency_id, message_quota")
        .eq("id", user_id)
        .execute()
    )
    if not user_res.data:
        return
        
    user = user_res.data[0]
    agency_id = user.get("agency_id")
    
    # 2. Update User Quota (Hard Limit)
    supabase.table("registered_users").update({
        "message_quota": max(0, user["message_quota"] - amount)
    }).eq("id", user_id).execute()
    
    # 3. Update Agency Pool (Soft Limit)
    if agency_id:
        pool_res = (
            supabase.table("agency_pools")
            .select("*")
            .eq("agency_id", agency_id)
            .execute()
        )
        
        if pool_res.data:
            pool = pool_res.data[0]
            current = pool["current_messages"]
            limit = pool["limit_messages"]
            
            update_data = {
                "current_messages": current + amount
            }
            
            # Overage Logic
            if current + amount > limit:
                # Increment overage counter
                overage_inc = (current + amount) - max(limit, current)
                update_data["overage_messages"] = pool["overage_messages"] + overage_inc
            
            supabase.table("agency_pools").update(update_data).eq("agency_id", agency_id).execute()

    # 4. Create Audit Log
    try:
        log_data = {
            "user_id": user_id,
            "agency_id": agency_id,
            "chatbot_id": chatbot_id,
            "type": type,
            "amount": amount,
            "created_at": datetime.utcnow().isoformat()
        }
        supabase.table("usage_logs").insert(log_data).execute()
    except Exception as e:
        logger.error(f"Error creating usage log: {e}")

def validate_api_key_v2(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Enhanced API key validation that also retrieves agency context.
    Returns: { user_id, chatbot_title, agency_id } or None
    """
    supabase = get_admin_supabase_client()
    
    # Get chatbot by API key
    bot_res = (
        supabase.table("chatbot_configs")
        .select("id, user_id, chatbot_title, agency_id")
        .eq("api_key", api_key)
        .execute()
    )
    
    if not bot_res.data:
        return None
        
    bot = bot_res.data[0]
    
    return {
        "chatbot_id": bot["id"],
        "user_id": bot["user_id"],
        "chatbot_title": bot["chatbot_title"],
        "agency_id": bot["agency_id"]
    }
