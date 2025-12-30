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
    request_start = time.perf_counter()
    
    # Log incoming mode for debugging
    original_mode = mode
    logging.debug(f"suggest_api_request request_id={rid} q={q} original_mode={original_mode}")
    
    # Map old mode values to "smart" for backwards compatibility (BEFORE validation)
    mode_mappings = {
        "spoken": "smart",
        "char": "smart",  # Handle mode=char from frontend
        "word": "smart",  # Handle mode=word from frontend
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
    
    # Metrics: increment request counter (after mode mapping)
    _suggest_requests_total[mode] = _suggest_requests_total.get(mode, 0) + 1
    
    # Use prev as context if context not provided (backwards compatibility)
    if context is None and prev:
        context = prev
        cursor = len(prev) if prev else None
    
    try:
        # Try Google API first for better quality suggestions (with shorter timeout)
        google_result = await get_transliteration_suggestions(
            text=q,
            limit=limit,
            mode=mode,
            use_google=True,
            use_cache=True,
            timeout=1.5  # Reduced timeout to fail fast
        )
        
        suggestions = google_result.get("suggestions", [])
        source = google_result.get("source", "unknown")
        
        # If Google didn't return good results, try local fallback first (fast, no external calls)
        if not suggestions or len(suggestions) == 0:
            logging.debug(f"suggest_api_fallback request_id={rid} q={q} google_source={source} trying_local_fallback")
            
            # Use local fallback transliterator (no external API calls, instant, no timeouts)
            try:
                from app.services.google_transliteration import TamilTransliterator
                local_transliterator = TamilTransliterator()
                local_suggestions = local_transliterator.get_suggestions(q, limit)
                
                if local_suggestions and len(local_suggestions) > 0:
                    suggestions = local_suggestions
                    source = "local_fallback"
                    logging.debug(f"suggest_api_local_success request_id={rid} q={q} count={len(suggestions)}")
            except Exception as local_error:
                logging.debug(f"suggest_api_local_error request_id={rid} q={q} error={local_error}")
                # Continue to engine fallback
            
            # Only try engine if local fallback didn't work (with strict timeout protection)
            if not suggestions or len(suggestions) == 0:
                logging.debug(f"suggest_api_engine_fallback request_id={rid} q={q} trying_engine")
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
                    
                    # Generate suggestions from existing engine with strict timeout protection
                    # Use shorter timeout to fail fast and avoid "Runner request timed out" errors
                    result = await asyncio.wait_for(
                        engine.suggest(suggest_request, rid),
                        timeout=1.5  # Reduced to 1.5 seconds to fail fast
                    )
                    
                    if result.success and result.suggestions:
                        suggestions = result.suggestions
                        source = "engine"
                        
                        # Metrics: track cache hits and runner errors
                        if result.meta:
                            cache_hits = result.meta.get("cache_hits", {})
                            if cache_hits.get("core", False):
                                _suggest_cache_hit_total["core"] += 1
                            if cache_hits.get("final", False):
                                _suggest_cache_hit_total["final"] += 1
                            if result.meta.get("runner_error", False):
                                global _suggest_runner_errors_total
                                _suggest_runner_errors_total += 1
                except asyncio.TimeoutError:
                    logging.warning(f"suggest_api_engine_timeout request_id={rid} q={q} engine_timed_out - using_empty")
                    # Return empty suggestions - local fallback already tried
                    suggestions = []
                    source = "timeout"
                except Exception as engine_error:
                    error_msg = str(engine_error)
                    # Check if it's a runner timeout error
                    if "timeout" in error_msg.lower() or "Runner request timed out" in error_msg:
                        logging.warning(f"suggest_api_runner_timeout request_id={rid} q={q} runner_timeout - using_empty")
                    else:
                        logging.error(f"suggest_api_engine_error request_id={rid} q={q} error={engine_error}")
                    # Return empty suggestions - local fallback already tried
                    suggestions = []
                    source = "error"
        else:
            # Google provided suggestions - log success
            logging.debug(f"suggest_api_google_success request_id={rid} q={q} source={source} count={len(suggestions)}")
        
        # Metrics: track latency
        request_latency_ms = (time.perf_counter() - request_start) * 1000
        if mode not in _suggest_latency_buckets:
            _suggest_latency_buckets[mode] = []
        _suggest_latency_buckets[mode].append(request_latency_ms)
        # Keep only last 1000 measurements per mode
        if len(_suggest_latency_buckets[mode]) > 1000:
            _suggest_latency_buckets[mode] = _suggest_latency_buckets[mode][-1000:]
        
        # Set response headers
        _no_cache_headers(response)
        response.headers["X-Source"] = source
        if google_result.get("cached"):
            response.headers["X-Cache-Hit"] = "true"
        if "ms" in google_result:
            response.headers["X-Latency-Ms"] = str(int(google_result["ms"]))
        
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
            "suggest_api_response request_id=%s q=%s mode=%s source=%s count=%d suggestions=%s",
            rid,
            q,
            mode,
            source,
            len(suggestions),
            suggestion_preview,
        )
        
        # Return response without meta field (exclude None fields) - 100% backward compatible
        response_obj = TransliterateResponse(
            success=True,
            suggestions=suggestions,
            meta=None
        )
        # Convert to dict and remove None fields
        response_dict = response_obj.dict(exclude_none=True)
        return response_dict
        
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        logging.warning(f"suggest_api_timeout request_id={rid} q={q} overall_timeout")
        _no_cache_headers(response)
        # Return empty suggestions on timeout (success=True to maintain compatibility)
        error_result = TransliterateResponse(success=True, suggestions=[])
        logging.info(
            "suggest_api_response request_id=%s q=%s success=True count=0 timeout=True",
            rid,
            q,
        )
        return error_result.dict(exclude_none=True)
    except Exception as e:
        logging.error(f"suggest_api_error request_id={rid} q={q} error={str(e)}", exc_info=True)
        _no_cache_headers(response)
        # Return empty suggestions on error (success=True to maintain compatibility)
        # This prevents frontend from breaking
        error_result = TransliterateResponse(success=True, suggestions=[])
        logging.info(
            "suggest_api_response request_id=%s q=%s success=True count=0 error_handled=True",
            rid,
            q,
        )
        return error_result.dict(exclude_none=True)


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
