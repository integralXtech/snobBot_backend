"""Supabase integration package."""

from .supabase_client import (
    get_supabase_client,
    get_admin_supabase_client,
    SupabaseClient,
    supabase_client
)

__all__ = [
    "get_supabase_client",
    "get_admin_supabase_client", 
    "SupabaseClient",
    "supabase_client"
]