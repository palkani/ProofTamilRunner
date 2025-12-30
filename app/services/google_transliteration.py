"""
Google Input Tools API integration for Tamil transliteration.
FREE API - No API key required.
"""

import httpx
import asyncio
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from collections import OrderedDict
import threading

logger = logging.getLogger(__name__)


class TransliterationCache:
    """Thread-safe LRU cache with TTL for transliteration suggestions."""
    
    def __init__(self, max_size: int = 15000, ttl_seconds: int = 600):
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0, "google_calls": 0, "fallback_calls": 0}
        
    def _make_key(self, text: str, limit: int) -> str:
        return f"{text.lower().strip()}:{limit}"
    
    def get(self, text: str, limit: int = 8) -> Optional[List[Dict]]:
        key = self._make_key(text, limit)
        with self._lock:
            if key not in self._cache:
                self._stats["misses"] += 1
                return None
            entry = self._cache[key]
            if datetime.now() > entry["expires_at"]:
                del self._cache[key]
                self._stats["misses"] += 1
                return None
            self._cache.move_to_end(key)
            self._stats["hits"] += 1
            return entry["suggestions"]
    
    def set(self, text: str, limit: int, suggestions: List[Dict], source: str = "unknown"):
        key = self._make_key(text, limit)
        with self._lock:
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = {
                "suggestions": suggestions,
                "expires_at": datetime.now() + timedelta(seconds=self._ttl_seconds),
                "source": source
            }
    
    def get_stats(self) -> Dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
            return {**self._stats, "size": len(self._cache), "hit_rate_percent": round(hit_rate, 2)}


class GoogleTransliterationClient:
    """Async client for Google Input Tools API."""
    
    BASE_URL = "https://inputtools.google.com/request"
    LANGUAGE_CODES = {
        "tamil": "ta-t-i0-und",
        "hindi": "hi-t-i0-und",
        "telugu": "te-t-i0-und",
        "kannada": "kn-t-i0-und",
        "malayalam": "ml-t-i0-und",
        "bengali": "bn-t-i0-und",
    }
    
    def __init__(self, language: str = "tamil", timeout: float = 2.0):
        self.language_code = self.LANGUAGE_CODES.get(language, "ta-t-i0-und")
        self.timeout = timeout
    
    async def get_suggestions(self, text: str, limit: int = 8) -> List[Dict[str, Any]]:
        if not text or not text.strip():
            return []
        
        params = {
            "text": text.strip().lower(),
            "itc": self.language_code,
            "num": min(limit, 20),
            "cp": 0,
            "cs": 1,
            "ie": "utf-8",
            "oe": "utf-8",
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.BASE_URL, params=params)
                if response.status_code != 200:
                    logger.debug(f"Google API returned status {response.status_code}")
                    return []
                data = response.json()
                return self._parse_response(data, limit)
        except Exception as e:
            logger.debug(f"Google API error: {e}")
            return []
    
    def _parse_response(self, data: Any, limit: int) -> List[Dict[str, Any]]:
        try:
            if not isinstance(data, list) or len(data) < 2 or data[0] != "SUCCESS":
                return []
            results = data[1]
            if not results or not isinstance(results, list) or len(results) == 0:
                return []
            first_result = results[0]
            if not isinstance(first_result, list) or len(first_result) < 2:
                return []
            suggestions_list = first_result[1]
            if not isinstance(suggestions_list, list):
                return []
            return [
                {"word": word.strip(), "score": round(1.0 - (idx * 0.03), 2)}
                for idx, word in enumerate(suggestions_list[:limit])
                if word and isinstance(word, str) and word.strip()
            ]
        except Exception as e:
            logger.debug(f"Error parsing Google response: {e}")
            return []


