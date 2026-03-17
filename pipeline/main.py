"""
main.py — Fetch papers, filter, save to JSON, update index.

Usage:
    python pipeline/main.py                 # normal run: fetch missing days only
    python pipeline/main.py --refilter      # re-run filter on all stored JSONs (no network)
    python pipeline/main.py --refetch       # re-download + re-filter all days in the window
    python pipeline/main.py --preview       # normal run + open browser when done

Flags can be combined:
    python pipeline/main.py --refetch --preview
    python pipeline/main.py --refilter --preview
"""

import argparse
import sys
import webbrowser
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from fetcher   import fetch_recent_days
from filter    import filter_papers, Topic
from storage   import (save_papers, date_has_data, list_available_dates,
                       load_papers, load_matched_summaries,
                       update_available_dates, prune_old_files)
from notifier  import send_digest, DaySummary, PaperSummary

CONFIG_PATH = ROOT / "config.yaml"
UTC         = timezone.utc
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


def filter_and_save(day: date, papers: list, topics: list[Topic], config: dict) -> DaySummary:
    """Run filter on a day's papers, save results, return a DaySummary for notification."""
    matched, unmatched = filter_papers(
        papers=papers,
        topics=topics,
        embedding_threshold=config.get("embedding_threshold", 0.35),
        seen_ids=set(),
    )
    print(f"[main] {day}: {len(matched)} matched, {len(unmatched)} unmatched.")
    save_papers(ROOT, day, matched, unmatched)

    paper_summaries = [
        PaperSummary(
            title          = r.paper.title,
            url            = r.paper.url,
            authors        = r.paper.authors,
            abstract       = r.paper.abstract,
            matched_topics = r.matched_topics,
        )
        for r in matched
    ]
    return DaySummary(day=day, matched=paper_summaries, total=len(matched) + len(unmatched))


def run_normal(config: dict, enabled: list[Topic], cats: list[str], max_res: int) -> list[DaySummary]:
    """Default mode: fetch and filter only days missing from storage."""
    today   = datetime.now(UTC).date()
    missing = [today - timedelta(days=i) for i in range(MAX_TABS)
               if not date_has_data(ROOT, today - timedelta(days=i))]

    if not missing:
        print("[main] All days already in storage — nothing to fetch.")
        return []

    print(f"[main] Days missing from storage: {missing}")
    num_days       = (today - min(missing)).days + 1
    papers_by_date = fetch_recent_days(categories=cats, max_results=max_res,
                                       num_days=num_days)
    summaries = []
    for day in missing:
        papers = papers_by_date.get(day, [])
        if papers:
            print(f"\n[main] Filtering {day} ({len(papers)} papers)…")
            summaries.append(filter_and_save(day, papers, enabled, config))
        else:
            print(f"[main] {day}: no papers found (weekend or holiday?).")
    return summaries


def run_refilter(config: dict, enabled: list[Topic]) -> list[DaySummary]:
    """
    Re-run the filter on every stored JSON using the current config.yaml —
    no network calls. Use this after editing topics or thresholds.
    """
    stored = list_available_dates(ROOT)
    if not stored:
        print("[main] No stored data to refilter.")
        return []

    print(f"[main] Refiltering {len(stored)} stored date(s)…")
    summaries = []
    for day in stored:
        papers = load_papers(ROOT, day)
        if papers is None:
            print(f"[main] {day}: file missing, skipping.")
            continue
        print(f"\n[main] Refiltering {day} ({len(papers)} papers)…")
        summaries.append(filter_and_save(day, papers, enabled, config))
    return summaries


