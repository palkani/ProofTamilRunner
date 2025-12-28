"""
Suggestion Engine for Tamil transliteration with layered algorithm.

Provides context-aware Tamil suggestions with multiple layers:
- Layer A: Core Transliteration (strict)
- Layer B: Tamil Orthography Expansion (vowel matrix)
- Layer C: Context-Aware Completion (prefix-aware)
- Layer D: Frequency/Dataset Ranking
- Layer E: Phonetic/Heuristic Neighbors (smart mode only)
- Layer F: Dedup + Final Ranker
"""

from app.suggestion_engine.engine import SuggestionEngine

__all__ = ["SuggestionEngine"]

