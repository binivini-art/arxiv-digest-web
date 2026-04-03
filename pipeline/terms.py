"""
terms.py — Manages per-topic keyword term lists.

Term files live at data/terms/{topic-id}.yaml, one file per topic.
If a file is missing, terms are generated using KeyBERT (offline, no API).
Existing files are never overwritten automatically — edit them by hand,
delete them, or run --regen-terms to regenerate.

Generation uses KeyBERT with the same sentence-transformer model already
used for semantic matching (all-MiniLM-L6-v2), so no extra model download.

KeyBERT extracts keyphrases by finding n-grams whose embeddings are most
similar to the document embedding. To get broad coverage from a short
description, generation expands the input with a synthetic paragraph built
from the topic name and description before extraction.

Requires:
    pip install keybert          (adds ~1 MB, reuses sentence-transformers)
"""

import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

ROOT      = Path(__file__).parent.parent
TERMS_DIR = ROOT / "data" / "terms"

# Extraction settings
_MODEL        = "all-MiniLM-L6-v2"   # same model as semantic scoring
_TOP_N        = 20                    # max candidates before dedup
_NGRAM_MIN    = 1
_NGRAM_MAX    = 3                     # up to trigrams
_DIVERSITY    = 0.5                   # MMR diversity (0=redundant, 1=diverse)
_SCORE_CUTOFF = 0.25                  # drop phrases below this cosine score


def _terms_path(topic_id: str) -> Path:
    return TERMS_DIR / f"{topic_id}.yaml"


def _expand_doc(name: str, description: str) -> str:
    """
    Build an expanded document from the topic name + description.
    Repeating the name and key phrases gives KeyBERT more signal
    than a single short sentence.
    """
    return (
        f"{name}. {name}. "
        f"{description} "
        f"Research on {name.lower()} includes methods, models, and theory. "
        f"Papers about {name.lower()} address {description.lower()}"
    )


def _generate_terms(name: str, description: str, topic_id: str) -> list[str]:
    """Extract terms with KeyBERT and save to disk."""
    try:
        from keybert import KeyBERT
    except ImportError:
        print("[terms] ERROR: 'keybert' package not installed.")
        print("[terms]   Run: pip install keybert")
        sys.exit(1)

    print(f"[terms] Generating terms for '{name}' with KeyBERT…")

    doc   = _expand_doc(name, description)
    model = KeyBERT(model=_MODEL)

    # Extract with MMR for diversity — avoids near-duplicate phrases
    candidates = model.extract_keywords(
        doc,
        keyphrase_ngram_range = (_NGRAM_MIN, _NGRAM_MAX),
        stop_words            = "english",
        use_mmr               = True,
        diversity             = _DIVERSITY,
        top_n                 = _TOP_N,
    )

    # Filter by score and clean up
    terms = [
        phrase.strip()
        for phrase, score in candidates
        if score >= _SCORE_CUTOFF and len(phrase.strip()) > 2
    ]

    if not terms:
        print(f"[terms] WARNING: No terms extracted for '{name}'. "
              f"Try writing a richer description.")
        terms = []

    print(f"[terms] Extracted {len(terms)} terms for '{name}'.")
    _save_terms(topic_id, name, terms)
    return terms


def _save_terms(topic_id: str, name: str, terms: list[str]) -> None:
    TERMS_DIR.mkdir(parents=True, exist_ok=True)
    path    = _terms_path(topic_id)
    payload = {
        "topic":        name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "terms":        terms,
    }
    path.write_text(
        yaml.dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"[terms] Saved → {path.relative_to(ROOT)}")


def load_or_generate(topic_id: str, name: str, description: str) -> list[str]:
    """
    Return the term list for a topic.
    Loads from data/terms/{topic_id}.yaml if it exists,
    otherwise generates with KeyBERT and saves.
    """
    path = _terms_path(topic_id)
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        terms   = payload.get("terms", [])
        print(f"[terms] Loaded {len(terms)} terms for '{name}' ← {path.name}")
        return terms

    return _generate_terms(name, description, topic_id)


def regenerate(topic_id: str, name: str, description: str) -> list[str]:
    """
    Force regeneration regardless of whether a file exists.
    Called by --regen-terms.
    """
    print(f"[terms] Regenerating '{name}'…")
    return _generate_terms(name, description, topic_id)
