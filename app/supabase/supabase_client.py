"""Supabase client configuration and utilities."""

from supabase import create_client, Client
from app.core.config import settings
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Supabase client wrapper with configuration."""
    
    def __init__(self):
        self._client: Optional[Client] = None
        self._admin_client: Optional[Client] = None
    
    @property
    def client(self) -> Client:
        """Get the standard Supabase client (with anon key)."""
        if self._client is None:
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_anon_key
            )
        return self._client
    
    @property
    def admin_client(self) -> Client:
        """Get the admin Supabase client (with service role key)."""
        if self._admin_client is None:
            self._admin_client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )
        return self._admin_client


# Global Supabase client instance
supabase_client = SupabaseClient()


def get_supabase_client() -> Client:
    """Get the standard Supabase client."""
    return supabase_client.client


def get_admin_supabase_client() -> Client:
    """Get the admin Supabase client."""
    return supabase_client.admin_client