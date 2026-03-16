# WeaveCastStudio

**AI-Powered Multilingual Live Broadcast Assistant**

WeaveCastStudio is an end-to-end system that automates news collection, fact-checking, video generation, and real-time live broadcast support for journalists. A Gemini-powered AI co-pilot listens to the journalist's voice commands during a live stream and controls on-screen media — playing video clips, showing maps, and surfacing breaking news — so the journalist can focus on reporting.

![System Architecture](docs/weavecaststudio_architecture_simple.png)

## How It Works

The system consists of four modules split across two environments:

### Google Cloud (GCE — runs 24/7)

| Module | Role | Key Tech |
|--------|------|----------|
| **M1: Data Collection & Video Generation** | Collects government statements via Gemini + Google Search, summarizes them, generates narration (TTS), images, and composes briefing videos. Optionally uploads to YouTube. | Gemini 2.5 Flash, nodriver, ffmpeg, YouTube Data API v3 |
| **M3: Fact Checker / Crawler** | Crawls trusted sources (gov sites, UN, OSINT) on a schedule, stores articles in SQLite, scores importance with Gemini, and flags breaking news. | DrissionPage, Gemini + Grounding, APScheduler |

### Windows PC (Broadcast Station)

| Module | Role | Key Tech |
|--------|------|----------|
| **M4: Live Broadcast** | Gemini Live API voice client. Journalist speaks (push-to-talk F9), Gemini responds with voice + function calls to play videos, show images, and explain context. Runs a breaking-news ticker overlay for OBS. | Gemini Live API, PyAudio, python-vlc, tkinter, OBS |

### Data Flow

```
GCE (M1/M3)  ──→  GCS Bucket  ──→  Windows PC (M4)
  cron jobs        gcloud rsync       pull_from_gcs.ps1
```

M1 and M3 run on cron, push results (videos, images, JSON, SQLite DB) to a GCS bucket.
The Windows broadcast station pulls them with `pull_from_gcs.ps1` before going live.
M4 reads the local data and serves it during the broadcast.

## Prerequisites