class TamilTransliterator:
    """Local Tamil transliteration fallback."""
    
    CONSONANTS = {
        'k': 'க', 'g': 'க', 'c': 'க', 'ng': 'ங', 'ch': 'ச', 's': 'ச', 'j': 'ஜ',
        'nj': 'ஞ', 't': 'ட', 'd': 'ட', 'N': 'ண', 'th': 'த', 'dh': 'த', 'n': 'ந',
        'p': 'ப', 'b': 'ப', 'f': 'ப', 'm': 'ம', 'y': 'ய', 'r': 'ர', 'l': 'ல',
        'v': 'வ', 'w': 'வ', 'zh': 'ழ', 'L': 'ள', 'R': 'ற', 'nn': 'ன', 'sh': 'ஷ', 'h': 'ஹ',
    }
    VOWELS = {
        'a': 'அ', 'aa': 'ஆ', 'A': 'ஆ', 'i': 'இ', 'ee': 'ஈ', 'I': 'ஈ', 'ii': 'ஈ',
        'u': 'உ', 'oo': 'ஊ', 'U': 'ஊ', 'uu': 'ஊ', 'e': 'எ', 'E': 'ஏ', 'ae': 'ஏ',
        'ai': 'ஐ', 'o': 'ஒ', 'O': 'ஓ', 'oa': 'ஓ', 'au': 'ஔ', 'ow': 'ஔ',
    }
    VOWEL_SIGNS = {
        'a': '', 'aa': 'ா', 'A': 'ா', 'i': 'ி', 'ee': 'ீ', 'I': 'ீ', 'ii': 'ீ',
        'u': 'ு', 'oo': 'ூ', 'U': 'ூ', 'uu': 'ூ', 'e': 'ெ', 'E': 'ே', 'ae': 'ே',
        'ai': 'ை', 'o': 'ொ', 'O': 'ோ', 'oa': 'ோ', 'au': 'ௌ', 'ow': 'ௌ',
    }
    PULLI = '்'
    COMMON_WORDS = {
        "tamil": ["தமிழ்", "தமிழன்", "தமிழி"],
        "vanakkam": ["வணக்கம்"],
        "nandri": ["நன்றி"],
        "amma": ["அம்மா"],
        "appa": ["அப்பா"],
        "anna": ["அண்ணா", "அண்ணன்"],
        "akka": ["அக்கா"],
        "thambi": ["தம்பி"],
        "thangai": ["தங்கை"],
        "muruga": ["முருகா", "முருகன்", "முருகேசன்"],
        "murugan": ["முருகன்"],
        "kovil": ["கோவில்"],
        "velu": ["வேலு", "வேல்"],
        "kumar": ["குமார்", "குமரன்"],
        "rajan": ["ராஜன்", "ராஜா"],
        "lakshmi": ["லட்சுமி"],
        "ganesh": ["கணேஷ்", "கணேசன்"],
        "shiva": ["சிவா", "சிவன்"],
        "krishna": ["கிருஷ்ணா"],
        "ram": ["ராம்", "ராமன்"],
        "sita": ["சீதா"],
        "radha": ["ராதா"],
    }
    
    def transliterate(self, text: str) -> str:
        if not text:
            return ""
        result = []
        i = 0
        text_lower = text.lower()
        length = len(text_lower)
        
        while i < length:
            matched = False
            for match_len in [3, 2, 1]:
                if i + match_len > length:
                    continue
                chunk = text_lower[i:i + match_len]
                
                if chunk in self.CONSONANTS:
                    consonant = self.CONSONANTS[chunk]
                    i += match_len
                    vowel_found = False
                    for v_len in [2, 1]:
                        if i + v_len <= length:
                            v_chunk = text_lower[i:i + v_len]
                            if v_chunk in self.VOWEL_SIGNS:
                                result.append(consonant + self.VOWEL_SIGNS[v_chunk])
                                i += v_len
                                vowel_found = True
                                break
                    if not vowel_found:
                        if i < length:
                            is_next_consonant = any(
                                text_lower[i:i+clen] in self.CONSONANTS
                                for clen in [3, 2, 1] if i + clen <= length
                            )
                            if is_next_consonant:
                                result.append(consonant + self.PULLI)
                            else:
                                result.append(consonant)
                        else:
                            result.append(consonant + self.PULLI)
                    matched = True
                    break
                
                if chunk in self.VOWELS:
                    result.append(self.VOWELS[chunk])
                    i += match_len
                    matched = True
                    break
            
            if not matched:
                result.append(text_lower[i])
                i += 1
        
        return ''.join(result)
    
    def get_suggestions(self, text: str, limit: int = 8) -> List[Dict[str, Any]]:
        if not text:
            return []
        
        # Import validation function
        from app.suggestion_engine.normalization import is_valid_tamil_word, clean_tamil_text
        
        text_lower = text.lower().strip()
        suggestions = []
        seen = set()
        
        # Helper to validate and add suggestion
        def add_suggestion(word: str, score: float):
            if not word or not word.strip():
                return
            # Clean the word (remove formatting characters)
            cleaned = clean_tamil_text(word)
            # Validate Tamil orthography
            if not is_valid_tamil_word(cleaned):
                return
            # Check for invalid patterns (e.g., vowel + standalone vowel)
            if cleaned in seen:
                return
            seen.add(cleaned)
            suggestions.append({"word": cleaned, "score": score})
        
        # 1. Check common words dictionary first (highest quality)
        for key, words in self.COMMON_WORDS.items():
            if key.startswith(text_lower) or text_lower.startswith(key):
                for word in words:
                    add_suggestion(word, 0.95 if key == text_lower else 0.85)
        
        # 2. Direct transliteration (high quality)
        direct = self.transliterate(text_lower)
        if direct:
            add_suggestion(direct, 1.0)
        
        # 3. Add suffix variations only for common suffixes that make sense
        # Only add suffixes that create valid Tamil words
        valid_suffixes = ["a", "aa", "am", "an"]  # Removed "ai", "u", "i" as they create invalid words
        for suffix in valid_suffixes:
            trans = self.transliterate(text_lower + suffix)
            if trans:
                add_suggestion(trans, 0.7)
        
        # Sort by score and return top suggestions
        suggestions.sort(key=lambda x: x["score"], reverse=True)
        
        # Deduplicate by word (keep highest score)
        seen_words = {}
        deduplicated = []
        for sug in suggestions:
            word = sug["word"]
            if word not in seen_words:
                seen_words[word] = sug["score"]
                deduplicated.append(sug)
            elif sug["score"] > seen_words[word]:
                # Replace with higher score
                seen_words[word] = sug["score"]
                # Remove old entry and add new one
                deduplicated = [s for s in deduplicated if s["word"] != word]
                deduplicated.append(sug)
                # Re-sort after replacement
                deduplicated.sort(key=lambda x: x["score"], reverse=True)
        
        return deduplicated[:limit]


