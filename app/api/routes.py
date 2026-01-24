import logging
import time
from typing import Optional, Dict
from fastapi import APIRouter, Request, Response, Query, HTTPException
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService
from app.services.google_transliteration import get_cache_stats, TamilTransliterator
from app.services.suggest_service import SuggestService
from app.core.config import settings

router = APIRouter()
service = TransliterationService()

# Suggest service singleton (used by /transliterate/suggest)
_suggest_service: Optional[SuggestService] = None


def get_suggest_service() -> SuggestService:
    global _suggest_service
    if _suggest_service is None:
        _suggest_service = SuggestService()
    return _suggest_service

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
    
    # Normalize/mapping for compatibility:
    # - UI uses: spoken | formal | academic
    # - Older clients used: smart | strict | written | char | word
    mode = (mode or "").strip().lower()
    mode_mappings = {
        "smart": "spoken",
        "strict": "formal",
        "written": "formal",
        "char": "spoken",
        "word": "spoken",
    }
    if mode in mode_mappings:
        mapped_mode = mode_mappings[mode]
        logging.debug(f"suggest_api_mode_mapped request_id={rid} from={mode} to={mapped_mode}")
        mode = mapped_mode
    if mode not in ("spoken", "formal", "academic"):
        mode = "spoken"
    
    # Validate inputs
    if not q or len(q) < 1 or len(q) > 40:
        raise HTTPException(status_code=400, detail="q must be between 1 and 40 characters")
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    # Mode is now normalized to spoken/formal/academic
    
    # Metrics: increment request counter
    _suggest_requests_total[mode] = _suggest_requests_total.get(mode, 0) + 1
    
    # Use local IME variant generation (no external dependencies, richer candidate list),
    # but preserve canonical/common-word correctness for high-signal tokens like "tamil".
    try:
        from app.services.canonical_map import get_canonical
        from app.services.tamil_linguistics import normalize_roman_input
        from app.services.corpus_db import get_corpus

        norm_q = normalize_roman_input(q)

        suggestions = []
        source = "local-ime"

        # 1) Canonical override (ensures common tokens are ALWAYS correct)
        canonical_word, is_canonical = get_canonical(norm_q)
        if is_canonical and canonical_word:
            # For some high-signal tokens, return ONLY the canonical word to avoid confusing junk.
            strict_only = {
                "tamil", "thamizh", "thamiz", "tamizh", "tamiz",
                "enna",
                "namma",
                "enathu", "enadu", "enadhu",
                "therkku", "therku", "therkk",
            }
            if norm_q in strict_only:
                suggestions = [{"word": canonical_word, "score": 1.0}]
                source = "canonical"
            else:
                suggestions.append({"word": canonical_word, "score": 1.0})
                source = "canonical+local-ime"

        # 2) Local common-word transliterator (good related words)
        if source != "canonical":
            try:
                local = TamilTransliterator().get_suggestions(norm_q, limit)
                if local:
                    suggestions.extend(local)
            except Exception:
                # non-fatal
                pass

        # 2.5) Corpus suggestions from Postgres (high quality for real words + alternates)
        try:
            corpus = get_corpus()
            # prev is optional Tamil context; only boosts if provided
            corpus_words = corpus.suggest_words(norm_q, limit=min(10, limit), prev_tamil=prev)
            if corpus_words:
                suggestions.extend([{"word": w, "score": sc} for (w, sc) in corpus_words])
                if source == "local-ime":
                    source = "corpus+local-ime"
        except Exception:
            pass

        # 3) IME variant generation (broad coverage)
        if source != "canonical":
            ime_suggestions = await service.generate_ime_suggestions(norm_q, limit=limit, mode=mode)
            if ime_suggestions:
                suggestions.extend(ime_suggestions)

        # 3.5) Phrase completions (if corpus has phrases starting with top word)
        try:
            if suggestions:
                top_word = (suggestions[0].get("word") if isinstance(suggestions[0], dict) else "") or ""
                top_word = str(top_word).strip()
                if top_word:
                    ph = get_corpus().suggest_phrases(top_word, limit=min(5, limit))
                    if ph:
                        suggestions.extend([{"word": w, "score": sc} for (w, sc) in ph])
                        if source.startswith("corpus"):
                            source = "corpus+phrases"
        except Exception:
            pass

        # Deduplicate and sort by score desc
        dedup = {}
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            w = (s.get("word") or "").strip()
            if not w:
                continue
            try:
                score = float(s.get("score", 0.0))
            except Exception:
                score = 0.0
            if w not in dedup or score > float(dedup[w].get("score", 0.0)):
                dedup[w] = {"word": w, "score": round(score, 2)}
        suggestions = sorted(dedup.values(), key=lambda x: x.get("score", 0.0), reverse=True)[:limit]

    except Exception as e:
        logging.error(f"suggest_api_error request_id={rid} q={q} error={str(e)}", exc_info=True)
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
    # Keep manual validation so errors are stable (400) instead of FastAPI 422.
    limit: int = 8,
    mode: str = Query("smart"),  # No pattern validation - we handle it in the function
    context: Optional[str] = Query(None, max_length=5000),
    cursor: Optional[int] = Query(None, ge=0),
    prev: Optional[str] = None,
):
    """
    Suggest endpoint backed by SuggestService (ranked + filtered).

    Returns:
      { success: true, suggestions: [{word, score}], meta: {...} }
    """
    rid = getattr(request.state, "request_id", "n/a")

    # Normalize compatibility modes into SuggestService modes
    m = (mode or "").strip().lower()
    mode_mappings = {
        "smart": "spoken",
        "strict": "formal",
        "written": "formal",
        "char": "char",
        "word": "spoken",
        "spoken": "spoken",
        "formal": "formal",
        "academic": "academic",
    }
    m = mode_mappings.get(m, "spoken")

    # Validate q/limit (keep error codes stable)
    if not q or len(q) < 1 or len(q) > 40:
        raise HTTPException(status_code=400, detail="q must be between 1 and 40 characters")
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")

    suggestions, meta = await get_suggest_service().suggest(q, limit=limit, mode=m, prev=prev, request_id=rid)
    # Always return success=true even if meta has error; keep UX stable.
    response.headers["X-Algorithm-Version"] = "runner-suggest-service"
    response.headers["X-Layers-Used"] = "SUGGEST"
    return {
        "success": True,
        "suggestions": suggestions or [],
        "meta": {
            "algorithm_version": "runner-suggest-service",
            "mode": m,
            **(meta or {}),
        },
    }


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
    """Alias for /transliterate/suggest (kept for backward compatibility)."""
    return await transliterate_suggest(q, request, response, limit, mode, context, cursor, prev)
