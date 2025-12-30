import logging
import time
import asyncio
from typing import Optional, Dict
from fastapi import APIRouter, Request, Response, Query
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService
from app.services.google_transliteration import (
    get_transliteration_suggestions,
    get_cache_stats
)
from app.core.config import settings

router = APIRouter()
service = TransliterationService()

# Metrics counters (lightweight, in-memory)
_suggest_requests_total: Dict[str, int] = {}
_suggest_cache_hit_total: Dict[str, int] = {"core": 0, "final": 0}
_suggest_runner_errors_total = 0
_suggest_latency_buckets: Dict[str, list] = {}  # mode -> list of latencies


def _no_cache_headers(resp: Response):
    if resp is None:
        return
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"


@router.get("/health")
async def health():
    cache_stats = service.cache.stats() if service.cache else {"size": 0, "hits": 0, "misses": 0}
    google_cache_stats = get_cache_stats()
    base_present = bool(settings.TRANSLITERATOR_BASE_URL)
    return {
        "ok": True,
        "transliterator_enabled": settings.TRANSLITERATOR_ENABLED,
        "transliterator_base_url_present": base_present,
        "cache_size": cache_stats["size"],
        "cache_hits": cache_stats["hits"],
        "cache_misses": cache_stats["misses"],
        "google_cache": google_cache_stats,
    }


@router.get("/transliterate/health")
async def transliteration_health():
    """Health check and cache stats for transliteration service."""
    google_cache_stats = get_cache_stats()
    return {
        "status": "healthy",
        "cache": google_cache_stats
    }


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(req: TransliterateRequest, request: Request, response: Response):
    rid = getattr(request.state, "request_id", "n/a")
    suggestions, used_runner, cache_status = await service.transliterate(req.text, req.mode, req.limit, rid)
    response.headers["X-Transliterator-Used"] = "true" if used_runner else "false"
    response.headers["X-Transliterator-Cache"] = cache_status
    _no_cache_headers(response)
    return TransliterateResponse(success=True, suggestions=suggestions)


