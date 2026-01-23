"""Database helper functions for website_links table."""
from typing import List, Dict, Optional
from datetime import datetime


def upsert_discovered_links(
    user_id: str,
    chatbot_title: str,
    links: List[Dict[str, str]]
) -> Dict:
    """
    Insert or update discovered links in Supabase.
    
    Args:
        user_id: User ID
        chatbot_title: Chatbot title
        links: List of dicts with 'url' and optional 'title'
        
    Returns:
        Dict with count of inserted/updated links
    """
    from app.supabase import get_admin_supabase_client
    
    supabase = get_admin_supabase_client()
    chatbot_id = chatbot_title.lower()
    
    inserted_count = 0
    updated_count = 0
    
    for link in links:
        url = link.get('url')
        title = link.get('title')
        
        if not url:
            continue
            
        # Try to insert, on conflict update last_updated
        data = {
            'user_id': user_id,
            'chatbot_id': chatbot_id,
            'url': url,
            'title': title,
            'last_updated': datetime.utcnow().isoformat()
        }
        
        try:
            # Check if exists
            existing = (
                supabase.table('website_links')
                .select('id')
                .eq('user_id', user_id)
                .eq('chatbot_id', chatbot_id)
                .eq('url', url)
                .execute()
            )
            
            if existing.data:
                # Update existing
                supabase.table('website_links').update({
                    'title': title,
                    'last_updated': datetime.utcnow().isoformat()
                }).eq('id', existing.data[0]['id']).execute()
                updated_count += 1
            else:
                # Insert new
                supabase.table('website_links').insert(data).execute()
                inserted_count += 1
                
        except Exception as e:
            print(f"Error upserting link {url}: {e}")
            continue
    
    return {
        'inserted': inserted_count,
        'updated': updated_count,
        'total': inserted_count + updated_count
    }


def get_links_for_chatbot(
    user_id: str,
    chatbot_title: str,
    only_uncrawled: bool = False
) -> List[Dict]:
    """
    Retrieve all links for a chatbot.
    
    Args:
        user_id: User ID
        chatbot_title: Chatbot title
        only_uncrawled: If True, only return links where is_crawled=False
        
    Returns:
        List of link dictionaries
    """
    from app.supabase import get_admin_supabase_client
    
    supabase = get_admin_supabase_client()
    chatbot_id = chatbot_title.lower()
    
    query = (
        supabase.table('website_links')
        .select('*')
        .eq('user_id', user_id)
        .eq('chatbot_id', chatbot_id)
    )
    
    if only_uncrawled:
        query = query.eq('is_crawled', False)
    
    result = query.execute()
    return result.data if result.data else []


def mark_link_as_crawled(
    user_id: str,
    chatbot_title: str,
    url: str
) -> bool:
    """
    Mark a link as crawled in the database.
    
    Args:
        user_id: User ID
        chatbot_title: Chatbot title
        url: URL to mark as crawled
        
    Returns:
        True if successful, False otherwise
    """
    from app.supabase import get_admin_supabase_client
    
    supabase = get_admin_supabase_client()
    chatbot_id = chatbot_title.lower()
    
    try:
        result = (
            supabase.table('website_links')
            .update({
                'is_crawled': True,
                'last_updated': datetime.utcnow().isoformat()
            })
            .eq('user_id', user_id)
            .eq('chatbot_id', chatbot_id)
            .eq('url', url)
            .execute()
        )
        
        return bool(result.data)
    except Exception as e:
        print(f"Error marking link as crawled: {e}")
        return False


def get_crawl_stats(user_id: str, chatbot_title: str) -> Dict:
    """
    Get crawling statistics for a chatbot.
    
    Args:
        user_id: User ID
        chatbot_title: Chatbot title
        
    Returns:
        Dict with total, crawled, and pending counts
    """
    from app.supabase import get_admin_supabase_client
    
    supabase = get_admin_supabase_client()
    chatbot_id = chatbot_title.lower()
    
    # Get all links
    all_links = (
        supabase.table('website_links')
        .select('is_crawled')
        .eq('user_id', user_id)
        .eq('chatbot_id', chatbot_id)
        .execute()
    )
    
    total = len(all_links.data) if all_links.data else 0
    crawled = sum(1 for link in (all_links.data or []) if link.get('is_crawled'))
    
    return {
        'total_links': total,
        'crawled': crawled,
        'pending': total - crawled
    }


# ---------------------------------------------------------
# CONVERSATION & MESSAGE HELPERS
# ---------------------------------------------------------

def create_conversation(user_id: str, chatbot_title: str, title: Optional[str] = None) -> Dict:
    """Create a new conversation thread."""
    from app.supabase import get_admin_supabase_client
    supabase = get_admin_supabase_client()
    
    chatbot_id = chatbot_title.lower()
    
    data = {
        "user_id": user_id,
        "chatbot_id": chatbot_id,
        "title": title or "New Conversation"
    }
    
    result = supabase.table("conversations").insert(data).execute()
    return result.data[0] if result.data else None


def add_message(conversation_id: str, sender: str, content: str) -> Dict:
    """Add a message to a conversation."""
    from app.supabase import get_admin_supabase_client
    supabase = get_admin_supabase_client()
    
    # Insert message
    msg_data = {
        "conversation_id": conversation_id,
        "sender": sender,
        "content": content
    }
    result = supabase.table("messages").insert(msg_data).execute()
    
    # Update conversation's updated_at timestamp
    supabase.table("conversations").update({
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", conversation_id).execute()
    
    return result.data[0] if result.data else None


def get_chatbot_conversations(user_id: str, chatbot_title: str) -> List[Dict]:
    """Get all conversations for a specific chatbot, ordered by most recent."""
    from app.supabase import get_admin_supabase_client
    supabase = get_admin_supabase_client()
    
    chatbot_id = chatbot_title.lower()
    
    result = (
        supabase.table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .eq("chatbot_id", chatbot_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return result.data if result.data else []


def get_conversation_messages(user_id: str, conversation_id: str) -> List[Dict]:
    """Get full message history for a conversation."""
    from app.supabase import get_admin_supabase_client
    supabase = get_admin_supabase_client()
    
    # First verify ownership
    conv_check = (
        supabase.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not conv_check.data:
        return []

    result = (
        supabase.table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False) # Oldest first
        .execute()
    )
    return result.data if result.data else []

