"""
Type definitions for the suggestion engine.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class Candidate:
    """A single suggestion candidate with metadata."""
    word: str
    base_score: float
    source_layer: str
    debug: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        """Convert to API response format."""
        result = {
            "word": self.word,
            "score": self.base_score,
        }
        if self.debug:
            result["_debug"] = self.debug
        return result


@dataclass
class SuggestionRequest:
    """Request parameters for suggestion generation."""
    q: str
    limit: int = 8
    mode: str = "smart"  # "smart" or "strict"
    context: Optional[str] = None
    cursor: Optional[int] = None
    lang: Optional[str] = None
    client_id: Optional[str] = None
    project_id: Optional[str] = None


@dataclass
class SuggestionResponse:
    """Response with suggestions and metadata."""
    success: bool
    suggestions: List[Dict[str, Any]]
    meta: Dict[str, Any]
    error: Optional[Dict[str, Any]] = None

