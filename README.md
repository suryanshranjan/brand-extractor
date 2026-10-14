# Brand Extractor

[![tests](https://github.com/suryanshranjan/brand-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/suryanshranjan/brand-extractor/actions/workflows/tests.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Find the brands that sponsor or partner with an Instagram account: college fests, events, clubs, creators.
Give it an account; it reads the recent posts (captions, tags, collab and paid-partnership labels, and the
text on posters), looks up every account it finds, and returns a ranked list of **brands**, kept separate
from the artists, people and organisers that were also tagged.

Use it three ways: a **command line**, a **web dashboard**, or a **REST API**.

**Tech stack:** Python · FastAPI · spaCy (NER) · OCR (OCR.space / EasyOCR) · SQLite job queue · Docker · pytest + GitHub Actions

<!-- Add a dashboard screenshot here: ![Dashboard](docs/screenshot.png) -->

## Features

- **Evidence-based scoring:** paid-partnership labels, collab co-authors, sponsor wording ("powered by",
  "gold sponsor:"), tags, mentions and poster text each add weighted points ([details](#how-the-score-works)).
- **Brand vs. person classification:** every account found is looked up and sorted into brands, creators,
  organisations and personal accounts.
- **Two data sources:** the official Instagram Graph API, or the website API for internal use.
- **Resumable scans:** checkpoints survive rate limits, Ctrl+C and server restarts.
- **Multi-tenant web service:** FastAPI with per-key isolation, a job queue and a single-page dashboard.
- **Polite by design:** hourly request cap, backoff, and one scan at a time per session.

---

## Quick start

**Windows:**

```bat
setup.bat
```

**macOS / Linux:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,login,dev]"
python -m spacy download en_core_web_sm
python -m playwright install chromium
cp .env.example .env
```

This installs the package with the dashboard and login support, the spaCy English model and Chromium,
and creates `.env` from `.env.example`.

```bash
brandscan login my_scraper_account          # once: opens a browser, you log in (2FA works)
brandscan scan engifest_dtu -n 100 --login my_scraper_account
brandscan serve                             # dashboard at http://127.0.0.1:8000
```

`python brand_extractor.py` with no arguments asks for the account and options interactively.

## Getting posts: two data sources

| | `web` (default) | `graph` |
|---|---|---|
| How | Instagram's website API with a logged-in account | Meta's official Instagram Graph API ("Business Discovery") |
| Accounts it can read | Any public account | Business and creator accounts only (most fest and brand pages) |
| Signals | Everything: tags, collab co-authors, "Paid partnership" labels, captions, posters | Captions and posters (no tags or partnership labels) |
| Brand vs person check | Uses the account's category | Can't tell businesses from creators |
| Risk | **Against Instagram's terms.** The login account can be restricted. | None: an approved API |
| Setup | `brandscan login USER` or `import-cookies` | A Meta app + token (below) |

Pick with `BRANDSCAN_SOURCE=web|graph` in `.env`, or `--source` per scan.

**Web source sessions:**
- **Dashboard:** the *Connect Instagram* button (top right) opens a login window or takes a pasted
  `sessionid` cookie. Available when the server runs locally without API keys.
- `brandscan login USER`: opens a browser window and you log in yourself. The script never sees the password.
- `brandscan import-cookies cookies.json --as USER`: on a server, export cookies from a logged-in browser
  (for example with the Cookie-Editor extension, "Export as JSON") and import them.
- `IG_SESSIONID=...`: a raw `sessionid` cookie value.

Use a dedicated account, never a personal or business one. Without a login, Instagram allows at most
the latest ~12 posts, and on many networks nothing at all.

**Graph source setup:**
1. Convert an Instagram account you own to a Professional account and link it to a Facebook Page.
2. Create a Meta app (developers.facebook.com), add the Instagram Graph API product, and generate a
   long-lived user token with `instagram_basic` and `pages_show_list`.
3. Find your account's IG user id: `GET /me/accounts`, then `GET /{page-id}?fields=instagram_business_account`.
4. Put them in `.env` as `IG_GRAPH_TOKEN` and `IG_GRAPH_USER_ID`, and set `BRANDSCAN_SOURCE=graph`.

## Reading posters (OCR)

Sponsor logos and "Powered by ..." lines are often only in the image. Two OCR engines are supported:

- **OCR.space** (recommended for servers): a cloud API with nothing heavy to install. Get a free key at
  https://ocr.space/ocrapi (25k images/month) and set `OCRSPACE_API_KEY`.
- **EasyOCR**: runs locally with no key, but needs PyTorch (~2 GB): `pip install -r requirements-ocr.txt`.

`BRANDSCAN_OCR=auto` uses OCR.space if a key is set, otherwise EasyOCR if installed, otherwise no OCR.

## Command line

```
brandscan scan TARGET [-n 50] [--login USER] [--source web|graph] [--no-ocr] [--fresh] [--proxy URL]
brandscan login USER
brandscan import-cookies FILE --as USER
brandscan serve [--host 127.0.0.1] [--port 8000]
```

Scans resume automatically: if Instagram rate-limits you or you press Ctrl+C, run the same command again.
`--fresh` starts over.

## Output

In `output/<target>/`:

| File | What's in it |
|---|---|
| `<target>_brands.csv` | The brand list: confirmed business accounts, plus names with explicit sponsor wording, from the known-brands list, or seen in several posts |
| `<target>_all_entities.csv` | Everything found, including creators, people, organisers and handles that don't exist |
| `<target>_report.json` | Both lists plus scan details, for other tools |

Each row has: name, handle, **type** (`brand` / `creator` / `organization` / `personal` / `not found` /
`unverified`), score, number of posts, primary role (e.g. *Title Sponsor*, *Gold Sponsor*, *Beverage Partner*,
*Paid Partnership*), the evidence, Instagram category, followers, and links to the strongest posts.

### How the score works

Each post adds points per signal (each signal counts once per post):

| Signal | Points |
|---|---|
| Instagram "Paid partnership with X" label | 50 |
| X is a co-author of a collab post | 30 |
| Sponsor wording: "powered by X", "gold sponsor: X", "beverage partner: X" (caption, alt text or poster) | 25 |
| X listed on an "Our sponsors" poster | 12 |
| @X in the caption | 10 |
| X tagged in the photo, or @X on a poster | 8 |
| x.com / x.in in the caption | 6 |
| Organisation name spotted by NLP in the caption | 3 |

Mentions in `#ad` / `#sponsored` posts count 1.5×. Plain names merge into a matching handle ("Red Bull"
into `@redbullindia`). The scanned account and its own branding are always excluded.

**Known brands:** names read from posters are noisy, so a name seen on only one poster is listed as a
brand only if it's in the known-brands list. The built-in list is `brandscan/data/known_brands.txt`; add
your own, one per line, in `<data dir>/known_brands.txt`.

## Web dashboard and API

```bash
brandscan serve                                  # local only: http://127.0.0.1:8000
BRANDSCAN_API_KEYS="acme:...,beta:..." brandscan serve --host 0.0.0.0
```

The dashboard starts scans, shows progress and results, and downloads the CSV and JSON files.
Interactive API docs are at `/api/docs`.

| Method | Path | |
|---|---|---|
| GET | `/api/health` | Status (no key needed) |
| POST | `/api/scans` | `{"target": "engifest_dtu", "limit": 50, "ocr": true, "fresh": false}` returns a job |
| GET | `/api/scans` | Your recent scans |
| GET | `/api/scans/{id}` | Status and progress; brands when finished (`?entities=true` for everything) |
| POST | `/api/scans/{id}/cancel` | Stop a queued or running scan |
| GET | `/api/scans/{id}/files/{brands.csv\|all_entities.csv\|report.json}` | Downloads |

Send the key as an `X-API-Key` header (or `?api_key=` on download links). Keys are `name:key` pairs in
`BRANDSCAN_API_KEYS`, and each key sees only its own scans. The server refuses to listen on a public
address without keys.

Scans run **one at a time**, on purpose. All scans share one Instagram session, and running them in
parallel on one session is what gets accounts blocked. Queued jobs wait their turn, and jobs interrupted
by a restart resume.

## Deploying

```bash
cp .env.example .env        # fill in source, session or token, OCR key, API keys
docker compose up -d --build
```

All state (sessions, results, cache, job database, logs) lives in the `./data` volume. For the web
source, put a session in place once:

```bash
docker compose run --rm brandscan brandscan import-cookies /data/cookies.json --as my_scraper_account
```

Put it behind HTTPS (a reverse proxy such as Caddy or nginx, or your platform's load balancer)
before handing out API keys.

## Settings

All settings come from environment variables or a `.env` file in the working folder. See `.env.example`.

| Variable | Default | |
|---|---|---|
| `BRANDSCAN_DATA_DIR` | current folder | Where sessions, results, cache, the DB and logs go |
| `BRANDSCAN_SOURCE` | `web` | `web` or `graph` |
| `IG_LOGIN` / `IG_SESSIONID` | | Web source session |
| `BRANDSCAN_PROXY` | | One proxy for all web requests (and the login browser) |
| `BRANDSCAN_HOURLY_CAP` | `200` | Max Instagram API calls per hour |
| `IG_GRAPH_TOKEN` / `IG_GRAPH_USER_ID` / `IG_GRAPH_VERSION` | | Graph source |
| `BRANDSCAN_OCR` / `OCRSPACE_API_KEY` | `auto` | OCR engine |
| `BRANDSCAN_MAX_LOOKUPS` | `80` | Accounts looked up per scan to classify them |
| `BRANDSCAN_API_KEYS` | | `name:key,name2:key2` for the web service |

## Design notes and limitations

- **Data source and Instagram's terms.** Logged-in scraping (the `web` source) breaches Instagram's terms,
  and the login account can be restricted. It exists for personal and research use. The `graph` source
  uses Meta's approved API and is the right basis for anything public-facing.
- **One scan at a time per session.** Parallel requests on one Instagram session are what get accounts
  blocked, so the queue is deliberately serial. Scale out with more sessions or tokens and one worker each.
- **API keys live in configuration.** Keys are `name:key` pairs in `.env`; there's no self-service key
  management or usage billing.
- **Results can include individuals.** Tagged creators and people are public data, but treat the
  `all_entities` output with care.
- **Poster text is noisy.** OCR misreads and generic words ("CONGRATULATIONS") are filtered by a
  stop-word lexicon and the known-brands rule, but some noise gets through.

## Project layout

```
brand_extractor.py      interactive entry point (same as `brandscan`)
brandscan/
  cli.py                commands: scan, login, import-cookies, serve
  pipeline.py           one scan end to end (shared by CLI and server)
  instagram.py          web source: API client (rate limits, hourly cap, backoff) + post parsers
  graph.py              graph source: official Instagram Graph API
  auth.py               sessions: browser login, cookie import
  signals.py            what a post says: tags, mentions, labels, sponsor phrases and tiers, posters, NLP
  lexicon.py            generic words and the known-brands list (data/known_brands.txt)
  classify.py           brand vs creator vs organisation vs person
  report.py             scoring, CSV and JSON
  store.py              checkpoints and the account lookup cache
  ocr.py                OCR.space and EasyOCR
  config.py             settings and weights
  server/               FastAPI app, job queue, dashboard
tests/                  python -m pytest
```
