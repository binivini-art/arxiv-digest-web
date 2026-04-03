"""
fetcher.py — Fetches today's papers from arXiv API.

arXiv API hard limit: 300 results per request.
Paginates in chunks of 300, stopping when we hit papers
submitted before today (UTC). Returns a flat list for today only.
Past days are loaded from storage, not fetched live.

Date clock: UTC, matching arXiv's own submission-day boundary.
The pipeline runs just after 00:00 UTC so each run captures the
previous UTC day's complete paper set.
"""

import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date


ARXIV_API     = "https://export.arxiv.org/api/query"
CHUNK_SIZE    = 300
REQUEST_DELAY = 3
MAX_RETRIES   = 3

NS = {"atom": "http://www.w3.org/2005/Atom"}

UTC = timezone.utc


@dataclass
class Paper:
    id:         str
    title:      str
    abstract:   str
    authors:    list[str]
    url:        str
    published:  datetime   # original v1 submission date — for display
    updated:    datetime   # most recent revision date — used for date-window checks
    categories: list[str]


def _fetch_chunk(category_query: str, start: int) -> list[Paper]:
    params = urllib.parse.urlencode({
        "search_query": category_query,
        "sortBy":       "submittedDate",
        "sortOrder":    "descending",
        "start":        start,
        "max_results":  CHUNK_SIZE,
    })
    url = f"{ARXIV_API}?{params}"
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                xml_data = resp.read()
            break
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt < MAX_RETRIES - 1:
                wait = REQUEST_DELAY * (attempt + 2)
                print(f"[fetcher] Attempt {attempt+1} failed ({e}), retrying in {wait}s…")
                time.sleep(wait)
            else:
                raise

    root   = ET.fromstring(xml_data)
    papers = []
    for entry in root.findall("atom:entry", NS):
        raw_id   = entry.find("atom:id", NS).text.strip()
        short_id = raw_id.split("/abs/")[-1].split("v")[0]
        title    = entry.find("atom:title",   NS).text.strip().replace("\n", " ")
        abstract = entry.find("atom:summary", NS).text.strip().replace("\n", " ")
        authors  = [a.find("atom:name", NS).text.strip()
                    for a in entry.findall("atom:author", NS)]
        published = datetime.fromisoformat(
            entry.find("atom:published", NS).text.strip().replace("Z", "+00:00"))
        updated = datetime.fromisoformat(
            entry.find("atom:updated", NS).text.strip().replace("Z", "+00:00"))
        categories = [tag.get("term") for tag in entry.findall("atom:category", NS)]

        papers.append(Paper(
            id=short_id, title=title, abstract=abstract,
            authors=authors, url=f"https://arxiv.org/abs/{short_id}",
            published=published, updated=updated, categories=categories,
        ))
    return papers


def fetch_today(
    categories: list[str] = ["cs.AI"],
    max_results: int = 2000,
) -> tuple[date, list[Paper]]:
    """
    Fetch today's papers from arXiv (today = current date in UTC).
    Stops as soon as a paper from a previous date is encountered.

    Returns (today_utc, papers).
    """
    today_utc    = datetime.now(UTC).date()
    cat_query    = " OR ".join(f"cat:{c}" for c in categories)
    all_papers:  list[Paper] = []
    seen_ids:    set[str]    = set()
    start = 0

    print(f"[fetcher] Fetching today's papers ({today_utc} UTC) from {categories}…")

    while start < max_results:
        print(f"[fetcher] Requesting papers {start+1}–{start+CHUNK_SIZE}…")
        chunk = _fetch_chunk(cat_query, start)

        if not chunk:
            print("[fetcher] Empty response — stopping.")
            break

        new_today = []
        hit_old   = False
        for p in chunk:
            if p.updated.astimezone(UTC).date() >= today_utc:
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    new_today.append(p)
            else:
                hit_old = True
                break  # sorted newest-first; everything after is older

        all_papers.extend(new_today)
        print(f"[fetcher] +{len(new_today)} (total today: {len(all_papers)})")

        if hit_old:
            print("[fetcher] Reached yesterday — done.")
            break
        if len(chunk) < CHUNK_SIZE:
            print("[fetcher] Partial page — end of results.")
            break

        start += CHUNK_SIZE
        print(f"[fetcher] Waiting {REQUEST_DELAY}s…")
        time.sleep(REQUEST_DELAY)

    print(f"[fetcher] Done. {len(all_papers)} papers for {today_utc}.")
    return today_utc, all_papers


def fetch_recent_days(
    categories: list[str] = ["cs.AI"],
    max_results: int = 2000,
    num_days: int = 7,
) -> dict[date, list[Paper]]:
    """
    Fetch papers for the past num_days in a single paginated pass.
    Stops as soon as all papers in a chunk are older than the window.

    Returns dict: date → [Paper, ...], sorted newest-first.
    Much more efficient than calling fetch_date() per day separately.
    """
    from collections import defaultdict

    today_utc = datetime.now(UTC).date()
    cutoff    = today_utc - timedelta(days=num_days - 1)
    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    grouped:  dict[date, list[Paper]] = defaultdict(list)
    seen_ids: set[str] = set()
    start = 0

    print(f"[fetcher] Fetching {num_days} days ({cutoff} → {today_utc} UTC)…")

    while start < max_results:
        print(f"[fetcher] Requesting papers {start+1}–{start+CHUNK_SIZE}…")
        chunk = _fetch_chunk(cat_query, start)

        if not chunk:
            print("[fetcher] Empty response — stopping.")
            break

        all_too_old = True
        for p in chunk:
            paper_date = p.updated.astimezone(UTC).date()
            if paper_date >= cutoff:
                all_too_old = False
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    grouped[paper_date].append(p)

        total = sum(len(v) for v in grouped.values())
        print(f"[fetcher] {total} papers in window so far across {len(grouped)} days.")

        if all_too_old:
            print("[fetcher] All papers older than window — stopping.")
            break
        if len(chunk) < CHUNK_SIZE:
            print("[fetcher] Partial page — end of results.")
            break

        start += CHUNK_SIZE
        print(f"[fetcher] Waiting {REQUEST_DELAY}s…")
        time.sleep(REQUEST_DELAY)

    result = dict(sorted(grouped.items(), reverse=True))
    for d, papers in result.items():
        print(f"[fetcher]   {d}: {len(papers)} papers")
    print(f"[fetcher] Done. {sum(len(v) for v in result.values())} total papers.")
    return result