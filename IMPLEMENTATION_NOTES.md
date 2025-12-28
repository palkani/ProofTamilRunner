# Implementation Notes - Suggestion Engine

## Overview

The suggestion engine implements a layered algorithm for generating context-aware Tamil transliteration suggestions. This document explains the ranking weights, tuning parameters, and implementation details.

## Algorithm Version

Current version: **1.0.0**

## Layer Architecture

### Layer A: Core Transliteration
- **Purpose**: Generate strict transliteration using existing mechanisms
- **Sources**: 
  - External transliteration runner (if enabled)
  - Aksharamukha adapter (fallback)
  - Direct consonant mapping for single characters
- **Base Scores**:
  - Runner results: 0.95
  - Adapter results: 0.90
  - Direct consonant base: 0.85
  - Direct consonant + pulli: 0.88

### Layer B: Tamil Vowel Expansion
- **Purpose**: Generate syllabic expansions for consonant-only fragments
- **Trigger**: Single consonant inputs (1 character)
- **Vowel Expansions** (with base scores):
  - ா (aa): 0.75
  - ி (i): 0.70
  - ீ (ii): 0.65
  - ு (u): 0.70
  - ூ (uu): 0.65
  - ெ (e): 0.72
  - ே (ee): 0.68
  - ை (ai): 0.60
  - ொ (o): 0.70
  - ோ (oo): 0.65
  - ௌ (au): 0.55

### Layer C: Context-Aware Completion
- **Purpose**: Adjust scores based on context and joining rules
- **Boosts**:
  - Can join with previous Tamil char: +0.05
  - At boundary (standalone): +0.02
- **Penalties**:
  - Cannot join (inside word): -0.01

### Layer D: Frequency Ranking
- **Purpose**: Boost common Tamil words
- **Maximum Boost**: +0.12
- **Formula**: `(word_freq / max_freq) * 0.12`
- **Prefix Matching**: For partial words, uses 50% of full word boost

### Layer E: Heuristic Neighbors
- **Purpose**: Add phonetic/orthographic neighbors (smart mode only)
- **Base Score**: 0.40 (lower than core to ensure they rank lower)
- **Enabled**: Only in "smart" mode
- **Mappings**: Defined in `heuristics.py` (e.g., "m" -> ["ன்"])

### Layer F: Dedup + Final Ranker
- **Purpose**: Normalize, deduplicate, and final ranking
- **Penalties**:
  - Length mismatch (1 char input, >3 char word): 0.05 per extra char
  - Rare expansions (≤2 char input, >4 char word): 0.10
- **Sorting** (stable):
  1. Score (descending)
  2. Word length (ascending)
  3. Unicode codepoint (ascending)

## Tuning Parameters

### Score Ranges
- Core transliteration: 0.85 - 0.95
- Vowel expansions: 0.55 - 0.75
- Heuristics: 0.40
- Frequency boost: up to +0.12
- Context boost: up to +0.10
- Penalties: up to -0.10

### Cache Configuration
- Core cache size: 10,000 entries
- Final cache size: 10,000 entries
- TTL: 600 seconds (configurable via `CACHE_TTL_SECONDS`)

### Performance Targets
- p95 latency: < 60ms (local, warm cache)
- Cache hit rate: > 80% (expected for repeated queries)

## Tamil Orthography Handling

### Dependent Vowels
The engine correctly handles Tamil dependent vowels (vowel signs):
- These attach to consonants using Unicode combining characters
- Invalid sequences (two dependent vowels in a row) are filtered out
- Common vowels: ா, ி, ீ, ு, ூ, ெ, ே, ை, ொ, ோ, ௌ

### Pulli (Virama)
- Pulli (்) removes the inherent vowel from a consonant
- For single consonants, both base and pulli forms are generated
- Example: "m" -> ["ம", "ம்"]

### Consonant Mapping
Roman-to-Tamil consonant mapping is defined in `normalization.py`:
- Handles common transliteration patterns
- Case-insensitive
- Supports both voiced and unvoiced variants (e.g., "k"/"g" -> "க")

## Context-Aware Joining Rules

### Boundary Detection
- At boundary: whitespace, punctuation, or start/end of text
- Inside word: within a Tamil word sequence

### Joining Logic
1. If last char is consonant with pulli: candidate can join
2. If last char is consonant without vowel: candidate can join
3. If last char ends with vowel: candidate should start with consonant to continue word

## Frequency Dictionary

### Format
JSON file: `data/ta_frequency_min.json`
```json
{
  "ம்": 1000,
  "ம": 950,
  "மா": 800,
  ...
}
```

### Updating
1. Edit `data/ta_frequency_min.json`
2. Add/update word-frequency pairs
3. Restart service to reload

### Future Enhancements
- Support for larger frequency datasets
- Dynamic loading without restart
- Per-domain frequency lists

## Error Handling

### Runner Failures
- If external runner fails, falls back to Aksharamukha adapter
- Sets `runner_error: true` in meta
- Does not fail the request

### Validation Errors
- Returns HTTP 400 with structured error
- Error format: `{"code": "INVALID_INPUT", "message": "..."}`

## Testing

### Unit Tests
- `test_suggestion_engine.py`: Tests individual layers and engine
- `test_suggest_endpoint.py`: Integration tests for API endpoint

### Key Test Cases
1. Single consonant "m" returns ["ம்", "ம", "மா", "மே"]
2. Context-aware joining at boundaries
3. Strict mode excludes heuristics
4. Frequency boosts reorder suggestions
5. Validation rejects invalid inputs

## Performance Optimization

### Caching Strategy
- Two-level cache:
  - Core cache: (mode, q) -> core candidates
  - Final cache: (mode, q, boundary, last_char_class) -> final suggestions
- Cache keys use SHA256 hash for consistency

### Async Operations
- All I/O operations are async
- External runner calls have timeout guards
- Non-blocking cache operations

## Security Considerations

### Input Sanitization
- Context truncated to 5000 chars
- Only last 12 chars logged (masked)
- No PII in logs

### Rate Limiting
- Inherits from existing middleware
- Per-client rate limits

## Future Enhancements

1. **Larger Frequency Dataset**: Support for bigger frequency lists
2. **Machine Learning**: Optional ML-based ranking (future)
3. **Multi-tenant**: Per-project frequency lists
4. **Metrics**: Prometheus/Micrometer integration
5. **A/B Testing**: Support for algorithm variants

## Maintenance

### Adding New Heuristics
1. Edit `app/suggestion_engine/layers/heuristics.py`
2. Add to `HEURISTIC_NEIGHBORS` dict
3. Add unit test
4. Update documentation

### Tuning Scores
1. Edit layer files to adjust base scores
2. Run tests to verify ranking
3. Monitor production metrics
4. Iterate based on user feedback

