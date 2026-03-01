"""
main.py — Fetch papers, filter, save to JSON, update index.

Usage:
    python pipeline/main.py           # run pipeline
    python pipeline/main.py --preview # run + open browser
"""

import sys
import webbrowser
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from fetcher  import fetch_recent_days
from filter   import filter_papers, Topic
from storage  import (save_papers, date_has_data, list_available_dates,
                      update_available_dates, prune_old_files)

CONFIG_PATH = ROOT / "config.yaml"
KST         = timezone(timedelta(hours=9))
MAX_TABS    = 7


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_topics(config: dict) -> list[Topic]:
    return [
        Topic(
            id=t["name"].lower().replace(" ", "-"),
            name=t["name"],
            terms=t["terms"],
            description=t["description"],
            enabled=t.get("enabled", True),
        )
        for t in config["topics"]
    ]


def filter_and_save(day: date, papers: list, topics: list[Topic], config: dict) -> None:
    """Run filter on a day's papers and save results to storage."""
    matched, unmatched = filter_papers(
        papers=papers,
        topics=topics,
        embedding_threshold=config.get("embedding_threshold", 0.35),
        seen_ids=set(),
    )
    print(f"[main] {day}: {len(matched)} matched, {len(unmatched)} unmatched.")
    save_papers(ROOT, day, matched, unmatched)


def main():
    preview = "--preview" in sys.argv

    print("=" * 60)
    print("arXiv Digest" + (" [preview]" if preview else ""))
    print("=" * 60)

    config   = load_config()
    topics   = build_topics(config)
    enabled  = [t for t in topics if t.enabled]
    cats     = config.get("categories", ["cs.AI", "cs.LG", "cs.CL"])
    max_res  = config.get("max_results", 2000)
    print(f"[main] Topics: {[t.name for t in enabled]}")

    # ── 1. Determine which days need fetching ─────────────────────────────────
    today     = datetime.now(KST).date()
    missing   = [today - timedelta(days=i) for i in range(MAX_TABS)
                 if not date_has_data(ROOT, today - timedelta(days=i))]

    if not missing:
        print("[main] All days already in storage — nothing to fetch.")
    else:
        print(f"[main] Days missing from storage: {missing}")

        # ── 2. One fetch pass covers all missing days ─────────────────────────
        # fetch_recent_days paginates arXiv once, collecting all days in the
        # window. Far more efficient than a separate API crawl per missing day.
        num_days   = (today - min(missing)).days + 1
        papers_by_date = fetch_recent_days(categories=cats, max_results=max_res,
                                           num_days=num_days)

        # ── 3. Filter and save only the days we were missing ──────────────────
        for day in missing:
            papers = papers_by_date.get(day, [])
            if papers:
                print(f"\n[main] Filtering {day} ({len(papers)} papers)…")
                filter_and_save(day, papers, enabled, config)
            else:
                print(f"[main] {day}: no papers found (weekend or holiday?).")

    # ── 3. Update available_dates.json ────────────────────────────────────────
    update_available_dates(ROOT)

    # ── 4. Prune old files ────────────────────────────────────────────────────
    prune_old_files(ROOT, retention_days=config.get("retention_days", 90))

    print(f"\n[main] Done.")

    if preview:
        import http.server, threading, os, time
        os.chdir(ROOT)
        port    = 8787
        handler = http.server.SimpleHTTPRequestHandler
        handler.log_message = lambda *a: None
        server  = http.server.HTTPServer(("", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.3)
        webbrowser.open(f"http://localhost:{port}/index.html")
        print(f"[main] Preview at http://localhost:{port}/index.html  (Ctrl+C to stop)")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    main()