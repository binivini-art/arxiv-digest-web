"""
notifier.py — Send a digest summary email via Gmail SMTP.

Required environment variables (set as GitHub Actions secrets):
    NOTIFY_GMAIL_USER      your Gmail address, e.g. you@gmail.com
    NOTIFY_GMAIL_APP_PASS  16-character App Password (not your login password)
    NOTIFY_TO              recipient address (can be the same as NOTIFY_GMAIL_USER)

Gmail App Password setup:
    1. Enable 2-Step Verification on your Google account.
    2. Go to https://myaccount.google.com/apppasswords
    3. Create an app password for "Mail" → copy the 16-char code.
    4. Add it as a GitHub Secret named NOTIFY_GMAIL_APP_PASS.

If any of the three env vars are missing the notifier silently skips,
so local runs and --refilter/--refetch never fail because of missing creds.
"""

import os
import smtplib
import html as html_lib
from collections import defaultdict
from datetime import date
from email.message import EmailMessage

# ── Types ─────────────────────────────────────────────────────────────────────

class PaperSummary:
    __slots__ = ("title", "url", "authors", "abstract", "matched_topics")

    def __init__(self, title: str, url: str, authors: list[str],
                 abstract: str, matched_topics: list[str]):
        self.title          = title
        self.url            = url
        self.authors        = authors
        self.abstract       = abstract
        self.matched_topics = matched_topics


class DaySummary:
    __slots__ = ("day", "matched", "total")

    def __init__(self, day: date, matched: list[PaperSummary], total: int):
        self.day     = day
        self.matched = matched
        self.total   = total


# ── Date helpers ──────────────────────────────────────────────────────────────

def _fmt_day(d: date) -> str:
    return d.strftime("%b ") + str(d.day)

def _fmt_day_long(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day) + d.strftime(" %Y")


# ── Topic chip colors (mirrors display.js CHIP_PALETTES) ─────────────────────

_PALETTES = [
    ("#dbeafe", "#1d4ed8"),
    ("#dcfce7", "#15803d"),
    ("#fef3c7", "#b45309"),
    ("#fce7f3", "#be185d"),
    ("#ede9fe", "#6d28d9"),
    ("#ccfbf1", "#0f766e"),
]

