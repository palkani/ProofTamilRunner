"""
Integration tests for the suggest API endpoint.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_suggest_endpoint_basic():
    """Test basic suggest endpoint functionality."""
    response = client.get("/transliterate/suggest?q=m&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) > 0


def test_suggest_endpoint_with_mode():
    """Test suggest endpoint with mode parameter."""
    response = client.get("/transliterate/suggest?q=m&limit=5&mode=smart")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    response_strict = client.get("/transliterate/suggest?q=m&limit=5&mode=strict")
    assert response_strict.status_code == 200
    data_strict = response_strict.json()
    assert data_strict["success"] is True


def test_suggest_endpoint_with_context():
    """Test suggest endpoint with context."""
    response = client.get(
        "/transliterate/suggest?q=m&limit=5&context=என்&cursor=2"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "meta" in data


def test_suggest_endpoint_validation_q_too_long():
    """Test validation for q too long."""
    long_q = "a" * 41
    response = client.get(f"/transliterate/suggest?q={long_q}")
    assert response.status_code == 400


def test_suggest_endpoint_validation_limit_invalid():
    """Test validation for invalid limit."""
    response = client.get("/transliterate/suggest?q=m&limit=0")
    assert response.status_code == 400

    response = client.get("/transliterate/suggest?q=m&limit=21")
    assert response.status_code == 400


def test_suggest_endpoint_validation_mode_invalid():
    """Test validation for invalid mode."""
    response = client.get("/transliterate/suggest?q=m&mode=invalid")
    assert response.status_code == 422  # FastAPI validation error


def test_suggest_endpoint_response_format():
    """Test response format matches expected schema."""
    response = client.get("/transliterate/suggest?q=m&limit=3")
    assert response.status_code == 200
    data = response.json()

    # Check structure
    assert "success" in data
    assert "suggestions" in data
    assert "meta" in data

    # Check suggestion format
    if data["suggestions"]:
        suggestion = data["suggestions"][0]
        assert "word" in suggestion
        assert "score" in suggestion
        assert isinstance(suggestion["word"], str)
        assert isinstance(suggestion["score"], (int, float))


def test_suggest_endpoint_meta_diagnostics():
    """Test that meta contains diagnostics."""
    response = client.get("/transliterate/suggest?q=m&limit=5")
    assert response.status_code == 200
    data = response.json()

    meta = data.get("meta", {})
    assert "algorithm_version" in meta
    assert "layers_used" in meta
    assert "cache_hits" in meta

    # Check headers
    assert "X-Algorithm-Version" in response.headers
    assert "X-Layers-Used" in response.headers