# Initialize global instances
_transliteration_cache = TransliterationCache(max_size=15000, ttl_seconds=600)
_google_client = GoogleTransliterationClient(language="tamil", timeout=2.0)
_fallback_transliterator = TamilTransliterator()
_inflight_requests: Dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


async def get_transliteration_suggestions(
    text: str,
    limit: int = 8,
    mode: str = "smart",
    use_google: bool = True,
    use_cache: bool = True,
    timeout: float = 2.5
) -> Dict[str, Any]:
    """Get Tamil suggestions with Google API + caching + fallback."""
    start_time = datetime.now()
    clean_text = text.strip().lower() if text else ""
    
    if not clean_text:
        return {"suggestions": [], "source": "empty", "cached": False, "ms": 0}
    
    # Check cache first
    if use_cache:
        cached = _transliteration_cache.get(clean_text, limit)
        if cached:
            elapsed = (datetime.now() - start_time).total_seconds() * 1000
            return {"suggestions": cached, "source": "cache", "cached": True, "ms": round(elapsed, 1)}
    
    # Deduplicate in-flight requests
    cache_key = f"{clean_text}:{limit}"
    async with _inflight_lock:
        if cache_key in _inflight_requests:
            try:
                result = await asyncio.wait_for(_inflight_requests[cache_key], timeout=timeout)
                elapsed = (datetime.now() - start_time).total_seconds() * 1000
                return {"suggestions": result, "source": "dedupe", "cached": False, "ms": round(elapsed, 1)}
            except Exception:
                pass
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        _inflight_requests[cache_key] = future
    
    suggestions = []
    source = "fallback"
    
    try:
        # Try Google API first (with strict timeout protection)
        if use_google:
            try:
                # Use shorter timeout than passed in to fail fast
                google_timeout = min(timeout, 0.5)  # Never wait more than 0.5s for Google
                google_suggestions = await asyncio.wait_for(
                    _google_client.get_suggestions(clean_text, limit), timeout=google_timeout
                )
                if google_suggestions and len(google_suggestions) > 0:
                    suggestions = google_suggestions
                    source = "google"
                    _transliteration_cache._stats["google_calls"] += 1
            except asyncio.TimeoutError:
                logger.debug(f"Google API timeout for: {clean_text} - using local fallback")
                # Immediately continue to local fallback (no waiting)
            except Exception as e:
                logger.debug(f"Google API error for {clean_text}: {e} - using local fallback")
                # Immediately continue to local fallback (no waiting)
        
        # Fallback to local (always available, no external calls)
        if not suggestions or len(suggestions) == 0:
            suggestions = _fallback_transliterator.get_suggestions(clean_text, limit)
            source = "fallback"
            _transliteration_cache._stats["fallback_calls"] += 1
        
        # Cache results
        if suggestions and use_cache:
            _transliteration_cache.set(clean_text, limit, suggestions, source)
        
        if not future.done():
            future.set_result(suggestions)
        
        elapsed = (datetime.now() - start_time).total_seconds() * 1000
        return {"suggestions": suggestions, "source": source, "cached": False, "ms": round(elapsed, 1)}
    
    except Exception as e:
        logger.error(f"Transliteration error: {e}")
        if not future.done():
            future.set_exception(e)
        return {"suggestions": [], "source": "error", "cached": False, "ms": 0}
    
    finally:
        async with _inflight_lock:
            _inflight_requests.pop(cache_key, None)


def get_cache_stats() -> Dict:
    """Get cache statistics."""
    return _transliteration_cache.get_stats()

