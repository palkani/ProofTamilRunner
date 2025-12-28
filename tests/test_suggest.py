"""
Comprehensive tests for suggest API endpoint.
Tests quality, rules, determinism, and production readiness.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.suggest_service import SuggestService
from app.services.canonical_map import get_canonical


@pytest.fixture
def client():
    """Create test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def suggest_service():
    """Create suggest service instance."""
    return SuggestService()


class TestCanonicalMapping:
    """Test canonical override mappings."""

    def test_tamil_canonical(self):
        """Input 'tamil' MUST return ONLY 'தமிழ்' as top suggestion."""
        output, found = get_canonical("tamil")
        assert found is True
        assert output == "தமிழ்"

    def test_thamizh_variants(self):
        """Test variant spellings all map to தமிழ்."""
        variants = ["thamizh", "thamiz", "tamizh", "tamiz"]
        for variant in variants:
            output, found = get_canonical(variant)
            assert found is True
            assert output == "தமிழ்"

    def test_mu_canonical(self):
        """Input 'mu' MUST return ONLY 'மு' as top suggestion."""
        output, found = get_canonical("mu")
        assert found is True
        assert output == "மு"

    def test_unknown_input(self):
        """Unknown input should return False."""
        output, found = get_canonical("unknown")
        assert found is False
        assert output == ""


class TestSuggestAPI:
    """Test suggest API endpoint."""

    @patch("app.api.routes.get_suggest_service")
    def test_suggest_tamil_returns_tamizh(self, mock_get_service, client):
        """QUALITY TEST: q='tamil' → top == 'தமிழ்'."""
        mock_service = MagicMock()
        mock_service.suggest = AsyncMock(
            return_value=(
                [{"word": "தமிழ்", "score": 1.0}],
                {"cache": "miss", "final_count": 1},
            )
        )
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/transliterate/suggest?q=tamil")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["suggestions"]) > 0
        assert data["suggestions"][0]["word"] == "தமிழ்"

    @patch("app.api.routes.get_suggest_service")
    def test_suggest_mu_returns_mu(self, mock_get_service, client):
        """QUALITY TEST: q='mu' → top == 'மு'."""
        mock_service = MagicMock()
        mock_service.suggest = AsyncMock(
            return_value=(
                [{"word": "மு", "score": 1.0}],
                {"cache": "miss", "final_count": 1},
            )
        )
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/transliterate/suggest?q=mu")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["suggestions"]) > 0
        assert data["suggestions"][0]["word"] == "மு"


class TestGarbageElimination:
    """Test that garbage forms are NEVER returned."""

    @pytest.mark.asyncio
    @patch("app.services.suggest_service.AksharaAdapter")
    async def test_no_garbage_forms(self, mock_adapter_class, suggest_service):
        """Garbage forms like 'முஉ', 'முஉஉ', 'முஇ' must NEVER be returned."""
        # Mock Aksharamukha to return garbage (simulating what could happen)
        mock_adapter = MagicMock()
        mock_adapter.transliterate = AsyncMock(
            return_value=["முஉ", "முஉஉ", "முஇ", "மு"]  # Last one is valid
        )
        mock_adapter_class.return_value = mock_adapter
        suggest_service.adapter = mock_adapter

        suggestions, metadata = await suggest_service.suggest("mu", limit=10)

        # Extract words
        words = [s["word"] for s in suggestions]

        # Assert garbage forms are NOT present
        assert "முஉ" not in words
        assert "முஉஉ" not in words
        assert "முஇ" not in words

        # Valid form should be present
        assert "மு" in words

    @pytest.mark.asyncio
    @patch("app.services.suggest_service.AksharaAdapter")
    async def test_no_invalid_endings(self, mock_adapter_class, suggest_service):
        """Test invalid endings like 'தமிலொஒ', 'தமில்ி' are rejected."""
        mock_adapter = MagicMock()
        mock_adapter.transliterate = AsyncMock(
            return_value=["தமிலொஒ", "தமில்ி", "தமில்த", "தமிழ்"]
        )
        mock_adapter_class.return_value = mock_adapter
        suggest_service.adapter = mock_adapter

        suggestions, metadata = await suggest_service.suggest("tamil", limit=10)
        words = [s["word"] for s in suggestions]

        # Invalid forms must not be present
        assert "தமிலொஒ" not in words
        assert "தமில்ி" not in words
        assert "தமில்த" not in words

        # Valid form should be present (or canonical should override)
        assert "தமிழ்" in words or any("தமிழ்" in w for w in words)


