"""
storage.py — Persists papers + filter results as JSON under data/papers/.

Schema per file (data/papers/YYYY/MM/DD.json):
  {
    "date":       "2026-02-27",
    "fetched_at": "2026-02-27T07:00:12+09:00",
    "papers": [
      {
        "id":             "2502.12345",
        "title":          "...",
        "abstract":       "...",
        "authors":        ["A", "B"],
        "url":            "https://arxiv.org/abs/2502.12345",
        "published":      "2026-02-27T00:00:00+00:00",
        "categories":     ["cs.AI"],
        "matched_topics": ["Symbolic AI"],   -- [] if unmatched
        "best_score":     0.72               -- 0.0 if keyword-only match
      },
      ...
    ]
  }

data/available_dates.json:
  {
    "updated_at": "2026-02-27T07:00:00+09:00",
    "latest":     "2026-02-27",
    "dates": {
      "2026": {
        "02": ["27", "26", "25"],
        "01": ["15"]
      }
    }
  }
"""

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

from fetcher import Paper
from filter  import MatchResult

RETENTION_DAYS = 90
KST = timezone(timedelta(hours=9))  # kept for fetched_at display only
UTC = timezone.utc


def _papers_dir(root: Path) -> Path:
    d = root / "data" / "papers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for_date(root: Path, d: date) -> Path:
    p = _papers_dir(root) / str(d.year) / f"{d.month:02d}" / f"{d.day:02d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Serialise ─────────────────────────────────────────────────────────────────

def _paper_to_dict(p: Paper, match: MatchResult | None) -> dict:
    return {
        "id":             p.id,
        "title":          p.title,
        "abstract":       p.abstract,
        "authors":        p.authors,
        "url":            p.url,
        "published":      p.published.isoformat(),
        "updated":        p.updated.isoformat(),
        "categories":     p.categories,
        "matched_topics": match.matched_topics if match else [],
        "match_method":   match.match_method   if match else "none",
        "best_score":     round(match.best_semantic_score, 3) if match else 0.0,
    }


def _dict_to_paper(d: dict) -> Paper:
    return Paper(
        id=d["id"],
        title=d["title"],
        abstract=d["abstract"],
        authors=d["authors"],
        url=d["url"],
        published=datetime.fromisoformat(d["published"]),
        updated=datetime.fromisoformat(d.get("updated", d["published"])),  # fallback for old files
        categories=d["categories"],
    )


# ── Public: save / load ───────────────────────────────────────────────────────

def save_papers(
    root: Path,
    d: date,
    matched: list[MatchResult],
    unmatched: list[MatchResult],
) -> None:
    """
    Save matched + unmatched MatchResults for a given date.
    matched_topics and best_score are embedded directly in each paper record.
    """
    path = _path_for_date(root, d)

    # All papers: matched first, then unmatched — each carries its own MatchResult
    all_results = matched + unmatched
    papers_json = [_paper_to_dict(r.paper, r) for r in all_results]

    payload = {
        "date":       d.isoformat(),
        "fetched_at": datetime.now(KST).isoformat(),
        "papers":     papers_json,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[storage] Saved {len(matched)} matched + {len(unmatched)} unmatched → {path.name}")


def load_papers(root: Path, d: date) -> list[Paper] | None:
    """
    Load raw Paper objects for a given date (for re-filtering if needed).
    Returns None if file doesn't exist.
    """
    path = _path_for_date(root, d)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers  = [_dict_to_paper(p) for p in payload["papers"]]
    print(f"[storage] Loaded {len(papers)} papers ← {path.name}")
    return papers


def load_matched_summaries(root: Path, d: date):
    """
    Load a DaySummary from an already-filtered JSON file.
    Used by --notify-only mode. Returns None if file doesn't exist.
    Imported lazily to avoid circular imports with notifier.
    """
    from notifier import PaperSummary, DaySummary

    path = _path_for_date(root, d)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers  = payload["papers"]

    matched = [
        PaperSummary(
            title          = p["title"],
            url            = p["url"],
            authors        = p["authors"],
            abstract       = p["abstract"],
            matched_topics = p["matched_topics"],
            backfilled     = p.get("backfilled", False),
        )
        for p in papers if p.get("matched_topics")
    ]
    return DaySummary(day=d, matched=matched, total=len(papers))


def date_has_data(root: Path, d: date) -> bool:
    return _path_for_date(root, d).exists()


def load_existing_ids(root: Path, d: date) -> set[str]:
    """Return the set of paper IDs already stored for a given date."""
    path = _path_for_date(root, d)
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {p["id"] for p in payload.get("papers", [])}


def patch_papers(
    root: Path,
    d: date,
    new_matched: list[MatchResult],
    new_unmatched: list[MatchResult],
) -> None:
    """
    Prepend backfilled papers to an existing day's JSON.
    New matched papers go to the very top; new unmatched appended at the end.
    Each backfilled record gets backfilled=true so the frontend can badge them.
    """
    path = _path_for_date(root, d)
    if not path.exists():
        return

    payload      = json.loads(path.read_text(encoding="utf-8"))
    existing     = payload["papers"]

    def to_dict_backfilled(r: MatchResult) -> dict:
        d = _paper_to_dict(r.paper, r)
        d["backfilled"] = True
        return d

    new_matched_dicts   = [to_dict_backfilled(r) for r in new_matched]
    new_unmatched_dicts = [to_dict_backfilled(r) for r in new_unmatched]

    # Matched backfills at top, existing papers in middle, unmatched at end
    payload["papers"] = new_matched_dicts + existing + new_unmatched_dicts
    payload["patched_at"] = datetime.now(UTC).isoformat()

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[storage] Patched {len(new_matched_dicts)} matched + "
          f"{len(new_unmatched_dicts)} unmatched backfills → {path.name}")


