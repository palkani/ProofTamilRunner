"""
Comprehensive tests for the suggestion engine.
"""

import pytest
from app.suggestion_engine.engine import SuggestionEngine
from app.suggestion_engine.types import SuggestionRequest
from app.suggestion_engine.layers.core_transliteration import CoreTransliterationLayer
from app.suggestion_engine.layers.tamil_vowel_expand import TamilVowelExpandLayer
from app.suggestion_engine.layers.context_join import ContextJoinLayer
from app.suggestion_engine.layers.frequency_ranker import FrequencyRankerLayer
from app.suggestion_engine.layers.heuristics import HeuristicsLayer
from app.suggestion_engine.normalization import (
    normalize_unicode,
    is_valid_tamil_word,
    get_consonant_base,
)


@pytest.mark.asyncio
async def test_single_consonant_m():
    """Test that 'm' returns at least ['ம்', 'ம', 'மா', 'மே'] in correct order."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="m", limit=10, mode="smart")
    result = await engine.suggest(request, "test-1")

    assert result.success, f"Request failed: {result.error}"
    assert len(result.suggestions) > 0, "No suggestions returned"

    words = [s["word"] for s in result.suggestions]

    # Must contain these core suggestions
    assert "ம்" in words, f"'ம்' not in {words}"
    assert "ம" in words, f"'ம' not in {words}"
    assert "மா" in words, f"'மா' not in {words}"
    assert "மே" in words, f"'மே' not in {words}"

    # Check ordering: 'ம்' should be first or second
    assert words[0] in ["ம்", "ம"], f"First suggestion should be 'ம்' or 'ம', got {words[0]}"

    # Verify scores are descending
    scores = [s["score"] for s in result.suggestions]
    assert scores == sorted(scores, reverse=True), "Scores not in descending order"


@pytest.mark.asyncio
async def test_single_consonant_k():
    """Test consonant 'k' returns pulli + base + vowel expansions."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="k", limit=10, mode="smart")
    result = await engine.suggest(request, "test-2")

    assert result.success
    words = [s["word"] for s in result.suggestions]

    assert "க்" in words or "க" in words, f"Expected 'க்' or 'க' in {words}"
    assert any("க" in w for w in words), f"Expected Tamil 'க' in suggestions"


@pytest.mark.asyncio
async def test_single_consonant_t():
    """Test consonant 't'."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="t", limit=10, mode="smart")
    result = await engine.suggest(request, "test-3")

    assert result.success
    words = [s["word"] for s in result.suggestions]
    assert any("த" in w for w in words), f"Expected Tamil 'த' in {words}"


@pytest.mark.asyncio
async def test_single_consonant_n():
    """Test consonant 'n'."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="n", limit=10, mode="smart")
    result = await engine.suggest(request, "test-4")

    assert result.success
    words = [s["word"] for s in result.suggestions]
    assert any("ந" in w or "ன" in w for w in words), f"Expected Tamil 'ந' or 'ன' in {words}"


@pytest.mark.asyncio
async def test_context_at_boundary():
    """Test context-aware suggestions at word boundary."""
    engine = SuggestionEngine()
    request = SuggestionRequest(
        q="m",
        limit=10,
        mode="smart",
        context="என் ",
        cursor=3,  # At end, after space
    )
    result = await engine.suggest(request, "test-5")

    assert result.success
    words = [s["word"] for s in result.suggestions]
    # Should prefer standalone forms at boundary
    assert "ம்" in words or "ம" in words


@pytest.mark.asyncio
async def test_context_inside_word():
    """Test context-aware joining inside Tamil word."""
    engine = SuggestionEngine()
    request = SuggestionRequest(
        q="m",
        limit=10,
        mode="smart",
        context="த",
        cursor=1,  # After 'த'
    )
    result = await engine.suggest(request, "test-6")

    assert result.success
    # Should still return valid suggestions (may or may not join)
    assert len(result.suggestions) > 0