def _topic_colors(topics: list[str]) -> dict[str, tuple[str, str]]:
    return {t: _PALETTES[i % len(_PALETTES)] for i, t in enumerate(topics)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clip(text: str, limit: int = 240) -> str:
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


# ── HTML builder ──────────────────────────────────────────────────────────────

def _build_html(summaries: list[DaySummary], site_url: str | None) -> str:
    days_with_matches = [s for s in summaries if s.matched]

    all_topics: list[str] = []
    seen_t: set[str] = set()
    for s in days_with_matches:
        for p in s.matched:
            for t in p.matched_topics:
                if t not in seen_t:
                    all_topics.append(t)
                    seen_t.add(t)
    colors = _topic_colors(all_topics)

    e = html_lib.escape

    day_html = ""
    for summary in days_with_matches:
        by_topic: dict[str, list[PaperSummary]] = defaultdict(list)
        for p in summary.matched:
            for t in p.matched_topics:
                by_topic[t].append(p)

        topic_sections = ""
        for topic, papers in by_topic.items():
            bg, fg = colors.get(topic, ("#f1f5f9", "#475569"))

            paper_cards = ""
            for p in papers:
                authors_str = ", ".join(p.authors[:3])
                if len(p.authors) > 3:
                    authors_str += " et al."
                paper_cards += f"""
                <div style="background:#ffffff;border:1px solid #e2e8f0;
                            border-left:4px solid {fg};border-radius:8px;
                            padding:16px 20px;margin-bottom:0.7em;">
                  <a href="{e(p.url)}"
                     style="font-size:15px;font-weight:700;color:#0f172a;
                            text-decoration:none;line-height:1.4;display:block;
                            margin-bottom:5px;">{e(p.title)}</a>
                  <div style="font-size:12px;color:#94a3b8;margin-bottom:10px;">
                    {e(authors_str)}
                  </div>
                  <div style="font-size:13px;color:#475569;line-height:1.65;">
                    {e(_clip(p.abstract))}
                  </div>
                </div>"""

            topic_sections += f"""
            <div style="margin-bottom:28px;">
              <div style="margin-bottom:12px;">
                <span style="background:{bg};color:{fg};border:1.5px solid {fg};
                             border-radius:999px;font-size:11px;font-weight:700;
                             padding:3px 12px;">{e(topic)}</span>
                <span style="font-size:12px;color:#94a3b8;margin-left:8px;">
                  {len(papers)} paper{'s' if len(papers) != 1 else ''}
                </span>
              </div>
              {paper_cards}
            </div>"""

        day_html += f"""
        <div style="margin-bottom:40px;padding-top:1em;">
          <div style="border-bottom:2px solid #e2e8f0;padding-bottom:12px;
                      margin-bottom:22px;">
            <div style="font-size:10px;font-weight:700;letter-spacing:0.15em;
                        text-transform:uppercase;color:#94a3b8;margin-bottom:4px;">
              arXiv Digest
            </div>
            <div style="font-size:22px;font-weight:700;color:#0f172a;
                        letter-spacing:-0.3px;">
              {e(_fmt_day_long(summary.day))}
            </div>
            <div style="font-size:13px;color:#94a3b8;margin-top:4px;">
              {len(summary.matched)} matched &nbsp;·&nbsp; {summary.total} total
            </div>
          </div>
          {topic_sections}
        </div>"""

    if not days_with_matches:
        day_html = """
        <div style="text-align:center;padding:48px 24px;color:#94a3b8;">
          <div style="font-size:32px;margin-bottom:12px;">📭</div>
          <div style="font-size:16px;font-weight:600;color:#475569;">
            No matched papers today
          </div>
          <div style="font-size:13px;margin-top:6px;">
            The pipeline ran successfully — nothing matched your topics.
          </div>
        </div>"""

    SITE_URL = "https://2ood.github.io/arxiv-digest-web"
    cta = f"""
        <div style="text-align:center;margin-top:32px;padding-top:28px;
                    border-top:1px solid #e2e8f0;">
          <a href="{SITE_URL}"
             style="display:inline-block;background:#3b82f6;color:#ffffff;
                    font-size:14px;font-weight:700;text-decoration:none;
                    border-radius:8px;padding:13px 36px;letter-spacing:0.01em;">
            Go to Arxiv Digest →
          </a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f8fafc;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:620px;margin:1em auto;padding:0 16px 48px;">
    {day_html}
    {cta}
    <div style="text-align:center;margin-top:36px;font-size:11px;color:#cbd5e1;">
      arXiv Digest &nbsp;·&nbsp; automated daily digest &nbsp;·&nbsp;
      papers sourced from
      <a href="https://arxiv.org" style="color:#94a3b8;text-decoration:none;">
        arxiv.org
      </a>
    </div>
  </div>
</body>
</html>"""


# ── Plain-text fallback ───────────────────────────────────────────────────────

def _build_plaintext(summaries: list[DaySummary], site_url: str | None) -> str:
    days_with_matches = [s for s in summaries if s.matched]
    if not days_with_matches:
        return "The pipeline ran but found no matched papers.\n"

    lines = []
    for summary in days_with_matches:
        lines += [f"{'─'*56}",
                  f"  {_fmt_day_long(summary.day)}  "
                  f"({len(summary.matched)} matched / {summary.total} total)",
                  f"{'─'*56}"]

        by_topic: dict[str, list[PaperSummary]] = defaultdict(list)
        for p in summary.matched:
            for t in p.matched_topics:
                by_topic[t].append(p)

        for topic, papers in by_topic.items():
            lines.append(f"\n  ▸ {topic}  ({len(papers)} paper{'s' if len(papers)!=1 else ''})\n")
            for p in papers:
                authors_str = ", ".join(p.authors[:3])
                if len(p.authors) > 3:
                    authors_str += " et al."
                lines += [f"  • {p.title}",
                          f"    {authors_str}",
                          f"    {_clip(p.abstract, 200)}",
                          f"    {p.url}", ""]

    if site_url:
        lines += [f"{'─'*56}", f"  Go to Arxiv Digest: https://2ood.github.io/arxiv-digest-web"]

    return "\n".join(lines)


# ── Send ──────────────────────────────────────────────────────────────────────

def send_digest(summaries: list[DaySummary], site_url: str | None = None) -> None:
    """
    Build and send the digest email as multipart HTML + plain-text fallback.
    Silently skips if credentials are not configured.
    """
    gmail_user = os.environ.get("NOTIFY_GMAIL_USER", "").strip()
    app_pass   = os.environ.get("NOTIFY_GMAIL_APP_PASS", "").strip()
    to_addr    = os.environ.get("NOTIFY_TO", "").strip()

    if not all([gmail_user, app_pass, to_addr]):
        print("[notifier] Credentials not set — skipping email notification.")
        return

    days_with_matches = [s for s in summaries if s.matched]
    if days_with_matches:
        total_match = sum(len(s.matched) for s in days_with_matches)
        subject     = f"arXiv Digest · {_fmt_day(days_with_matches[0].day)} — {total_match} matched"
    else:
        subject = "arXiv Digest · no new matches today"

    msg            = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"arXiv Digest <{gmail_user}>"
    msg["To"]      = to_addr
    msg.set_content(_build_plaintext(summaries, site_url))
    msg.add_alternative(_build_html(summaries, site_url), subtype="html")

    try:
        print(f"[notifier] Sending digest to {to_addr}…")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, app_pass)
            smtp.send_message(msg)
        print(f"[notifier] Sent: {subject!r}")
    except smtplib.SMTPAuthenticationError:
        print("[notifier] ERROR: SMTP authentication failed. "
              "Check NOTIFY_GMAIL_APP_PASS — it must be a Gmail App Password, "
              "not your regular login password.")
    except Exception as e:
        print(f"[notifier] ERROR: Failed to send email — {e}")
