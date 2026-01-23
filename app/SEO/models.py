from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime
from uuid import UUID

class GenerateFAQSRequest(BaseModel):
    url: str

class FAQItem(BaseModel):
    question: str
    answer: str

class FAQResult(BaseModel):
    page_url: str
    faqs: List[FAQItem]

class SEOJobResponse(BaseModel):
    id: str
    user_id: str
    target_url: str
    scope: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    tokens_used: int
    error_message: Optional[str]

class SEOJobDetailResponse(SEOJobResponse):
    results: List[FAQResult]
