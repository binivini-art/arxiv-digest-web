# arXiv Digest

A daily personalized arXiv paper digest, published as a static GitHub Pages site.

Papers are fetched from arXiv, filtered by your topics using keyword and semantic matching, ranked by match quality, and served as a clean browsable feed. A digest email lands in your inbox every morning.

## How it works

Two GitHub Actions jobs run on a schedule:

| Job | Time | What it does |
|-----|------|-------------|
| `pipeline` | 00:30 UTC Tue–Sat | Fetch papers, filter, rank, save JSONs, deploy to Pages |
| `notify` | 22:00 UTC Mon–Fri (= 07:00 KST) | Read latest stored data, send digest email |

All dates follow **UTC**, matching arXiv's own submission-day boundary. The pipeline also performs a 2-day backfill check on each run — if arXiv publishes papers late, they get patched into the correct day's JSON automatically.

## Setup

### 1. Fork / clone this repo

### 2. Enable GitHub Pages
- Go to repo **Settings → Pages**
- Set source to **Deploy from branch → gh-pages → / (root)**

### 3. Add GitHub Secrets
Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `NOTIFY_GMAIL_USER` | Your Gmail address |
| `NOTIFY_GMAIL_APP_PASS` | 16-character Gmail App Password (see below) |
| `NOTIFY_TO` | Recipient address |

**Getting a Gmail App Password:**
1. Enable 2-Step Verification on your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create an app password for "Mail" — copy the 16-character code (no spaces)

### 4. Run it
- Go to **Actions → Daily arXiv Digest → Run workflow** to trigger manually
- Or wait — it runs automatically on schedule

## Customizing topics

Edit `config.yaml` and push. Topics only need a name and description — keyword terms are managed separately in `data/terms/`.

```yaml
topics:
  - name: My Topic
    enabled: true
    description: "Plain English description of what this topic is about"
```

- **`name`** — display name, also used as the ID for the terms file
- **`description`** — used for semantic (embedding) matching
- **`enabled`** — set to `false` to temporarily disable without deleting

### How terms work

Each topic has a corresponding keyword file at `data/terms/{topic-id}.yaml`:

```yaml
topic: My Topic
generated_at: 2026-03-17T00:00:00+00:00
terms:
  - my key phrase
  - another specific term
```

- If the file **exists**, it is loaded as-is
- If the file **doesn't exist**, it is auto-generated using KeyBERT (offline, uses the same embedding model as semantic matching)
- Edit the file directly to tune keywords — it is never overwritten automatically

After changing topics or terms, run `--refilter` to re-score all stored history without re-fetching from arXiv.

## Running locally

```bash
pip install -r requirements.txt
python pipeline/main.py --preview   # fetch + open browser
```

For local email testing, create a `.env` file (never commit this):

```bash
export NOTIFY_GMAIL_USER=you@gmail.com
export NOTIFY_GMAIL_APP_PASS="yourapppassword"
export NOTIFY_TO=you@gmail.com
```

Then:

```bash
source .env && python pipeline/main.py --notify
```

## Pipeline flags

```
python pipeline/main.py                           # normal daily run
python pipeline/main.py --refilter                # re-filter all stored JSONs (no network)
python pipeline/main.py --refetch                 # re-download + re-filter everything
python pipeline/main.py --regen-terms             # regenerate all term files with KeyBERT
python pipeline/main.py --regen-terms --refilter  # regenerate terms + re-score history
python pipeline/main.py --notify-only             # send email from latest stored data only
python pipeline/main.py --notify                  # run pipeline + send email
python pipeline/main.py --preview                 # run pipeline + open browser
```

## How papers are ranked

Matched papers are sorted by match quality within each topic:

1. **Keyword + semantic** — matched by both an exact term and embedding similarity
2. **Keyword only** — exact term hit; precise and reliable
3. **Semantic only** — ranked by embedding similarity score descending

Each card in the feed shows a small badge indicating how it was matched. Unmatched papers appear below a divider, sorted by semantic proximity (closest-to-relevant first).

## Late additions (backfill)

arXiv occasionally publishes papers 1–2 days after their submission date. Each pipeline run checks the two most recently stored days for new arrivals and patches them in. Backfilled papers appear at the top of their day's list with a ⚡ **Late addition** badge, and the digest email includes an amber notice when this happens.

## File structure

```
arxiv-digest/
├── .github/workflows/daily.yml  # two-job cron schedule
├── pipeline/
│   ├── main.py                  # orchestrator + CLI flags
│   ├── fetcher.py               # arXiv API client (UTC clock)
│   ├── filter.py                # keyword + semantic matching + ranking
│   ├── storage.py               # JSON persistence + backfill patching
│   ├── terms.py                 # per-topic term file management (KeyBERT)
│   └── notifier.py              # Gmail SMTP HTML digest email
├── static/
│   ├── display.js               # shared frontend rendering
│   └── global.css               # responsive styles
├── data/
│   ├── papers/                  # YYYY-MM-DD.json per day
│   ├── terms/                   # {topic-id}.yaml per topic
│   └── available_dates.json     # date index for the frontend calendar
├── index.html                   # main digest page (tabbed, last 7 days)
├── date.html                    # archive page for older dates
├── config.yaml                  # your topics
└── requirements.txt
```