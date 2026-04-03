"""
filter.py — Two-layer matching:
  1. Synonym/keyword match (fast, rule-based, with stemming)
  2. Semantic embedding similarity (sentence-transformers, local, no API)

A paper passes if EITHER layer fires.
Unmatched papers are returned separately, sorted by best semantic score,
so nothing is truly lost — they appear at the bottom as "misc".
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from fetcher import Paper


@dataclass
class Topic:
    id: str
    name: str
    terms: list[str]
    description: str
    enabled: bool = True


@dataclass
class MatchResult:
    paper: Paper
    matched_topics: list[str]
    match_method: str                   # "keyword" | "semantic" | "both"
    semantic_scores: dict[str, float] = field(default_factory=dict)
    best_semantic_score: float = 0.0    # used for sorting unmatched papers


# ─── Layer 1: Keyword / Synonym Matching ────────────────────────────────────

def _normalize(text: str) -> str:
    return text.lower()


def _build_patterns(topics: list[Topic]) -> dict[str, list[re.Pattern]]:
    """
    Compile one regex per term per topic.

    Single-word terms (e.g. "symbolic", "IIT", "CoT"):
      - matched as exact whole words only — no suffix wildcard.
      - This prevents "symbolic" from firing on "symbolically" in an unrelated
        sentence, and "awareness" from matching "self-aware" tangentially.

    Multi-word phrases (e.g. "test-time training", "knowledge graph"):
      - the final word gets a light suffix allowance (ing/ed/s/tion) because
        "test-time training" should still match "test-time trained".
      - interior words matched exactly.
    """
    patterns = {}
    for topic in topics:
        if not topic.enabled:
            continue
        compiled = []
        for term in topic.terms:
            escaped = re.escape(term.lower())
            words   = term.split()
            if len(words) == 1:
                # Exact whole-word match only — no stemming for single tokens
                pattern = re.compile(r'\b' + escaped + r'\b', re.IGNORECASE)
            else:
                # Multi-word: allow light suffix on the last word only
                pattern = re.compile(
                    r'\b' + escaped + r'(?:ing|ed|s|tion|ations?)?\b',
                    re.IGNORECASE
                )
            compiled.append(pattern)
        patterns[topic.id] = compiled
    return patterns


def keyword_match(
    papers: list[Paper],
    topics: list[Topic],
) -> dict[str, list[str]]:
    patterns  = _build_patterns(topics)
    topic_map = {t.id: t for t in topics}
    results: dict[str, list[str]] = {}

    for paper in papers:
        haystack = f"{paper.title} {paper.abstract}".lower()
        matched = []
        for topic_id, compiled_patterns in patterns.items():
            for pat in compiled_patterns:
                if pat.search(haystack):
                    matched.append(topic_map[topic_id].name)
                    break
        if matched:
            results[paper.id] = matched

    return results


# ─── Layer 2: Semantic Embedding Matching ───────────────────────────────────

def semantic_score_all(
    papers: list[Paper],
    topics: list[Topic],
    threshold: float = 0.35,
    already_matched_ids: Optional[set] = None,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """
    Scores ALL candidate papers (those not already keyword-matched).

    Returns:
      matched:   paper_id → {topic_name: score}  for papers >= threshold
      all_best:  paper_id → best_score            for ALL candidates (inc. below threshold)
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        print("[filter] sentence-transformers not installed, skipping semantic layer.")
        return {}, {}

    already_matched_ids = already_matched_ids or set()
    enabled_topics = [t for t in topics if t.enabled]

    candidates = [p for p in papers if p.id not in already_matched_ids]
    if not candidates:
        return {}, {}

    print(f"[filter] Running semantic scoring on {len(candidates)} papers...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    paper_texts     = [f"{p.title}. {p.abstract[:512]}" for p in candidates]
    paper_embeddings = model.encode(paper_texts, batch_size=64, show_progress_bar=False)

    topic_texts      = [t.description for t in enabled_topics]
    topic_embeddings = model.encode(topic_texts, show_progress_bar=False)

    from numpy.linalg import norm
    paper_norms = paper_embeddings / (norm(paper_embeddings, axis=1, keepdims=True) + 1e-9)
    topic_norms = topic_embeddings / (norm(topic_embeddings, axis=1, keepdims=True) + 1e-9)
    similarity  = paper_norms @ topic_norms.T  # (n_papers, n_topics)

    matched:  dict[str, dict[str, float]] = {}
    all_best: dict[str, float]            = {}

    for i, paper in enumerate(candidates):
        scores = {
            topic.name: round(float(similarity[i, j]), 3)
            for j, topic in enumerate(enabled_topics)
        }
        best = max(scores.values()) if scores else 0.0
        all_best[paper.id] = best

        above = {t: s for t, s in scores.items() if s >= threshold}
        if above:
            matched[paper.id] = above

    return matched, all_best


# ─── Combined Filter ─────────────────────────────────────────────────────────

def filter_papers(
    papers: list[Paper],
    topics: list[Topic],
    embedding_threshold: float = 0.35,
    seen_ids: Optional[set] = None,
) -> tuple[list[MatchResult], list[MatchResult]]:
    """
    Returns (matched, unmatched).

    matched:   papers that passed keyword or semantic threshold, sorted by date desc.
    unmatched: everything else, sorted by best semantic score desc
               (closest-to-relevant first) — rendered as "irrelevant" safety net.
    """
    seen_ids = seen_ids or set()

    fresh_papers = [p for p in papers if p.id not in seen_ids]
    print(f"[filter] {len(fresh_papers)} fresh papers.")

    if not fresh_papers:
        return [], []

    # Layer 1: keyword
    keyword_results = keyword_match(fresh_papers, topics)
    print(f"[filter] Keyword layer matched {len(keyword_results)} papers.")

    # Layer 2: semantic — scores ALL non-keyword papers, not just above threshold
    semantic_results, all_best_scores = semantic_score_all(
        fresh_papers,
        topics,
        threshold=embedding_threshold,
        already_matched_ids=set(keyword_results.keys()),
    )
    print(f"[filter] Semantic layer matched {len(semantic_results)} additional papers.")

    paper_map   = {p.id: p for p in fresh_papers}
    matched_ids = set(keyword_results) | set(semantic_results)

    # ── Matched results ──
    matched = []
    for pid in matched_ids:
        paper  = paper_map[pid]
        in_kw  = pid in keyword_results
        in_sem = pid in semantic_results

        if in_kw and in_sem:
            method         = "both"
            topics_matched = list(set(keyword_results[pid]) | set(semantic_results[pid].keys()))
        elif in_kw:
            method         = "keyword"
            topics_matched = keyword_results[pid]
        else:
            method         = "semantic"
            topics_matched = list(semantic_results[pid].keys())

        matched.append(MatchResult(
            paper=paper,
            matched_topics=topics_matched,
            match_method=method,
            semantic_scores=semantic_results.get(pid, {}),
            best_semantic_score=all_best_scores.get(pid, 0.0),
        ))

    def _rank(r: MatchResult) -> tuple:
        """
        Sort key — lower tuple = higher rank (sort ascending, then reverse).
        Tier 0: keyword + semantic match (most confident)
        Tier 1: keyword-only (exact term hit — precise but no score)
        Tier 2: semantic-only (ranked by score descending)
        Within each tier: best_score descending (keyword gets synthetic 1.0)
        """
        tier = 0 if r.match_method == "both" else \
               1 if r.match_method == "keyword" else 2
        score = r.best_semantic_score if r.match_method != "keyword" else 1.0
        return (tier, -score)

    matched.sort(key=_rank)

    # ── Unmatched results — sorted by semantic proximity ──
    unmatched = []
    for pid, paper in paper_map.items():
        if pid not in matched_ids:
            unmatched.append(MatchResult(
                paper=paper,
                matched_topics=[],
                match_method="none",
                semantic_scores={},
                best_semantic_score=all_best_scores.get(pid, 0.0),
            ))

    unmatched.sort(key=lambda r: r.best_semantic_score, reverse=True)

    print(f"[filter] {len(matched)} matched, {len(unmatched)} unmatched.")
    return matched, unmatched