@pytest.mark.asyncio
async def test_strict_mode_no_heuristics():
    """Test that strict mode excludes heuristic neighbors."""
    engine = SuggestionEngine()
    request_strict = SuggestionRequest(q="m", limit=20, mode="strict")
    result_strict = await engine.suggest(request_strict, "test-7")

    request_smart = SuggestionRequest(q="m", limit=20, mode="smart")
    result_smart = await engine.suggest(request_smart, "test-8")

    assert result_strict.success
    assert result_smart.success

    words_strict = [s["word"] for s in result_strict.suggestions]
    words_smart = [s["word"] for s in result_smart.suggestions]

    # Strict mode should have fewer or equal suggestions
    assert len(words_strict) <= len(words_smart)

    # Heuristic neighbor "ன்" should not appear in strict mode
    # (or if it does, it should be from core transliteration, not heuristics)
    if "ன்" in words_strict:
        # Check that it's not from heuristics layer
        for s in result_strict.suggestions:
            if s["word"] == "ன்":
                # Should not have heuristic debug flag
                debug = s.get("_debug", {})
                assert not debug.get("heuristic", False), "Heuristic found in strict mode"


@pytest.mark.asyncio
async def test_frequency_boost():
    """Test that frequency boosts reorder suggestions."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="m", limit=10, mode="smart")
    result = await engine.suggest(request, "test-9")

    assert result.success
    # "ம்" should be highly ranked due to frequency
    words = [s["word"] for s in result.suggestions]
    assert "ம்" in words[:3], f"'ம்' should be in top 3, got order: {words}"


@pytest.mark.asyncio
async def test_validation_q_too_long():
    """Test validation rejects q > 40 chars."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="a" * 41, limit=10, mode="smart")
    result = await engine.suggest(request, "test-10")

    assert not result.success
    assert result.error is not None
    assert result.error.get("code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_validation_limit_out_of_range():
    """Test validation rejects invalid limit."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="m", limit=0, mode="smart")
    result = await engine.suggest(request, "test-11")

    assert not result.success
    assert result.error is not None


@pytest.mark.asyncio
async def test_validation_mode_invalid():
    """Test validation rejects invalid mode."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="m", limit=10, mode="invalid")
    result = await engine.suggest(request, "test-12")

    assert not result.success
    assert result.error is not None


@pytest.mark.asyncio
async def test_meta_diagnostics():
    """Test that meta contains required diagnostics."""
    engine = SuggestionEngine()
    request = SuggestionRequest(q="m", limit=5, mode="smart")
    result = await engine.suggest(request, "test-13")

    assert result.success
    assert "meta" in result.__dict__ or hasattr(result, "meta")

    meta = result.meta
    assert "algorithm_version" in meta
    assert "layers_used" in meta
    assert "timings_ms" in meta
    assert "cache_hits" in meta
    assert isinstance(meta["layers_used"], list)
    assert len(meta["layers_used"]) > 0


def test_vowel_expansion_layer():
    """Test vowel expansion layer generates correct expansions."""
    layer = TamilVowelExpandLayer()
    core_candidates = []  # Empty for this test
    candidates = layer.generate("m", core_candidates)

    words = [c.word for c in candidates]
    assert "மா" in words
    assert "மே" in words
    assert "மி" in words


def test_frequency_ranker():
    """Test frequency ranker applies boosts."""
    layer = FrequencyRankerLayer()
    from app.suggestion_engine.types import Candidate

    candidates = [
        Candidate(word="ம்", base_score=0.85, source_layer="test"),
        Candidate(word="ம", base_score=0.80, source_layer="test"),
    ]

    boosted = layer.apply_boost(candidates)
    assert len(boosted) == 2
    # "ம்" should have higher score due to frequency
    scores = {c.word: c.base_score for c in boosted}
    assert scores["ம்"] >= scores["ம"]


def test_heuristics_layer_smart_mode():
    """Test heuristics layer only in smart mode."""
    layer = HeuristicsLayer(enabled=True)
    candidates = layer.generate("m", "smart")
    assert len(candidates) > 0

    candidates_strict = layer.generate("m", "strict")
    assert len(candidates_strict) == 0


def test_normalization():
    """Test Unicode normalization."""
    assert normalize_unicode("ம") == "ம"
    assert is_valid_tamil_word("ம்")
    assert not is_valid_tamil_word("abc")
    assert get_consonant_base("m") == "ம"

