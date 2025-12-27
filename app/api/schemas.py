from pydantic import BaseModel, Field
from typing import List, Optional


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
