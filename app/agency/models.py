from pydantic import BaseModel
from typing import Optional, Dict, Any

class AgencyBrandingUpdate(BaseModel):
    company_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    logo_url: Optional[str] = None
    branding_settings: Optional[Dict[str, Any]] = None

class AgencyDomainUpdate(BaseModel):
    custom_domain: str

class AgencySettingsResponse(BaseModel):
    id: str
    name: str
    company_name: Optional[str]
    custom_domain: Optional[str]
    logo_url: Optional[str]
    primary_color: Optional[str]
    secondary_color: Optional[str]
    branding_settings: Optional[Dict[str, Any]]

class AgencyPlanUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    interval: Optional[str] = None # 'month' or 'year'
    
    # Granular Limits
    limit_chatbots: Optional[int] = None
    limit_messages: Optional[int] = None
    limit_training_chars: Optional[int] = None
    limit_blog_creation: Optional[int] = None
    limit_blog_ideas: Optional[int] = None
    limit_faqs: Optional[int] = None
    
    is_active: Optional[bool] = None

class AgencyPlanCreate(BaseModel):
    name: str
    price: float
    description: Optional[str] = ""
    interval: str = "month" # Default to month
    
    # Granular Limits are REQUIRED for creation to avoid null states
    limit_chatbots: int = 1
    limit_messages: int = 1000
    limit_training_chars: int = 100000
    limit_blog_creation: int = 0
    limit_blog_ideas: int = 0
    limit_faqs: int = 0
    
    is_active: bool = True

class AgencyPlanResponse(AgencyPlanCreate):
    id: str
    agency_id: str
    currency: str = "USD"
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

# --- Top Up Models ---

class AgencyTopUpCreate(BaseModel):
    name: str
    price: float
    credit_type: str # 'messages', 'characters', etc.
    credit_amount: int

class AgencyTopUpUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    credit_type: Optional[str] = None
    credit_amount: Optional[int] = None

class AgencyTopUpResponse(AgencyTopUpCreate):
    id: str
    agency_id: str
    created_at: Optional[Any] = None

class CustomerCreate(BaseModel):
    name: str
    email: str
    password: str
    plan_id: Optional[str] = None # Now links to agency_plans.id

class TicketCreate(BaseModel):
    subject: str
    message: str
    priority: str = "medium"
    ticket_type: str = "client_to_agency" # 'agency_to_platform' or 'client_to_agency'
