import logging
from typing import Optional
from fastapi import APIRouter, Request, Response
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService
from app.core.config import settings

router = APIRouter()
service = TransliterationService()


def _no_cache_headers(resp: Response):
    if resp is None:
        return
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"


@router.get("/health")
async def health():
    cache_stats = service.cache.stats() if service.cache else {"size": 0, "hits": 0, "misses": 0}
    base_present = bool(settings.TRANSLITERATOR_BASE_URL)
    return {
        "ok": True,
        "transliterator_enabled": settings.TRANSLITERATOR_ENABLED,
        "transliterator_base_url_present": base_present,
        "cache_size": cache_stats["size"],
        "cache_hits": cache_stats["hits"],
        "cache_misses": cache_stats["misses"],
    }


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(req: TransliterateRequest, request: Request, response: Response):
    rid = getattr(request.state, "request_id", "n/a")
    suggestions, used_runner, cache_status = await service.transliterate(req.text, req.mode, req.limit, rid)
    response.headers["X-Transliterator-Used"] = "true" if used_runner else "false"
    response.headers["X-Transliterator-Cache"] = cache_status
    _no_cache_headers(response)
    return TransliterateResponse(success=True, suggestions=suggestions)


@router.get("/transliterate/suggest", response_model=TransliterateResponse)
async def transliterate_suggest(
    q: str,
    request: Request,
    response: Response,
    limit: int = 8,
    mode: str = "smart",
    context: Optional[str] = None,
    cursor: Optional[int] = None,
    prev: Optional[str] = None,  # Backwards compatibility
):
    """
    Enhanced suggest API with layered algorithm for context-aware Tamil suggestions.
    
    Returns Tamil transliteration suggestions with multiple layers:
    - Layer A: Core Transliteration (strict)
    - Layer B: Tamil Vowel Expansion
    - Layer C: Context-Aware Completion
    - Layer D: Frequency Ranking
    - Layer E: Heuristic Neighbors (smart mode only)
    - Layer F: Dedup + Final Ranker
    
    Parameters:
    - q: Roman input fragment (1-40 characters)
    - limit: Maximum suggestions (1-20, default: 8)
    - mode: "smart" (default) or "strict"
    - context: Full text around cursor for context-aware suggestions (optional)
    - cursor: Cursor position within context (optional)
    - prev: Previous context (backwards compatibility, maps to context)
    """
    from fastapi import HTTPException
    from app.suggestion_engine.engine import SuggestionEngine
    from app.suggestion_engine.types import SuggestionRequest
    
    rid = getattr(request.state, "request_id", "n/a")
    
    # Validate inputs
    if not q or len(q) < 1 or len(q) > 40:
        raise HTTPException(status_code=400, detail="q must be between 1 and 40 characters")
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    if mode not in ("smart", "strict"):
        raise HTTPException(status_code=400, detail="mode must be 'smart' or 'strict'")
    
    # Map old "spoken" mode to "smart" for backwards compatibility
    if mode == "spoken":
        mode = "smart"
    
    # Use prev as context if context not provided (backwards compatibility)
    if context is None and prev:
        context = prev
        cursor = len(prev) if prev else None
    
    try:
        # Initialize engine (singleton pattern - could be cached)
        engine = SuggestionEngine()
        
        # Build request
        suggest_request = SuggestionRequest(
            q=q,
            limit=limit,
            mode=mode,
            context=context,
            cursor=cursor,
            client_id=getattr(request.state, "client_id", None),
        )
        
        # Generate suggestions
        result = await engine.suggest(suggest_request, rid)
        
        if not result.success:
            if result.error:
                raise HTTPException(status_code=400, detail=result.error.get("message", "Invalid request"))
            raise HTTPException(status_code=500, detail="Internal error")
        
        # Set response headers
        _no_cache_headers(response)
        if result.meta:
            if "algorithm_version" in result.meta:
                response.headers["X-Algorithm-Version"] = result.meta["algorithm_version"]
            if "layers_used" in result.meta:
                response.headers["X-Layers-Used"] = ",".join(result.meta["layers_used"])
            if "cache_hits" in result.meta:
                cache_hits = result.meta["cache_hits"]
                response.headers["X-Cache-Hit-Core"] = str(cache_hits.get("core", False)).lower()
                response.headers["X-Cache-Hit-Final"] = str(cache_hits.get("final", False)).lower()
            if "total_time_ms" in result.meta:
                response.headers["X-Latency-Ms"] = str(int(result.meta["total_time_ms"]))
        
        # Convert to response format (backwards compatible)
        suggestions = result.suggestions
        
        # Log response
        suggestion_preview = []
        for s in suggestions[:5]:
            if isinstance(s, dict):
                word = s.get("word", "")
                score = s.get("score", 0.0)
                suggestion_preview.append(f"{word}({score:.2f})")
            else:
                suggestion_preview.append(str(s)[:20])
        
        logging.info(
            "suggest_api_response request_id=%s q=%s mode=%s success=%s count=%d suggestions=%s meta=%s",
            rid,
            q,
            mode,
            result.success,
            len(suggestions),
            suggestion_preview,
            result.meta,
        )
        
        return TransliterateResponse(success=True, suggestions=suggestions)
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"suggest_api_error request_id={rid} q={q} error={str(e)}", exc_info=True)
        _no_cache_headers(response)
        # Return empty suggestions on error rather than crashing
        error_result = TransliterateResponse(success=False, suggestions=[])
        logging.info(
            "suggest_api_response request_id=%s q=%s success=False count=0",
            rid,
            q,
        )
        return error_result
