from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class TransliterateRequest(BaseModel):
    text: str = Field(..., max_length=64)
    mode: Optional[str] = "spoken"
    limit: int = 8


class Suggestion(BaseModel):
    word: str = Field(..., description="Tamil suggestion text")
    score: float = Field(..., ge=0.0, le=1.0)


class TransliterateResponse(BaseModel):
    success: bool = True
    suggestions: List[Suggestion]
    meta: Optional[Dict[str, Any]] = None
    
    class Config:
        # Exclude meta field from response when it's None
        exclude_none = True