class TestOrthographyRules:
    """Test Tamil orthography validation rules."""

    def test_dependent_vowel_cannot_start(self):
        """Dependent vowel cannot start a word."""
        from app.services.tamil_linguistics import validate_tamil_orthography

        assert validate_tamil_orthography("ி") is False
        assert validate_tamil_orthography("ா") is False
        assert validate_tamil_orthography("ு") is False

    def test_no_double_dependent_vowels(self):
        """Two dependent vowels in a row is invalid."""
        from app.services.tamil_linguistics import validate_tamil_orthography

        assert validate_tamil_orthography("முஉ") is False  # மு + உ (invalid)
        assert validate_tamil_orthography("காா") is False  # கா +ா (invalid)

    def test_valid_words_pass(self):
        """Valid Tamil words should pass validation."""
        from app.services.tamil_linguistics import validate_tamil_orthography

        assert validate_tamil_orthography("தமிழ்") is True
        assert validate_tamil_orthography("மு") is True
        assert validate_tamil_orthography("வணக்கம்") is True

    def test_latin_digit_rejection(self):
        """Latin characters and digits must be rejected."""
        from app.services.tamil_linguistics import validate_tamil_orthography

        assert validate_tamil_orthography("தமிழ்a") is False
        assert validate_tamil_orthography("தமிழ்1") is False
        assert validate_tamil_orthography("tamil") is False


class TestDeterminism:
    """Test determinism - same input → same output."""

    @pytest.mark.asyncio
    async def test_deterministic_output(self, suggest_service):
        """Same input should produce same output."""
        # First call
        suggestions1, _ = await suggest_service.suggest("tamil", limit=5)

        # Second call (should be identical)
        suggestions2, _ = await suggest_service.suggest("tamil", limit=5)

        # Compare results
        words1 = [s["word"] for s in suggestions1]
        words2 = [s["word"] for s in suggestions2]

        assert words1 == words2, "Outputs must be deterministic"

    @pytest.mark.asyncio
    async def test_caching_determinism(self, suggest_service):
        """Cached results should match uncached results."""
        # Clear cache by creating new service
        service1 = SuggestService()
        service2 = SuggestService()

        # First call (will cache)
        suggestions1, meta1 = await service1.suggest("tamil", limit=5)

        # Second call (should use cache, but results should be same)
        # Note: This tests that caching doesn't break determinism
        suggestions2, meta2 = await service1.suggest("tamil", limit=5)

        words1 = [s["word"] for s in suggestions1]
        words2 = [s["word"] for s in suggestions2]

        assert words1 == words2


class TestInputNormalization:
    """Test Roman input normalization."""

    def test_lowercase_normalization(self):
        """Input should be lowercased."""
        from app.services.tamil_linguistics import normalize_roman_input

        assert normalize_roman_input("TAMIL") == "tamil"
        assert normalize_roman_input("Tamil") == "tamil"

    def test_variant_normalization(self):
        """Variant spellings should normalize to canonical."""
        from app.services.tamil_linguistics import normalize_roman_input

        assert normalize_roman_input("thamizh") == "tamil"
        assert normalize_roman_input("thamiz") == "tamil"
        assert normalize_roman_input("tamizh") == "tamil"

    def test_whitespace_trimming(self):
        """Whitespace should be trimmed."""
        from app.services.tamil_linguistics import normalize_roman_input

        assert normalize_roman_input("  tamil  ") == "tamil"
        assert normalize_roman_input("\ttamil\n") == "tamil"


class TestMorphologicalFiltering:
    """Test morphological garbage elimination."""

    def test_short_input_length_limit(self):
        """Short inputs (<=2 chars) should not produce long expansions (>3 chars)."""
        from app.services.tamil_linguistics import eliminate_morphological_garbage

        # Input "mu" (2 chars) should reject long outputs
        assert eliminate_morphological_garbage("முட்டை", 2) is False  # Too long
        assert eliminate_morphological_garbage("மு", 2) is True  # Valid

    def test_long_input_allows_longer_outputs(self):
        """Longer inputs can produce longer outputs."""
        from app.services.tamil_linguistics import eliminate_morphological_garbage

        assert eliminate_morphological_garbage("தமிழ்", 5) is True
        assert eliminate_morphological_garbage("வணக்கம்", 8) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

