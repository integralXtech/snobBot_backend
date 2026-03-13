from typing import Dict, Optional, Any, Tuple
from app.supabase import get_admin_supabase_client
import logging

logger = logging.getLogger(__name__)

class CreditManager:
    """
    Helper to manage user credits (checking and deducting) 
    from the user_usage_balances table.
    """
    
    COLUMNS = {
        "blog_ideas": "blog_ideas_credits",
        "blog_creation": "blog_creation_credits",
        "faq": "faq_credits",
        "messages": "chatbot_messages_credits",
        "training": "chatbot_training_credits"
    }

    # Based on Business Model & Pricing Logic PDF
    COSTS = {
        "blog_ideas": 5,
        "faq": 10,
        "blog_creation": 20,
        "messages": 1,
        "training": 1 # Per character, but usually handled by count
    }

    @staticmethod
    def get_balances(user_id: str) -> Optional[Dict]:
        supabase = get_admin_supabase_client()
        res = supabase.table("user_usage_balances").select("*").eq("user_id", user_id).execute()
        return res.data[0] if res.data else None

    @staticmethod
    def has_sufficient_credits(user_id: str, credit_type: str, amount: int = 1) -> Tuple[bool, str]:
        """
        Check if user has enough credits of a certain type.
        credit_type: one of 'blog_ideas', 'blog_creation', 'faq', 'messages', 'training'
        """
        balances = CreditManager.get_balances(user_id)
        if not balances:
            return False, "No credit record found for user."

        col_base = CreditManager.COLUMNS.get(credit_type)
        if not col_base:
            return False, f"Invalid credit type: {credit_type}"

        total = balances.get(f"{col_base}_total", 0)
        used = balances.get(f"{col_base}_used", 0)

        if (total - used) >= amount:
            return True, "Sufficient credits"
        
        return False, f"Insufficient {credit_type} credits. {total - used} remaining, need {amount}."

    @staticmethod
    def consume_credits(user_id: str, credit_type: str, amount: int = 1) -> bool:
        """
        Deduct/consume credits by incrementing the _used column.
        """
        supabase = get_admin_supabase_client()
        col_base = CreditManager.COLUMNS.get(credit_type)
        if not col_base:
            return False

        try:
            # We use an RPC for atomic increment of the 'used' column or just update
            # For simplicity, we fetch and update, but ideally use an RPC to avoid race conditions.
            balances = CreditManager.get_balances(user_id)
            if not balances:
                return False
            
            used_col = f"{col_base}_used"
            new_used = (balances.get(used_col, 0) or 0) + amount
            
            supabase.table("user_usage_balances").update({
                used_col: new_used
            }).eq("user_id", user_id).execute()
            
            return True
        except Exception as e:
            logger.error(f"Error consuming credits for {user_id}: {e}")
            return False

    @staticmethod
    def can_create_chatbot(user_id: str) -> Tuple[bool, str]:
        """Check if user can create another chatbot."""
        supabase = get_admin_supabase_client()
        
        balances = CreditManager.get_balances(user_id)
        if not balances:
            return False, "No balance record."
            
        allowed = balances.get("chatbot_count_allowed", 0)
        
        # Count current bots
        bots_res = supabase.table("chatbot_configs").select("id", count="exact").eq("user_id", user_id).execute()
        current_count = bots_res.count if bots_res.count is not None else 0
        
        if current_count < allowed:
            return True, "Allowed"
            
        return False, f"Maximum chatbot limit ({allowed}) reached."
