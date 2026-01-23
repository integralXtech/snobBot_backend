"""
Simple token tracking - all 5 categories in separate columns.
One row per bot with all token counts.
"""

from app.supabase import get_admin_supabase_client

# Column mapping for operation types
OPERATION_COLUMNS = {
    "file_upload": "file_upload_tokens",      # PDF/DOCX/TXT - embedding tokens
    "raw_text": "raw_text_tokens",            # Raw text uploads - embedding tokens  
    "qa_pairs": "qa_pairs_tokens",            # QA pairs uploads - embedding tokens
    "web_crawl": "web_crawl_tokens",          # Web crawling results - embedding tokens
    "ask_query": "ask_query_tokens"           # Ask endpoint responses - LLM completion tokens
}


def initialize_bot_tokens(user_id: str, chatbot_title: str):
    """
    Initialize bot with all 5 categories set to 0 tokens and 0 query count.
    """
    supabase = get_admin_supabase_client()
    chatbot_title = chatbot_title.lower()
    
    try:
        # Check if bot already exists
        existing = (
            supabase.table("bot_token_usage")
            .select("id")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )
        
        if not existing.data:
            # Create new bot record with all tokens = 0 and query_count = 0
            supabase.table("bot_token_usage").insert({
                "user_id": user_id,
                "chatbot_title": chatbot_title,
                "file_upload_tokens": 0,
                "raw_text_tokens": 0,
                "qa_pairs_tokens": 0,
                "web_crawl_tokens": 0,
                "ask_query_tokens": 0,
                "query_count": 0
            }).execute()
            
            return {
                "success": True,
                "message": f"Initialized bot {chatbot_title} with all categories"
            }
        else:
            return {
                "success": True,
                "message": f"Bot {chatbot_title} already exists"
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def update_tokens(user_id: str, chatbot_title: str, operation_type: str, tokens_used: int):
    """
    Update tokens for a specific operation type.
    Add new tokens to existing count in the specific column.
    For ask_query operations, also increment the query_count.
    """
    supabase = get_admin_supabase_client()
    chatbot_title = chatbot_title.lower()
    
    try:
        # First ensure bot exists (initialize if needed)
        initialize_bot_tokens(user_id, chatbot_title)
        
        # Get the column name for this operation
        column_name = OPERATION_COLUMNS.get(operation_type)
        if not column_name:
            return {
                "success": False,
                "error": f"Invalid operation type: {operation_type}"
            }
        
        # Get current tokens for this operation and query_count
        existing = (
            supabase.table("bot_token_usage")
            .select(column_name, "query_count")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )
        
        if existing.data:
            current_tokens = existing.data[0][column_name]
            new_total = current_tokens + tokens_used
            
            # Prepare update data
            update_data = {column_name: new_total}
            
            # If this is an ask_query operation, also increment query_count
            if operation_type == "ask_query":
                current_queries = existing.data[0]["query_count"]
                update_data["query_count"] = current_queries + 1
            
            # Update the database
            supabase.table("bot_token_usage").update(update_data).eq("user_id", user_id).eq("chatbot_title", chatbot_title).execute()
            
            return {
                "success": True,
                "operation": operation_type,
                "previous_tokens": current_tokens,
                "tokens_added": tokens_used,
                "new_total": new_total,
                "query_count_incremented": operation_type == "ask_query"
            }
        else:
            # This shouldn't happen if initialization worked
            return {
                "success": False,
                "error": f"No record found for bot {chatbot_title}"
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_bot_tokens(user_id: str, chatbot_title: str):
    """Get all token counts and query count for a specific bot."""
    supabase = get_admin_supabase_client()
    chatbot_title = chatbot_title.lower()
    
    try:
        # Initialize bot first if it doesn't exist
        initialize_bot_tokens(user_id, chatbot_title)
        
        # Get bot record with all token columns and query_count
        result = (
            supabase.table("bot_token_usage")
            .select("file_upload_tokens, raw_text_tokens, qa_pairs_tokens, web_crawl_tokens, ask_query_tokens, query_count")
            .eq("user_id", user_id)
            .eq("chatbot_title", chatbot_title)
            .execute()
        )
        
        if result.data:
            row = result.data[0]
            operations = {
                "file_upload": row["file_upload_tokens"],
                "raw_text": row["raw_text_tokens"],
                "qa_pairs": row["qa_pairs_tokens"],
                "web_crawl": row["web_crawl_tokens"],
                "ask_query": row["ask_query_tokens"]
            }
            total = sum(operations.values())
            
            return {
                "chatbot_title": chatbot_title,
                "operations": operations,
                "total_tokens": total,
                "query_count": row["query_count"]
            }
        else:
            return {"error": f"Bot {chatbot_title} not found"}
        
    except Exception as e:
        return {"error": str(e)}


def get_user_total_tokens(user_id: str):
    """Get total tokens and total queries across all bots for a user."""
    supabase = get_admin_supabase_client()
    
    try:
        result = (
            supabase.table("bot_token_usage")
            .select("chatbot_title, file_upload_tokens, raw_text_tokens, qa_pairs_tokens, web_crawl_tokens, ask_query_tokens, query_count")
            .eq("user_id", user_id)
            .execute()
        )
        
        bots = {}
        total_tokens = 0
        total_queries = 0
        
        for row in result.data:
            bot = row["chatbot_title"]
            operations = {
                "file_upload": row["file_upload_tokens"],
                "raw_text": row["raw_text_tokens"],
                "qa_pairs": row["qa_pairs_tokens"],
                "web_crawl": row["web_crawl_tokens"],
                "ask_query": row["ask_query_tokens"]
            }
            bot_total_tokens = sum(operations.values())
            bot_query_count = row["query_count"]
            
            bots[bot] = {
                "operations": operations,
                "total_tokens": bot_total_tokens,
                "query_count": bot_query_count
            }
            total_tokens += bot_total_tokens
            total_queries += bot_query_count
        
        return {
            "user_id": user_id,
            "bots": bots,
            "total_tokens_all_bots": total_tokens,
            "total_queries_all_bots": total_queries
        }
        
    except Exception as e:
        return {"error": str(e)}