def list_available_dates(root: Path) -> list[date]:
    """Return all dates that have saved JSON files, sorted newest-first."""
    dates = []
    for f in _papers_dir(root).glob("*/*/??.json"):
        try:
            day   = int(f.stem)
            month = int(f.parent.name)
            year  = int(f.parent.parent.name)
            dates.append(date(year, month, day))
        except (ValueError, TypeError):
            pass
    return sorted(dates, reverse=True)


# ── available_dates.json ──────────────────────────────────────────────────────

def update_available_dates(root: Path) -> None:
    """
    Rebuild data/available_dates.json from all existing paper JSON files.
    Hierarchical format: { year: { month: [day, ...] } }
    """
    dates = list_available_dates(root)
    if not dates:
        return

    hierarchy: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d in dates:
        y = str(d.year)
        m = str(d.month).zfill(2)
        day = str(d.day).zfill(2)
        hierarchy[y][m].append(day)

    # Convert defaultdicts to plain dicts, days sorted descending
    plain = {
        y: {m: sorted(days, reverse=True) for m, days in months.items()}
        for y, months in sorted(hierarchy.items(), reverse=True)
    }

    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "latest":     dates[0].isoformat(),
        "dates":      plain,
    }

    out = root / "data" / "available_dates.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[storage] Updated available_dates.json ({len(dates)} dates, latest: {dates[0]})")


# ── Prune ─────────────────────────────────────────────────────────────────────

def prune_old_files(root: Path, retention_days: int = RETENTION_DAYS) -> None:
    """Delete paper JSON files older than retention_days."""
    cutoff = datetime.now(UTC).date() - timedelta(days=retention_days)
    papers_dir = _papers_dir(root)
    pruned = 0
    for f in papers_dir.glob("*/*/??.json"):
        try:
            day   = int(f.stem)
            month = int(f.parent.name)
            year  = int(f.parent.parent.name)
            file_date = date(year, month, day)
        except (ValueError, TypeError):
            continue
        if file_date < cutoff:
            f.unlink()
            pruned += 1
            print(f"[storage] Pruned {f.relative_to(papers_dir)}")
    # Remove empty month/year directories
    for month_dir in papers_dir.glob("*/*/"):
        if month_dir.is_dir() and not any(month_dir.iterdir()):
            month_dir.rmdir()
    for year_dir in papers_dir.glob("*/"):
        if year_dir.is_dir() and not any(year_dir.iterdir()):
            year_dir.rmdir()
    if pruned:
        print(f"[storage] Pruned {pruned} file(s).")
    else:
        print(f"[storage] Nothing to prune (retention: {retention_days} days).")