# M3: Fact Checker / Crawler

**News Intelligence Pipeline — Crawl, Analyze, Compose**

M3 crawls trusted news sources (government sites, UN, wire services, think tanks, OSINT dashboards) on a configurable schedule, stores articles in SQLite, scores their newsworthiness with Gemini, and generates briefing videos or short clips for the M4 live broadcast module.

Designed for general-purpose news content creators — YouTubers, independent journalists, and broadcast teams — covering any topic. The default prompt set is intentionally broad; see [Customising Prompts](#customising-prompts) to tailor it to a specialist vertical.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  config/sources.yaml                                            │
│  (11 sources: gov, military, UN, wire services, think tanks)    │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────┐     ┌─────────────────────────────────┐
│  crawler/              │     │  store/                          │
│  drission_crawler.py   │────▶│  article_store.py  (SQLite)     │
│                        │     │  data/articles.db                │
│  • Headless Chromium   │     │                                  │
│  • Screenshot capture  │     │  Tables:                         │
│  • HTML + text extract │     │    articles (crawl + analysis)   │
└────────────────────────┘     └──────────┬──────────────────────┘
                                          │
                                          ▼
                               ┌─────────────────────────┐
                               │  analyst/                │
                               │  gemini_analyst.py       │
                               │                          │
                               │  • Summary generation    │
                               │  • Importance scoring    │
                               │  • Topic classification  │
                               │  • Entity extraction     │
                               │  • Breaking detection    │
                               └──────────┬──────────────┘
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                  ▼
              ┌──────────────┐  ┌──────────────────┐  ┌───────────┐
              │ composer/    │  │ scheduler/        │  │ M4        │
              │ briefing_    │  │ crawl_            │  │ (consumer)│
              │ composer.py  │  │ scheduler.py      │  │           │
              │              │  │                   │  │ • search()│
              │ • Briefing   │  │ • APScheduler     │  │ • get_    │
              │   video      │  │ • Per-source jobs │  │   breaking│
              │ • Short clips│  │ • Breaking flag   │  │ • get_    │
              └──────┬───────┘  └───────────────────┘  │   today   │
                     │                                  └───────────┘
                     ▼
              ┌──────────────┐
              │ shared/      │  (project root)
              │              │
              │ • image_gen  │
              │ • narrator   │
              │ • video_comp │
              └──────────────┘
```

## Data Flow

```
1. CRAWL     sources.yaml → DrissionCrawler → ArticleStore (raw articles)
2. ANALYZE   ArticleStore (unanalyzed) → GeminiAnalyst → ArticleStore (scored)
3. COMPOSE   ArticleStore (top articles) → BriefingComposer → video / clips
4. REGISTER  BriefingComposer → ContentIndex (content_index.json) → M4
```

Each step is independently executable via the CLI, or chained with `pipeline`.

## CLI Reference

All commands run from `compe_M3/`:

```bash
uv run main.py [--debug] <command> [options]
```

The `--debug` flag enables verbose output (DB stats, article details, script previews).

### crawl — Source Crawling

```bash
uv run main.py crawl                        # Crawl un_news (default)
uv run main.py crawl --source centcom        # Crawl specific source
uv run main.py crawl --all                   # Crawl all sources
uv run main.py crawl --list-sources          # List registered sources
```

For each source, the crawler visits the top page, takes a screenshot, extracts article links using CSS selectors, then visits each linked article (up to 5 per source) for screenshot + text extraction. Results are saved to `data/articles.db`.

### analyze — Gemini Analysis

```bash
uv run main.py analyze                       # Analyze all unanalyzed articles
uv run main.py analyze --limit 5             # Limit to 5 articles
```

Each article is sent to Gemini 2.5 Flash for structured analysis. The model returns a JSON object with summary, importance_score (0-10), topics, key_entities, sentiment, and actionable intel flags. Results are written back to the same SQLite row.

### compose — Video Generation

```bash
uv run main.py compose --dry-run             # Generate script only
uv run main.py compose                       # Full briefing video
uv run main.py compose --short-clips         # Per-article 30s clips
uv run main.py compose --short-clips --limit 1
uv run main.py compose --hours 6             # Articles from last 6 hours
```

Two modes are available:

**Briefing mode** (default): Selects top articles by importance, generates a 5-8 minute narrated briefing video with title card, infographics, and TTS audio via the `shared/` pipeline.

**Short clip mode** (`--short-clips`): Generates one 30-second clip per article (4 sentences, ~75 words). Uses the article's screenshot as the video background if available, otherwise generates a title card.

Output directory: `data/output/briefing_<timestamp>/` or `data/output/clips_<timestamp>/`.

### schedule — Background Scheduler

```bash
uv run main.py schedule                      # Daemon mode (Ctrl+C to stop)
uv run main.py schedule --duration 30        # Auto-stop after 30s (testing)
```

Uses APScheduler (BackgroundScheduler) to run per-source crawl jobs at the intervals defined in `sources.yaml`. Thread-based, works on both Windows and Linux. Each job crawls + stores articles. Articles with importance_score >= 9.0 are flagged as BREAKING and registered in ContentIndex immediately.

### pipeline — Full Pipeline

```bash
uv run main.py pipeline                      # crawl_all → analyze → compose
uv run main.py pipeline --dry-run            # Skip video generation
uv run main.py pipeline --hours 48           # Compose from last 48 hours
```

Runs `crawl --all`, then `analyze`, then `compose` in sequence. Intended for cron jobs on GCE.

## sources.yaml Schema

Each entry in `config/sources.yaml` defines a crawl target:

```yaml
sources:
  - id: centcom                    # Unique identifier (used in DB, file paths, CLI)
    name: "U.S. Central Command"   # Human-readable display name
    url: "https://www.centcom.mil/MEDIA/NEWS-ARTICLES/"  # Entry point URL
    tier: 1                        # Trust tier (1=highest, 3=lowest)
    credibility: 5                 # Credibility score (1-5, passed to Gemini)
    type: military                 # Source type (see below)
    country: US                    # Country code or INTL
    crawl_interval_min: 10         # Scheduler interval in minutes
    selectors:                     # CSS selectors for link extraction
      news_links: "a[href*='/MEDIA/NEWS-ARTICLES/Article/']"
    notes: "Optional notes"        # Free-text notes (not used in code)
```

### Source Types

| Type | Description | Examples |
|------|-------------|---------|
| `government` | Official government departments | U.S. DoD |
| `military` | Military command pages | CENTCOM |
| `international_org` | UN and affiliated bodies | UN News, UNHCR |
| `wire_service` | Wire services / news agencies | Reuters, AP |
| `news_liveblog` | Live-updating news blogs | Al Jazeera Live Blog |
| `think_tank` | Policy research institutions | ISW, Critical Threats |
| `research` | Academic / conflict research | Understanding War |
| `osint_dashboard` | Map-based OSINT tools | LiveUAMap |
| `social_official` | Official social media accounts | (X/Twitter timelines) |

### Tier System

| Tier | Credibility | Description | Crawl Interval |
|------|-------------|-------------|----------------|
| 1 | 5/5 | Official government & international organizations | 10-60 min |
| 2 | 4/5 | Major wire services & high-trust media | 5 min |
| 3 | 3-4/5 | Think tanks, OSINT dashboards, research | 10-60 min |

Tier and credibility values are passed to Gemini during analysis so the model can weigh source reliability when scoring importance.

### Selectors

The `selectors.news_links` field contains a CSS selector string used by BeautifulSoup to extract article links from the top page HTML. If the selector yields fewer than 3 results, the crawler falls back to heuristic link extraction using URL path keywords (`/news/`, `/article/`, `/story/`, etc.).

Set `news_links: null` for sources where only the top page screenshot is meaningful (e.g., OSINT map dashboards).

## Database Schema

All data lives in `data/articles.db` (SQLite):

```sql
CREATE TABLE articles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           TEXT NOT NULL,       -- matches sources.yaml id
    source_name         TEXT NOT NULL,
    url                 TEXT NOT NULL,
    url_hash            TEXT NOT NULL,       -- MD5 of URL (dedup key)
    title               TEXT,
    text_content        TEXT,                -- extracted article text
    screenshot_path     TEXT,                -- path to PNG screenshot
    html_path           TEXT,                -- path to saved HTML
    credibility         INTEGER,             -- from sources.yaml
    tier                INTEGER,             -- from sources.yaml
    is_top_page         BOOLEAN DEFAULT FALSE,

    -- Gemini analysis results (populated by analyze step)
    summary             TEXT,
    importance_score    REAL,                -- 0.0-10.0
    importance_reason   TEXT,
    topics              TEXT,                -- JSON array ["politics", "economy", "environment"]
    key_entities        TEXT,                -- JSON array ["Person Name", "Organisation", "Location"]
    sentiment           TEXT,                -- positive|negative|neutral|alarming
    has_actionable_intel BOOLEAN,
    ai_image_path       TEXT,

    crawled_at          TEXT NOT NULL,        -- ISO 8601 UTC
    analyzed_at         TEXT,                 -- set after Gemini analysis
    used_in_briefing    BOOLEAN DEFAULT FALSE,
    is_breaking         BOOLEAN DEFAULT FALSE,

    UNIQUE(url_hash, crawled_at)
);
```

### Importance Scoring Guide

The scoring rubric passed to Gemini:

| Score | Level | Criteria |
|-------|-------|----------|
| 9-10 | Breaking | Major confirmed event, significant policy change, large-scale incident |
| 7-8 | High | Notable development, new official statement, trending story |
| 5-6 | Medium | Ongoing situation update, background context, expert opinion |
| 3-4 | Low | Soft news, editorial, analysis without new facts |
| 0-2 | Minimal | Tangential, outdated, promotional, or redundant content |

Articles scoring >= 9.0 are automatically flagged as BREAKING by the scheduler and registered in ContentIndex for M4's ticker overlay.

## Output Directory Structure

### Briefing Video

```
data/output/briefing_20260315_120000/
├── briefing_plan.json        # Article selection & intermediate structure
├── script.txt                # Generated narration script
├── images/                   # Title card + infographic slides
├── audio/                    # TTS audio segments
└── video/                    # Final MP4
```

### Short Clips

```
data/output/clips_20260315_120000/
├── clips_manifest.json       # Summary of all clips
├── clip_001/
│   ├── script.txt            # 75-word narration
│   ├── images/               # Title card or screenshot
│   ├── audio/                # TTS segment
│   └── clip_001.mp4
├── clip_002/
│   └── ...
```

## Integration with Other Modules

### M4 (Live Broadcast)

M4 consumes M3 data in two ways:

**ContentIndex** (`content_index.json` at project root): M3 registers briefing videos, short clips, and BREAKING screenshots here. M4's Gemini Live client queries ContentIndex to find relevant media when the journalist asks "show me the latest on Iran."

**ArticleStore direct access**: M4 can query the SQLite database for real-time data using methods like `search(query)`, `get_breaking()`, and `get_today_titles()`.

### M1 (Data Collection)

M3 shares the `shared/` pipeline modules with M1 for video generation (image generation, TTS narration, ffmpeg composition). M3's `BriefingComposer` converts its article data into M1-compatible structures before calling these shared functions.

### GCS Sync

On GCE, M3 output is synced to Google Cloud Storage via cron:
```bash
gcloud storage rsync data/output/ gs://<bucket>/m3/output/ --recursive
gcloud storage cp data/articles.db gs://<bucket>/m3/articles.db
```

The Windows broadcast station pulls with `pull_from_gcs.ps1 -m3only`.

## Configuration

### Environment Variables

M3 requires both `GOOGLE_API_KEY` and `LANGUAGE` set in the project root `.env` file:

```
GOOGLE_API_KEY=your_gemini_api_key
LANGUAGE=ja        # BCP-47 code: ja, en, ko, zh, fr, de, es, ...
```

`GeminiClient` resolves `LANGUAGE` on instantiation and exposes it as `self.language` (a `LanguageConfig` with `bcp47_code` and `prompt_lang`). All components that generate user-facing text read the language from the client automatically — no extra configuration is needed per component.

#### Language output behaviour

| Field | Language |
|-------|----------|
| `summary` | Output language (set by `LANGUAGE`) |
| `importance_reason` | Output language (set by `LANGUAGE`) |
| Briefing script | Output language (set by `LANGUAGE`) |
| Short clip script | Output language (set by `LANGUAGE`) |
| `topics` | **English always** (for consistent downstream filtering) |
| `key_entities` | **English always** (for consistent downstream filtering) |
| Log messages / internal IDs | **English always** |

If `LANGUAGE` is not set, M3 defaults to `en` / English. Unsupported BCP-47 codes also fall back to English with a warning log.

The `GeminiClient` searches for `.env` in this order:
1. `WeaveCastStudio/.env` (project root — recommended)
2. Current directory `.env`
3. `config/.env` (legacy)
4. `../config/.env` (legacy)

### Gemini Models Used

| Component | Model | Purpose |
|-----------|-------|---------|
| GeminiAnalyst | `gemini-2.5-flash` | Article analysis (summary, scoring) |
| BriefingComposer | `gemini-2.5-flash-lite` | Script generation (briefing & clips) |
| GeminiClient default | `gemini-2.5-flash-lite` | General text/JSON generation |

## Customising Prompts

The default prompts are written for a general news audience. Each prompt file contains a `NOTE FOR MAINTAINERS` comment block that describes exactly what to change for a specialist vertical.

| File | What to customise |
|------|-------------------|
| `analyst/gemini_analyst.py` — `_ANALYSIS_PROMPT_TEMPLATE` | Scoring guide criteria, `topics` example values, system role description |
| `composer/briefing_composer.py` — `generate_m3_script` | Script structure, word count, tone, channel branding |
| `composer/briefing_composer.py` — `_generate_short_clip_script` | Sentence count, word count, tone |

**Example verticals:**

- **Finance / markets**: Change scoring guide to weight earnings, rate decisions, and regulatory news. Add `topics` examples like `"earnings"`, `"markets"`, `"regulation"`.
- **Sports**: Prioritise match results and transfer news. Adjust tone to match-report style.
- **Local news**: Lower the bar for "Breaking" (city council decisions, local incidents). Restrict `sources.yaml` to regional outlets.

## Cron Setup (GCE)

Recommended crontab for production:

```cron
# Full pipeline every 2 hours
0 */2 * * * cd /home/user/WeaveCastStudio/compe_M3 && uv run main.py pipeline >> /var/log/m3_pipeline.log 2>&1

# Sync to GCS after pipeline
10 */2 * * * gcloud storage rsync /home/user/WeaveCastStudio/compe_M3/data/output/ gs://BUCKET/m3/output/ --recursive
15 */2 * * * gcloud storage cp /home/user/WeaveCastStudio/compe_M3/data/articles.db gs://BUCKET/m3/articles.db
```

For near-real-time BREAKING detection, use the scheduler daemon instead:
```bash
# In a screen/tmux session or systemd service
cd compe_M3 && uv run main.py schedule
```
