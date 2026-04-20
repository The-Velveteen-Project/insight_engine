"""
Relevance ranker — no LLM, no network.

Scores a SignalCandidate against The Velveteen Project's profile using
deterministic keyword matching plus a small query-overlap bonus.

Scoring rules
─────────────
• Primary keywords   : +0.30 per match, cap at 0.60
• Secondary keywords : +0.15 per match, cap at 0.30
• Tertiary keywords  : +0.05 per match, cap at 0.10
• Query overlap      : +0.10 per match, cap at 0.20
• Recency bonus      : +0.10 when published_at is within the last 7 days
• Total              : capped at 1.0

The text corpus searched = title (2×) + summary (1×), all lowercased.
Doubling the title weight is implemented by concatenating it twice.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from app.schemas.discovery import SignalCandidate

# ---------------------------------------------------------------------------
# Profile keyword tables (The Velveteen Project)
# ---------------------------------------------------------------------------

_PRIMARY: list[str] = [
    "machine learning",
    "llm",
    "large language model",
    "nlp",
    "natural language processing",
    "agentic",
    "agent",
    "system design",
    "decision system",
    "decision support",
    "applied ai",
    "ai system",
    "research workflow",
    "mathematical model",
    "mathematical modeling",
    "modeling",
    "neural network",
    "deep learning",
    "transformer",
    "reinforcement learning",
    "applied research",
    "fine-tuning",
    "rag",
    "retrieval augmented",
    "embedding",
]

_SECONDARY: list[str] = [
    "time series",
    "anomaly detection",
    "forecasting",
    "bayesian",
    "causal",
    "risk",
    "risk management",
    "operational risk",
    "health",
    "public health",
    "climate",
    "climate risk",
    "resilience",
    "education",
    "technical education",
    "education technology",
    "software engineering",
    "useful software",
    "open source",
    "latam",
    "latin america",
    "interpretability",
    "explainability",
    "benchmark",
    "evaluation",
    "reproducib",
]

_TERTIARY: list[str] = [
    "founder",
    "product",
    "api",
    "dataset",
    "paper",
    "survey",
    "review",
    "tutorial",
    "workflow",
    "automation",
    "tool",
    "library",
    "prototype",
    "mvp",
]

_RECENCY_WINDOW = timedelta(days=7)
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{3,}")
_QUERY_STOPWORDS = {
    "about",
    "agentic",
    "against",
    "como",
    "con",
    "data",
    "from",
    "into",
    "para",
    "por",
    "sobre",
    "systems",
    "that",
    "this",
    "with",
}

# Score increments and caps
_PRIMARY_INCREMENT = 0.30
_PRIMARY_CAP = 0.60
_SECONDARY_INCREMENT = 0.15
_SECONDARY_CAP = 0.30
_TERTIARY_INCREMENT = 0.05
_TERTIARY_CAP = 0.10
_QUERY_INCREMENT = 0.10
_QUERY_CAP = 0.20
_RECENCY_BONUS = 0.10
_MAX_SCORE = 1.0


def _build_corpus(title: str, summary: str) -> str:
    """Title is concatenated twice to give it 2× search weight."""
    return f"{title} {title} {summary}".lower()


def _tier_score(
    corpus: str,
    keywords: list[str],
    increment: float,
    cap: float,
) -> tuple[float, list[str]]:
    matched: list[str] = []
    for kw in keywords:
        if kw in corpus:
            matched.append(kw)
    score = min(len(matched) * increment, cap)
    return score, matched


def _query_terms(query: str | None) -> list[str]:
    if not query:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(query.lower()):
        if token in _QUERY_STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def rank(
    candidate: SignalCandidate,
    *,
    query: str | None = None,
) -> tuple[float, str]:
    """
    Score a SignalCandidate against the profile.

    Returns (score, note) where:
    - score is in [0.0, 1.0]
    - note is a compact explanation suitable for logging / UI display
    """
    corpus = _build_corpus(candidate.title, candidate.summary)

    primary_score, primary_hits = _tier_score(
        corpus, _PRIMARY, _PRIMARY_INCREMENT, _PRIMARY_CAP
    )
    secondary_score, secondary_hits = _tier_score(
        corpus, _SECONDARY, _SECONDARY_INCREMENT, _SECONDARY_CAP
    )
    tertiary_score, tertiary_hits = _tier_score(
        corpus, _TERTIARY, _TERTIARY_INCREMENT, _TERTIARY_CAP
    )
    query_score, query_hits = _tier_score(
        corpus,
        _query_terms(query),
        _QUERY_INCREMENT,
        _QUERY_CAP,
    )

    recency_bonus = 0.0
    if candidate.published_at is not None:
        pub = candidate.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        if datetime.now(UTC) - pub <= _RECENCY_WINDOW:
            recency_bonus = _RECENCY_BONUS

    # Query gate — query relevance is the primary criterion.
    # When a query is provided but nothing matched, penalise heavily so that
    # off-topic profile hits don't outrank genuinely relevant results.
    #   query is None  →  no gate (pure profile scoring, used for scheduled jobs)
    #   query_score > 0 →  matched: gate = 1.0 (score normally)
    #   query_score == 0 →  no match: gate = 0.3 (caps profile score at ~0.18)
    query_gate = 1.0 if (query is None or query_score > 0.0) else 0.3

    raw = primary_score + secondary_score + tertiary_score + query_score + recency_bonus
    score = round(min(raw * query_gate, _MAX_SCORE), 4)

    # Build a terse, readable note
    parts: list[str] = []
    if primary_hits:
        parts.append(f"primary={primary_hits[:3]}")
    if secondary_hits:
        parts.append(f"secondary={secondary_hits[:2]}")
    if tertiary_hits:
        parts.append(f"tertiary={tertiary_hits[:2]}")
    if query_hits:
        parts.append(f"query={query_hits[:2]}")
    if recency_bonus:
        parts.append("recent")
    if query is not None and query_score == 0.0 and _query_terms(query):
        parts.append("sin match temático")
    note = "; ".join(parts) if parts else "no keyword matches"

    return score, note
