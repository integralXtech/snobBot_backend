from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime
from uuid import UUID

# Ideation Models
class BlogIdeationRequest(BaseModel):
    url: str

class BlogIdeationResponse(BaseModel):
    id: str  # DB ID
    titles: List[str]
    tokens_used: int

# Generation Models
class BlogGenerationRequest(BaseModel):
    title: str
    custom_prompt: Optional[str] = None

class GeneratedBlog(BaseModel):
    title: str
    description: str
    meta_description: str
    keywords: List[str]
    content: str # Markdown

class BlogGenerationResponse(GeneratedBlog):
    id: str # DB ID
    tokens_used: int
    created_at: datetime
    
class BlogHistoryItem(BaseModel):
    id: str
    title: str
    description: str
    meta_description: str
    keywords: List[str]
    content: str
    tokens_used: int
    created_at: datetime