def run_refetch(config: dict, enabled: list[Topic], cats: list[str], max_res: int) -> list[DaySummary]:
    """
    Re-download from arXiv and re-filter every day in the retention window,
    overwriting existing JSONs. Use this after changing categories or when
    you suspect stale/incomplete data.
    """
    stored           = list_available_dates(ROOT)
    today            = datetime.now(UTC).date()
    retention_days   = config.get("retention_days", 90)

    tab_days    = [today - timedelta(days=i) for i in range(MAX_TABS)]
    target_days = sorted(set(stored) | set(tab_days), reverse=True)
    cutoff      = today - timedelta(days=retention_days)
    target_days = [d for d in target_days if d >= cutoff]

    if not target_days:
        print("[main] No days to refetch.")
        return []

    num_days = (today - target_days[-1]).days + 1
    print(f"[main] Refetching {len(target_days)} days "
          f"({target_days[-1]} → {target_days[0]})…")
    papers_by_date = fetch_recent_days(categories=cats, max_results=max_res,
                                       num_days=num_days)
    summaries = []
    for day in target_days:
        papers = papers_by_date.get(day, [])
        if papers:
            print(f"\n[main] Filtering {day} ({len(papers)} papers)…")
            summaries.append(filter_and_save(day, papers, enabled, config))
        else:
            print(f"[main] {day}: no papers from arXiv (weekend/holiday?).")
    return summaries


def run_notify_only() -> list:
    """
    Read the most recently stored day's JSON and return its DaySummary.
    No fetching, no filtering — used by the 07:00 KST notification job.
    """
    stored = list_available_dates(ROOT)
    if not stored:
        print("[main] No stored data found for notification.")
        return []

    latest = stored[0]
    print(f"[main] Loading latest stored day for notification: {latest}")
    summary = load_matched_summaries(ROOT, latest)
    if summary is None:
        print(f"[main] Could not load {latest}.")
        return []
    print(f"[main] {latest}: {len(summary.matched)} matched / {summary.total} total")
    return [summary]


def main():
    parser = argparse.ArgumentParser(
        description="arXiv Digest pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--refilter", action="store_true",
        help="Re-run filter on all stored JSONs without hitting arXiv. "
             "Use after editing topics or thresholds in config.yaml.",
    )
    parser.add_argument(
        "--refetch", action="store_true",
        help="Re-download from arXiv and re-filter all days in the window, "
             "overwriting existing data.",
    )
    parser.add_argument(
        "--notify-only", action="store_true",
        help="Skip fetch/filter entirely — just read the latest stored day "
             "and send the digest email. Used by the scheduled 07:00 KST notify job.",
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="Send digest email after the run. Always active in normal mode "
             "when credentials are configured; opt-in for --refilter/--refetch.",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Serve the site locally and open it in a browser when done.",
    )
    args = parser.parse_args()

    if args.refilter and args.refetch:
        parser.error("--refilter and --refetch are mutually exclusive.")
    if getattr(args, 'notify_only', False) and (args.refilter or args.refetch):
        parser.error("--notify-only cannot be combined with --refilter or --refetch.")

    notify_only = getattr(args, 'notify_only', False)
    mode = ("notify-only" if notify_only else
            "refetch"     if args.refetch else
            "refilter"    if args.refilter else
            "normal")

    # Notification: always on for normal + notify-only; opt-in for manual modes
    do_notify = (mode in ("normal", "notify-only")) or args.notify

    print("=" * 60)
    print(f"arXiv Digest  [{mode}"
          f"{'  +notify' if do_notify else ''}"
          f"{'  +preview' if args.preview else ''}]")
    print("=" * 60)

    config  = load_config()
    topics  = build_topics(config)
    enabled = [t for t in topics if t.enabled]
    cats    = config.get("categories", ["cs.AI", "cs.LG", "cs.CL"])
    max_res = config.get("max_results", 2000)
    print(f"[main] Topics: {[t.name for t in enabled]}")

    if mode == "notify-only":
        summaries = run_notify_only()
    elif mode == "refetch":
        summaries = run_refetch(config, enabled, cats, max_res)
    elif mode == "refilter":
        summaries = run_refilter(config, enabled)
    else:
        summaries = run_normal(config, enabled, cats, max_res)

    if mode != "notify-only":
        update_available_dates(ROOT)
        prune_old_files(ROOT, retention_days=config.get("retention_days", 90))

    # ── Notify ────────────────────────────────────────────────────────────────
    if do_notify:
        site_url = config.get("site_url")   # optional in config.yaml
        send_digest(summaries, site_url=site_url)

    print("\n[main] Done.")

    if args.preview:
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