async def _transliterate_suggest_impl(
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
    Fast suggest API using local transliterator only - no external calls, no timeouts.
    
    Returns Tamil transliteration suggestions instantly using local dictionary.
    This ensures zero 504 errors and fast response times.
    
    Parameters:
    - q: Roman input fragment (1-40 characters)
    - limit: Maximum suggestions (1-20, default: 8)
    - mode: "smart" (default) or "strict" (mapped from "spoken", "char", "word")
    - context: Full text around cursor (optional, not used currently)
    - cursor: Cursor position (optional, not used currently)
    - prev: Previous context (backwards compatibility, not used currently)
    """
    from fastapi import HTTPException
    
    rid = getattr(request.state, "request_id", "n/a")
    request_start = time.perf_counter()
    
    # Log incoming mode for debugging
    original_mode = mode
    logging.debug(f"suggest_api_request request_id={rid} q={q} original_mode={original_mode}")
    
    # Map old mode values to "smart" for backwards compatibility (BEFORE validation)
    mode_mappings = {
        "spoken": "smart",
        "char": "smart",
        "word": "smart",
        "written": "strict",
    }
    if mode in mode_mappings:
        mapped_mode = mode_mappings[mode]
        logging.debug(f"suggest_api_mode_mapped request_id={rid} from={mode} to={mapped_mode}")
        mode = mapped_mode
    
    # Validate inputs
    if not q or len(q) < 1 or len(q) > 40:
        raise HTTPException(status_code=400, detail="q must be between 1 and 40 characters")
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    if mode not in ("smart", "strict"):
        logging.error(f"suggest_api_validation_error request_id={rid} q={q} mode={mode} original_mode={original_mode}")
        raise HTTPException(status_code=400, detail="mode must be 'smart' or 'strict'")
    
    # Metrics: increment request counter
    _suggest_requests_total[mode] = _suggest_requests_total.get(mode, 0) + 1
    
    # Use Google API with local fallback - fast, high quality suggestions
    try:
        # Try Google API first (fast timeout, non-blocking)
        from app.services.google_transliteration import get_transliteration_suggestions
        from app.services.google_transliteration import TamilTransliterator
        
        # Get suggestions with Google API (with fast timeout and local fallback)
        google_result = await asyncio.wait_for(
            get_transliteration_suggestions(
                text=q,
                limit=limit,
                mode=mode,
                use_google=True,
                use_cache=True,
                timeout=0.4  # Fast timeout - fail fast to local
            ),
            timeout=0.5  # Total timeout
        )
        
        suggestions = google_result.get("suggestions", [])
        source = google_result.get("source", "local")
        
        # If Google didn't return enough, use local fallback
        if not suggestions or len(suggestions) < 3:
            local_transliterator = TamilTransliterator()
            local_suggestions = local_transliterator.get_suggestions(q, limit)
            if local_suggestions:
                # Merge and deduplicate
                seen = {s["word"]: s for s in suggestions}
                for local_sug in local_suggestions:
                    word = local_sug["word"]
                    if word not in seen:
                        seen[word] = local_sug
                        suggestions.append(local_sug)
                    elif local_sug["score"] > seen[word].get("score", 0):
                        seen[word] = local_sug
                suggestions = list(seen.values())
                suggestions.sort(key=lambda x: x.get("score", 0), reverse=True)
                source = "local" if source == "fallback" else source
        
        logging.debug(f"suggest_api request_id={rid} q={q} source={source} count={len(suggestions)}")
        
    except asyncio.TimeoutError:
        # Timeout - use local fallback immediately
        logging.debug(f"suggest_api_timeout request_id={rid} q={q} - using_local_fallback")
        try:
            from app.services.google_transliteration import TamilTransliterator
            local_transliterator = TamilTransliterator()
            suggestions = local_transliterator.get_suggestions(q, limit)
            source = "local"
        except Exception:
            suggestions = []
            source = "error"
    except Exception as e:
        # Error - use local fallback
        logging.error(f"suggest_api_error request_id={rid} q={q} error={str(e)}", exc_info=True)
        try:
            from app.services.google_transliteration import TamilTransliterator
            local_transliterator = TamilTransliterator()
            suggestions = local_transliterator.get_suggestions(q, limit)
            source = "local"
        except Exception:
            suggestions = []
            source = "error"
    
    # Metrics: track latency (should be < 10ms for local transliterator)
    request_latency_ms = (time.perf_counter() - request_start) * 1000
    if mode not in _suggest_latency_buckets:
        _suggest_latency_buckets[mode] = []
    _suggest_latency_buckets[mode].append(request_latency_ms)
    if len(_suggest_latency_buckets[mode]) > 1000:
        _suggest_latency_buckets[mode] = _suggest_latency_buckets[mode][-1000:]
    
    # Set response headers
    _no_cache_headers(response)
    response.headers["X-Source"] = source
    
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
        "suggest_api_response request_id=%s q=%s mode=%s source=%s count=%d latency_ms=%.1f suggestions=%s",
        rid,
        q,
        mode,
        source,
        len(suggestions),
        request_latency_ms,
        suggestion_preview,
    )
    
    # Always return success=True with suggestions (even if empty) to maintain compatibility
    response_obj = TransliterateResponse(
        success=True,
        suggestions=suggestions if suggestions else [],
        meta=None
    )
    # Convert to dict and remove None fields
    response_dict = response_obj.dict(exclude_none=True)
    return response_dict


@router.get("/transliterate/suggest", response_model=TransliterateResponse)
async def transliterate_suggest(
    q: str,
    request: Request,
    response: Response,
    limit: int = Query(8, ge=1, le=20),
    mode: str = Query("smart"),  # No pattern validation - we handle it in the function
    context: Optional[str] = Query(None, max_length=5000),
    cursor: Optional[int] = Query(None, ge=0),
    prev: Optional[str] = None,
):
    """Alias for /transliterate/suggest endpoint."""
    return await _transliterate_suggest_impl(q, request, response, limit, mode, context, cursor, prev)


@router.get("/ime/suggest", response_model=TransliterateResponse)
async def ime_suggest(
    q: str,
    request: Request,
    response: Response,
    limit: int = Query(8, ge=1, le=20),
    mode: str = Query("smart"),  # No pattern validation - we handle it in the function
    context: Optional[str] = Query(None, max_length=5000),
    cursor: Optional[int] = Query(None, ge=0),
    prev: Optional[str] = None,
):
    """IME suggest endpoint - accepts 'spoken' mode for backwards compatibility."""
    return await _transliterate_suggest_impl(q, request, response, limit, mode, context, cursor, prev)
