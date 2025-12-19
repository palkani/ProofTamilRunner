from pydantic import BaseModel, Field
from typing import List, Optional


class TransliterateRequest(BaseModel):
  text: str = Field(..., max_length=64)
  mode: Optional[str] = "spoken"
  limit: int = 8


class Suggestion(BaseModel):
  word: str
  ta: str
  score: float


class TransliterateResponse(BaseModel):
  success: bool = True
  suggestions: List[Suggestion]

