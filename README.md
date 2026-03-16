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
├── content_index.py           # Shared module: content registry for M1/M3 → M4
├── pull_from_gcs.ps1          # Windows: pull GCS data to local
├── pyproject.toml             # uv project definition
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
│   │   ├── topics.yaml        # Topic definitions
│   │   └── .env               # GOOGLE_API_KEY (git-ignored)
│   ├── agents/                # Pipeline stages
│   │   ├── source_collector.py    # Phase 1: Gemini Search + nodriver
│   │   ├── summarizer.py         # Phase 1: Structured JSON summary
│   │   ├── script_writer.py      # Phase 2: Narration script
│   │   ├── image_generator.py    # Phase 2: Infographic images
│   │   ├── narrator.py           # Phase 3: TTS audio
│   │   └── video_composer.py     # Phase 4: ffmpeg video composition
│   └── uploader/
│       └── youtube_uploader.py   # Phase 5: YouTube upload
│
├── compe_M3/                  # Module M3: Fact Checker / Crawler
│   ├── main.py                # (placeholder — use test_phase*.py)
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
│   ├── scheduler/
│   │   └── crawl_scheduler.py
│   ├── test_phase1.py         # Crawl single source (also used by cron)
│   ├── test_phase2.py         # Analyze articles
│   ├── test_phase3.py         # Compose briefings
│   └── test_phase4.py         # Full pipeline (--all flag for cron)
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

# Set API key
cat > compe_M1/config/.env << 'EOF'
GOOGLE_API_KEY=your_api_key_here
EOF
cp compe_M1/config/.env compe_M3/config/.env

# Verify M3
cd compe_M3 && uv run test_phase1.py

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
uv run main.py --phase 5          # Upload to YouTube

# Or run all at once
uv run main.py                    # Phases 1-5
uv run main.py --skip-upload      # Phases 1-4 (no YouTube)

# Switch topics
uv run main.py --phase 1 --topic-index 0   # Iran Conflict (default)
uv run main.py --phase 1 --topic-index 1   # Ukraine Peace
```

See `compe_M1/README.md` for the full pipeline diagram and cost estimates.

### M3: Crawl & Analyze News Sources

```bash
# On GCE
cd compe_M3

uv run test_phase1.py              # Crawl a single source
uv run test_phase2.py              # Analyze stored articles
uv run test_phase3.py              # Compose briefings
uv run test_phase4.py --all        # Full pipeline (used by cron)
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

## Environment Variables

| Variable | Where | Description |
|----------|-------|-------------|
| `GOOGLE_API_KEY` | `compe_M1/config/.env` (shared with M3, M4) | Gemini API key |

M4 loads the `.env` from `compe_M1/config/.env` automatically.  
YouTube upload (M1 Phase 5) additionally requires OAuth2 credentials — see `compe_M1/README.md`.

## Tech Stack

Python, google-genai SDK, Gemini Live API, Gemini 2.5 Flash, Google Grounding, DrissionPage, nodriver, ffmpeg, APScheduler, PyAudio, python-vlc, tkinter, OBS Studio, Terraform, GCE, GCS, YouTube Data API v3

## TODO

- [ ] **M3 refactoring**: `compe_M3/main.py` is currently a placeholder; the actual pipeline is split across `test_phase*.py` files. Consolidate into a proper `main.py` with CLI args.
- [ ] **M3 documentation**: Add a dedicated `compe_M3/README.md` with architecture, data flow, and sources.yaml schema docs.
- [ ] **Delete dead files**: Remove the development artifacts listed above (`p.py`, `check_prompts.py`, `test_grounding.py`, `compe_M4/test_*.py`).
- [ ] **GCE→GCS sync script**: The `gcp/` directory references sync but no dedicated push script is committed. Document or add the cron-based `gcloud storage rsync` commands.
- [ ] **Root `main.py`**: Currently a placeholder (`print("Hello")`). Either remove or repurpose as a top-level orchestrator.
- [ ] **Unified config**: `.env` is duplicated between M1 and M3. Consider a single project-root `.env`.
- [ ] **CI/CD**: No automated tests or linting configured yet.
- [ ] **content_index.py docs**: Document the shared content registry schema used between M1/M3 (producers) and M4 (consumer).

## License

*TBD*
