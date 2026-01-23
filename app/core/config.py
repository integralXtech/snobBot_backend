"""Application configuration using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional
import os


class Settings(BaseSettings):
    """Application settings."""
    
    # Supabase Configuration
    supabase_url: str = Field(..., validation_alias="SUPABASE_URL")
    supabase_anon_key: str = Field(..., validation_alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(..., validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    openai_api_key: str = Field(..., validation_alias="OPENAI_API_KEY")
    pinecone_api_key: str = Field(..., validation_alias="PINECONE_API_KEY")
    aws_access_key: str = Field(..., validation_alias="AWS_ACCESS_KEY")
    aws_secret_access_key: str = Field(..., validation_alias="AWS_SECRET_ACCESS_KEY")
    aws_bucket_name: str = Field(..., validation_alias="AWS_BUCKET_NAME")
    aws_region: str = Field(..., validation_alias="AWS_REGION")
    
    # Optional API Keys (for web scraping services)
    firecrawl_api_key: Optional[str] = Field(default=None, validation_alias="FIRECRAWL_API_KEY")
    spider_api_key: Optional[str] = Field(default=None, validation_alias="SPIDER_API_KEY")
    
    # Stripe Configuration
    stripe_test_secret_key: str = Field(..., validation_alias="STRIPE_TEST_SECRET_KEY")
    stripe_live_secret_key: str = Field(..., validation_alias="STRIPE_LIVE_SECRET_KEY")
    stripe_test_publishable_key: str = Field(..., validation_alias="STRIPE_TEST_PUBLISHABLE_KEY")
    stripe_live_publishable_key: str = Field(..., validation_alias="STRIPE_LIVE_PUBLISHABLE_KEY")
    stripe_webhook_secret: str = Field(..., validation_alias="STRIPE_WEBHOOK_SECRET")
    
    # App Configuration
    debug: bool = Field(default=False, validation_alias="DEBUG")
    environment: str = Field(default="production", validation_alias="ENVIRONMENT")
    
    # API Configuration
    api_prefix: str = Field(default="/api", validation_alias="API_PREFIX")
    cors_origins: str = Field(default='["*"]', validation_alias="CORS_ORIGINS")
    
    frontend_url: str = Field(..., validation_alias="FRONTEND_URL")
    backend_url: str = Field(..., validation_alias="BACKEND_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow"
    )

    @property
    def is_production(self) -> bool:
        """Helper to check if environment is production."""
        return self.environment.lower() == "production"

    @property
    def stripe_secret_key(self) -> str:
        """Return appropriate Stripe secret key based on environment."""
        return self.stripe_live_secret_key if self.is_production else self.stripe_test_secret_key
    
    @property
    def stripe_publishable_key(self) -> str:
        """Return appropriate Stripe publishable key based on environment."""
        return self.stripe_live_publishable_key if self.is_production else self.stripe_test_publishable_key


# Global settings instance
settings = Settings()