- **Google Cloud** account with billing enabled
- **Gemini API key** (`GOOGLE_API_KEY`)
- **Python 3.11+** managed via [uv](https://docs.astral.sh/uv/)
- **Chromium** (for DrissionPage / nodriver on GCE)
- **ffmpeg** (for video composition on GCE)
- **OBS Studio** (for live streaming on Windows)
- **Google Cloud SDK** (`gcloud`) on both GCE and Windows

## Repository Structure

```
WeaveCastStudio/
├── README.md                  # ← This file
├── .env                       # GOOGLE_API_KEY + LANGUAGE (git-ignored, shared by all modules)
├── .env.sample                # Template for .env
├── content_index.py           # Shared module: content registry for M1/M3 → M4
├── pull_from_gcs.ps1          # Windows: pull GCS data to local
├── pyproject.toml             # uv project definition
│
├── shared/                    # Common pipeline modules for M1/M3
│   ├── __init__.py
│   ├── language_utils.py      # BCP-47 language config loader (reads LANGUAGE from .env)
│   ├── source_collector.py    # Phase 1: Gemini Search + nodriver
│   ├── summarizer.py          # Phase 1: Structured JSON summary
│   ├── script_writer.py       # Phase 2: Narration script
│   ├── image_generator.py     # Phase 2: Infographic images
│   ├── narrator.py            # Phase 3: TTS audio
│   └── video_composer.py      # Phase 4: ffmpeg video composition
│
├── IaC/                       # Terraform config for GCE + GCS
│   ├── main.tf
│   ├── variables.tf
│   ├── startup.sh
│   ├── terraform.tfvars.example
│   └── README.md
│
├── gcp/                       # GCE deployment guides & sync scripts
│   ├── README.md              # English deploy guide
│   └── README_ja.md           # Japanese deploy guide
│
├── compe_M1/                  # Module M1: Data Collection & Video Generation
│   ├── main.py                # Pipeline entry point (--phase 1..5)
│   ├── requirements.txt
│   ├── README.md              # Detailed M1 docs (pipeline, costs, etc.)
│   ├── config/
│   │   └── topics.yaml        # Topic definitions
│   └── uploader/
│       └── youtube_uploader.py   # Phase 5: YouTube upload
│
├── compe_M3/                  # Module M3: Fact Checker / Crawler
│   ├── main.py                # Unified CLI (crawl / analyze / compose / schedule / pipeline)
│   ├── requirements_m3.txt
│   ├── config/
│   │   └── sources.yaml       # Crawl target definitions (gov, UN, OSINT)
│   ├── crawler/
│   │   └── drission_crawler.py
│   ├── store/
│   │   └── article_store.py   # SQLite article storage
│   ├── analyst/
│   │   ├── gemini_client.py
│   │   └── gemini_analyst.py  # Importance scoring + breaking detection
│   ├── composer/
│   │   └── briefing_composer.py
│   └── scheduler/
│       └── crawl_scheduler.py
│
├── compe_M4/                  # Module M4: Live Broadcast Client
│   ├── gemini_live_client.py  # Main entry point
│   ├── media_window.py        # tkinter video/image display
│   ├── breaking_news_server.py# HTTP+SSE server for OBS ticker
│   ├── overlay/
│   │   └── ticker.html        # OBS browser source (breaking news)
│   ├── OBS_SETUP.md           # OBS configuration guide
│   ├── DEMO_SCRIPT.md         # Demo recording script
│   └── demo_setup.py          # Demo data seeding & breaking trigger
│
└── docs/
    └── weavecaststudio_architecture_simple.png
```

> **Files to delete** (development artifacts, not needed in the repo):
> - `compe_M3/p.py` — one-off DrissionPage test
> - `compe_M3/test_phase1.py` — replaced by `main.py crawl`
> - `compe_M3/test_phase2.py` — replaced by `main.py analyze`
> - `compe_M3/test_phase3.py` — replaced by `main.py compose`
> - `compe_M3/test_phase4.py` — replaced by `main.py schedule` / `main.py pipeline`
> - `compe_M3/shared/` — symlink to compe_M1/agents/ (replaced by top-level shared/)
> - `compe_M1/check_prompts.py` — prompt debugging script
> - `compe_M1/test_grounding.py` — Grounding API test
> - `compe_M4/test_media_playback.py` — VLC playback test
> - `compe_M4/test_gemini.py` — Gemini API test
> - `compe_M4/test_vlc.py` — VLC binding test
> - `compe_M4/test_breaking_news.py` — ticker server test

## Setup

### 1. GCE Infrastructure (M1 + M3)

**Option A — Terraform (recommended):**

```bash
cd IaC/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: set project_id, google_api_key, etc.
terraform init && terraform apply
```

**Option B — Manual:**

Follow `gcp/README.md` (English) or `gcp/README_ja.md` (Japanese) for step-by-step GCE setup.

**After instance is ready:**

```bash
gcloud compute ssh weavecast-collector --zone=asia-northeast1-b

cd ~/WeaveCastStudio
uv sync

# Configure environment (project root — shared by all modules)
cp .env.sample .env
# Edit .env: set GOOGLE_API_KEY and LANGUAGE

# Verify M3
cd compe_M3 && uv run main.py crawl

# Verify M1
cd ../compe_M1 && uv run main.py --phase 1

# Set up cron (see gcp/README.md Step 10)
crontab -e
```

### 2. Windows Broadcast Station (M4)

```powershell
# Clone & install
git clone https://github.com/webbigdata-jp/WeaveCastStudio.git
cd WeaveCastStudio
uv sync

# Configure environment
cp .env.sample .env
# Edit .env: set GOOGLE_API_KEY and LANGUAGE

# Pull data from GCS
.\pull_from_gcs.ps1

# Set up OBS (see compe_M4/OBS_SETUP.md)

# Launch
cd compe_M4
python gemini_live_client.py
```

**OBS setup summary:**
1. Add a **Window Capture** source for the MediaWindow (tkinter)
2. Add a **Browser** source pointing to `http://localhost:8765/overlay` (1920×1080)
3. Layer order: Ticker (top) → MediaWindow → other sources
4. See `compe_M4/OBS_SETUP.md` for full details

### 3. Data Sync (GCE → GCS → Windows)

**GCE → GCS** is handled by scripts in `gcp/` (run via cron or manually with `gcloud storage rsync`).

**GCS → Windows:**

```powershell
# Pull all data
.\pull_from_gcs.ps1

# Pull M3 only
.\pull_from_gcs.ps1 -m3only

# Pull M1 only
.\pull_from_gcs.ps1 -m1only
```

## Environment Variables

| Variable | Where | Description |
|----------|-------|-------------|
| `GOOGLE_API_KEY` | `.env` (project root) | Gemini API key |
| `LANGUAGE` | `.env` (project root) | Output language as a BCP-47 code (e.g. `ja`, `en`, `ko`) |

All modules (M1, M3, M4) load `.env` from the project root (`WeaveCastStudio/.env`).

### LANGUAGE configuration

`LANGUAGE` is a [BCP-47 language code](https://ai.google.dev/gemini-api/docs/live-api/capabilities#supported-languages) that controls the output language across all modules:

- **Live API (M4):** passed directly as the session language code (e.g. `ja`).
- **Standard prompts (M1/M3):** automatically converted to a natural-language name (e.g. `Japanese`) by `shared/language_utils.py` and injected into Gemini prompts, so narration scripts, image captions, and summaries are generated in the chosen language.

The conversion table lives in `shared/language_utils.py`. To add a language, append its BCP-47 code and English name to `_BCP47_TO_PROMPT_LANG`. Unsupported codes fall back to `en` / `English`.

```bash
# Example .env
GOOGLE_API_KEY=your_api_key_here
LANGUAGE=ja   # Japanese output
```

#### M3-specific language behaviour

`GeminiClient` resolves `LANGUAGE` on instantiation and exposes it as `self.language` (a `LanguageConfig` with `bcp47_code` and `prompt_lang`). All M3 components that generate user-facing text read this value from the client — no extra configuration is needed.

| Field | Language |
|-------|----------|
| `summary` | Output language (set by `LANGUAGE`) |
| `importance_reason` | Output language (set by `LANGUAGE`) |
| Briefing script | Output language (set by `LANGUAGE`) |
| Short clip script | Output language (set by `LANGUAGE`) |
| `topics` | **English always** (for consistent downstream filtering) |
| `key_entities` | **English always** (for consistent downstream filtering) |
| Log messages / internal IDs | **English always** |

> **Customising M3 prompts for your content vertical:**
> `compe_M3/analyst/gemini_analyst.py` contains `_ANALYSIS_PROMPT_TEMPLATE` and
> `compe_M3/composer/briefing_composer.py` contains `generate_m3_script` and
> `_generate_short_clip_script`.  Both files include a `NOTE FOR MAINTAINERS`
> comment block explaining what to adjust (scoring guide, topic examples, tone)
> when deploying for a specialist audience such as finance, sports, or local news.

YouTube upload (M1 Phase 5) additionally requires OAuth2 credentials — see `compe_M1/README.md`.

## Topic Configuration (`topics.yaml`)

Each topic entry uses two title fields:

| Field | Purpose |
|-------|---------|
| `title_en` | English topic name used for Gemini search queries and as the internal dictionary key. |
| `title_target_lang` | Display name in the output language (set via `LANGUAGE`). Used for on-screen titles, narration scripts, and image captions. |

```yaml
# Example
- title_en: "Strait of Hormuz blockade"
  title_target_lang: "ホルムズ海峡封鎖"   # shown in output when LANGUAGE=ja
  query_keywords:
    - "Strait of Hormuz blockade 2026"
  importance_score: 9.0
  tags: ["hormuz", "iran", "shipping", "military"]
```

When adding a topic, always set `title_target_lang` to the display text appropriate for your configured `LANGUAGE`.

## Usage

### M1: Generate a Briefing Video

```bash
# On GCE
cd compe_M1

# Run individual phases
uv run main.py --phase 1          # Collect + summarize
uv run main.py --phase 2          # Script + images
uv run main.py --phase 3          # TTS narration
uv run main.py --phase 4          # Compose video
uv run main.py --phase 5          # Register to ContentIndex

# Or run all at once
uv run main.py                    # Phases 1–5
uv run main.py --skip-upload      # Phases 1–4, then ContentIndex only (no YouTube)

# Switch topics
uv run main.py --phase 1 --topic-index 0   # First topic (default)
uv run main.py --phase 1 --topic-index 1   # Second topic
```

See `compe_M1/README.md` for the full pipeline diagram and cost estimates.

### M3: Crawl & Analyze News Sources

```bash
# On GCE
cd compe_M3

# Crawl
uv run main.py crawl                          # Default source (un_news)
uv run main.py crawl --source centcom          # Specific source
uv run main.py crawl --all                     # All sources
uv run main.py crawl --list-sources            # Show registered sources

# Analyze
uv run main.py analyze                         # Batch analyze unanalyzed articles
uv run main.py analyze --limit 3               # Limit to 3 articles

# Compose briefing video
uv run main.py compose --dry-run               # Script only (no video)
uv run main.py compose                         # Full video generation
uv run main.py compose --short-clips           # Short clip mode
uv run main.py compose --short-clips --limit 1 # Single clip

# Scheduler (daemon)
uv run main.py schedule                        # Run until Ctrl+C
uv run main.py schedule --duration 30          # Auto-stop after 30s

# Full pipeline (crawl → analyze → compose)
uv run main.py pipeline                        # One-shot full run
uv run main.py pipeline --dry-run              # Skip video generation

# Debug output
uv run main.py --debug crawl                   # Show DB stats, detailed logs
```

### M4: Live Broadcast

```bash
# On Windows
cd compe_M4
python gemini_live_client.py
```

**Keyboard shortcuts during broadcast:**

| Key | Action |
|-----|--------|
| F5 | Play / Pause toggle |
| F6 | Stop playback |
| F7 | Minimize media window |
| F8 | Restore media window |
| **F9** | **Push-to-talk** (hold to speak to Gemini) |

## Tech Stack

Python, google-genai SDK, Gemini Live API, Gemini 2.5 Flash, Google Grounding, DrissionPage, nodriver, ffmpeg, APScheduler, PyAudio, python-vlc, tkinter, OBS Studio, Terraform, GCE, GCS, YouTube Data API v3

## TODO

- [x] ~~**M3 refactoring**: Consolidate `test_phase*.py` into a proper `main.py` with CLI subcommands.~~
- [x] ~~**Unified config**: `.env` consolidated to project root, no longer duplicated.~~
- [x] ~~**Shared modules**: `compe_M1/agents/` moved to top-level `shared/`, eliminating cross-platform symlink issues.~~
- [x] ~~**Multilingual output**: All source code comments, docstrings, and log messages converted to English. `LANGUAGE` (BCP-47) added to `.env`; `shared/language_utils.py` handles Live API ↔ prompt conversion automatically.~~
- [x] ~~**M3 code English**: All M3 docstrings, inline comments, prompts, and CLI help strings converted to English. `GeminiClient` now resolves `LANGUAGE` on init (`self.language`) and passes it to analyst and composer prompts. Text output fields (`summary`, `importance_reason`, scripts) are generated in the configured language; structured fields (`topics`, `key_entities`) remain in English.~~
- [x] ~~**M3 general-purpose prompts**: `gemini_analyst.py` and `briefing_composer.py` prompts rewritten for general news content creators (YouTubers, independent journalists). Removed OSINT/military-specific framing. Each prompt file contains a `NOTE FOR MAINTAINERS` comment with guidance for customising to a specialist vertical.~~
- [ ] **M3 prompt tuning**: Default prompts in `gemini_analyst.py` (`_ANALYSIS_PROMPT_TEMPLATE`) and `briefing_composer.py` (`generate_m3_script`, `_generate_short_clip_script`) are intentionally general-purpose. If deploying for a specific content vertical (finance, sports, local politics, etc.), edit the scoring guide, topic examples, and tone instructions in those files.
- [ ] **GCE→GCS sync script**: The `gcp/` directory references sync but no dedicated push script is committed. Document or add the cron-based `gcloud storage rsync` commands.
- [ ] **CI/CD**: No automated tests or linting configured yet.
- [ ] **content_index.py docs**: Document the shared content registry schema used between M1/M3 (producers) and M4 (consumer).

## License

*TBD*
