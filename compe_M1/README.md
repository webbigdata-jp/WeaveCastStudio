# WeaveCastStudio M1

Pipeline for collecting official government statements, summarising them, generating images and narration, composing videos, and registering everything to ContentIndex.
Processes all topics in a single run to produce a **full briefing video** and **per-topic short clips**.

---

## System Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          M1 Pipeline Overview                            │
│                                                                          │
│  [STEP 1] Topic definitions (config/topics.yaml)                        │
│      │    Load search_countries + topics[]                               │
│      ▼                                                                   │
│  [STEP 2] Collect official statements                                   │
│      │  ├─ Gemini 2.5 Flash + Google Search Tool → URLs + summary text  │
│      │  ├─ DrissionPage (Chrome) → fetch page body + screenshots        │
│      │  ├─ Screenshots saved → output/.../data/screenshots/             │
│      │  └─ Gemini 2.5 Flash → re-extract key points from page body      │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 3] Structured summary generation (Gemini 2.5 Flash — JSON out)  │
│      │  └─ Convert per-country statements to unified JSON schema        │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 4] Narration script generation (Gemini 2.5 Flash)               │
│      │  ├─ Full briefing script (script.txt)                            │
│      │  └─ Per-topic clip scripts (clip_scripts.json)                   │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 5] Image generation (Gemini Image Generation)                    │
│      │  ├─ Title slide                                                   │
│      │  ├─ News lineup image (today's topics)                           │
│      │  ├─ Content slides (one per topic)                               │
│      │  └─ Clip images (one per topic)                                  │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 6] Narration audio generation (Gemini TTS)                      │
│      │  ├─ Full briefing audio                                           │
│      │  ├─ Per-topic clip audio                                          │
│      │  └─ Exponential backoff retry on failure (max 4 attempts)        │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 7] Video composition (ffmpeg)                                    │
│      │  ├─ Full briefing video (1920×1080 / H.264 / AAC)               │
│      │  └─ Per-topic short clip videos                                  │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 8] ContentIndex registration                                     │
│           ├─ Register full briefing video                               │
│           ├─ Register each clip video individually                      │
│           ├─ Register news lineup image                                 │
│           └─ Update manifest.json                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
config/topics.yaml
    │
    ▼
[Phase 1] shared/source_collector.py ──→ output/.../data/raw_statements.json
              │                                {topic_en: {text, urls}}
              │                          output/.../data/screenshots/*.png
              ▼
          shared/summarizer.py ────────→ output/.../data/briefing_data.json
                                               {briefing_sections[], analysis{}}
    │
    ▼
[Phase 2] shared/script_writer.py ─────→ output/.../data/script.txt        (full script)
              │                          output/.../data/clip_scripts.json  (clip scripts)
              ▼
          shared/image_generator.py ───→ output/.../images/slide_title.png
                                         output/.../images/news_lineup.png
                                         output/.../images/slide_*.png
                                         output/.../clips/clip_*/image.png
                                         output/.../data/image_manifest.json
    │
    ▼
[Phase 3] shared/narrator.py ──────────→ output/.../audio/segment_*.wav    (full audio)
                                         output/.../clips/clip_*/          (clip audio)
                                         output/.../data/audio_manifest.json
    │
    ▼
[Phase 4] shared/video_composer.py ────→ output/.../briefing.mp4           (full video)
                                         output/.../video/clips/clip_*.mp4 (clip videos)
                                         output/.../manifest.json
    │
    ▼
[Phase 5] content_index.py ────────────→ content_index.json (project root)
              │                          └─ M4 reads this file for playback
              ▼
          (optional) YouTube upload
```

> **Output directory**: A timestamped directory `output/briefing_YYYYMMDD_HHMMSS/` is
> created for each run. Use `--output-dir` to reuse an existing directory.

---

## Setup

### 1. Install dependencies

Run `uv sync` from the project root. No M1-specific extras are required.

```bash
cd WeaveCastStudio
uv sync
```

### 2. Install Chrome / Chromium (required by DrissionPage)

```bash
# Ubuntu/Debian (GCE)
sudo apt install chromium-browser

# Windows
# Chrome or Edge already installed is sufficient.
```

### 3. Install ffmpeg

```bash
# Ubuntu/Debian (GCE)
sudo apt install ffmpeg

# Windows
# Download from https://www.gyan.dev/ffmpeg/builds/ and add to PATH.
```

### 4. Configure environment variables

Copy `.env.sample` to `.env` at the project root and fill in the values (shared by all modules).

```bash
cp .env.sample .env
# Edit .env: set GOOGLE_API_KEY and LANGUAGE
```

```ini
GOOGLE_API_KEY=your_api_key_here
LANGUAGE=ja   # BCP-47 language code (e.g. ja / en / ko / zh)
```

`LANGUAGE` controls the output language across all modules.
`shared/language_utils.py` automatically converts the BCP-47 code (e.g. `ja`) used by
the Live API into a natural-language name (e.g. `Japanese`) injected into Gemini prompts,
so narration scripts, image captions, and summaries are all generated in the chosen language.
Full list of supported codes: https://ai.google.dev/gemini-api/docs/live-api/capabilities#supported-languages

### 5. YouTube OAuth2 setup (only if YouTube upload is needed)

1. Enable **YouTube Data API v3** in [Google Cloud Console](https://console.cloud.google.com/).
2. Go to Credentials → OAuth 2.0 Client IDs → create a **Desktop application** credential.
3. Save the downloaded JSON as `compe_M1/config/youtube_client_secrets.json`.
4. On first run, a browser window opens for the OAuth flow → `config/youtube_token.json` is created automatically.
5. Subsequent runs use automatic token refresh — no re-authentication needed.

---

## Usage

### Run phases individually (recommended for debugging)

```bash
cd compe_M1

# Phase 1: Information collection + structured summary
uv run main.py --phase 1

# Phase 2: Script generation + image generation
uv run main.py --phase 2

# Phase 3: TTS audio generation
uv run main.py --phase 3

# Phase 4: Video composition
uv run main.py --phase 4

# Phase 5: ContentIndex registration
uv run main.py --phase 5
```

### Run all phases at once

```bash
uv run main.py                 # Phases 1–5
uv run main.py --skip-upload   # Phases 1–5 (ContentIndex only, no YouTube upload)
```

### Switch topics

Edit `config/topics.yaml` to define topics.
By default all topics are processed together. Use `--topic-index` to process a single topic.

```bash
uv run main.py --phase 1 --topic-index 0   # First topic only
uv run main.py --phase 1 --topic-index 1   # Second topic only
```

### Reuse an existing output directory

Useful when re-running Phase 2 onwards without repeating Phase 1.

```bash
uv run main.py --phase 2 --output-dir output/briefing_20260310_172523
```

---

## topics.yaml Reference

Topics are defined in `config/topics.yaml`. Example:

```yaml
# search_countries: search hints — other countries may also appear in results
search_countries:
  - "United States"
  - "Iran"
  - "Israel"
  - "Russia"
  - "China"

topics:
  - title_en: "Strait of Hormuz blockade"    # used for Gemini search queries and as internal key
    title_target_lang: "ホルムズ海峡封鎖"      # display name in the output language (set via LANGUAGE)
    query_keywords:
      - "Strait of Hormuz blockade 2026"
      - "Iran Hormuz closure shipping"
    importance_score: 8.5       # 0.0–10.0 (affects ContentIndex priority)
    tags: ["hormuz", "iran", "shipping", "military"]   # used for M4 search/filter
```

| Field | Purpose |
|-------|---------|
| `title_en` | Used for Gemini search query construction and as the `raw_statements` dict key. Always in English. |
| `title_target_lang` | On-screen display name, narration script, and image captions. Write in the language matching your `LANGUAGE` setting. |

Each entry in the `topics` list produces one full briefing section and one short clip video.

---

## Models & Tools

| STEP | Tool / Model | Purpose |
|------|-------------|---------|
| 2 | Gemini 2.5 Flash + Google Search Tool | Collect statements and URLs |
| 2 | **DrissionPage** (Chrome automation) | Open URLs in browser, retrieve page body and screenshots |
| 2 | Gemini 2.5 Flash | Re-extract key points from page body |
| 3 | Gemini 2.5 Flash | Structured JSON summary |
| 4 | Gemini 2.5 Flash | Narration script generation (full + clips) |
| 5 | Gemini Image Generation | Title, lineup, content, and clip image generation |
| 6 | Gemini TTS | Speech synthesis (full + clips) |
| 7 | ffmpeg | Image + audio → MP4 video composition |
| 8 | content_index.py | Register content for M4 playback |

---

## Error Handling

| STEP | Failure scenario | Behaviour |
|------|-----------------|-----------|
| STEP 2 (Gemini Search) | No search results | Skip that topic |
| STEP 2 (DrissionPage) | Page fetch failure | Continue with Gemini search results only |
| STEP 3 | JSON parse failure | Retry up to 3 times → continue with fallback structure |
| STEP 5 | Image generation failure | Substitute solid-colour placeholder image |
| STEP 6 | TTS failure | Exponential backoff, up to 4 retries (5s → 10s → 20s → 40s) |
| STEP 6 | All retries exhausted | Insert 3-second silent WAV placeholder to keep video composition running |
| STEP 7 | ffmpeg failure | Log error and exit — manual inspection required |

---

## Directory Structure

```
WeaveCastStudio/
├── .env                           # GOOGLE_API_KEY + LANGUAGE (shared by all modules)
├── .env.sample                    # Template for .env
├── content_index.py               # Shared content registry: M1/M3 → M4
├── content_index.json             # Index file managed by content_index.py (auto-generated)
├── shared/                        # Common pipeline modules for M1/M3
│   ├── language_utils.py          #   BCP-47 language config loader (reads LANGUAGE from .env)
│   ├── source_collector.py        #   STEP 2: Information collection (Gemini + DrissionPage)
│   ├── summarizer.py              #   STEP 3: Structured JSON summary
│   ├── script_writer.py           #   STEP 4: Narration script generation
│   ├── image_generator.py         #   STEP 5: Image generation
│   ├── narrator.py                #   STEP 6: TTS audio synthesis (with retry)
│   └── video_composer.py          #   STEP 7: ffmpeg video composition
│
├── compe_M1/                      # ← This module
│   ├── main.py                    # Pipeline entry point
│   ├── README.md                  # This file (English)
│   ├── README_ja.md               # Japanese version
│   ├── config/
│   │   ├── topics.yaml            # Topic definitions (edit as needed)
│   │   ├── youtube_client_secrets.json  # YouTube OAuth2 (place manually)
│   │   └── youtube_token.json     # OAuth2 token (auto-generated on first auth)
│   ├── uploader/
│   │   └── youtube_uploader.py    # YouTube upload
│   └── output/
│       └── briefing_YYYYMMDD_HHMMSS/   # Created per run
│           ├── data/
│           │   ├── raw_statements.json  # Phase 1 output
│           │   ├── briefing_data.json   # Phase 1 output
│           │   ├── script.txt           # Phase 2 output (full script)
│           │   ├── clip_scripts.json    # Phase 2 output (clip scripts)
│           │   ├── image_manifest.json  # Phase 2 output
│           │   ├── audio_manifest.json  # Phase 3 output
│           │   └── screenshots/         # Phase 1 screenshots
│           ├── images/                  # Phase 2 generated images
│           ├── audio/                   # Phase 3 generated audio
│           ├── video/
│           │   └── clips/               # Phase 4 clip videos
│           ├── clips/
│           │   └── clip_001/            # Per-clip working directory
│           │       ├── image.png
│           │       ├── script.txt
│           │       └── *.wav
│           ├── briefing.mp4             # Phase 4 full video
│           └── manifest.json            # Artifact paths for all phases
```

---

## Running on Windows (alongside M4 live broadcast)

M1 is primarily designed for scheduled execution on GCE, but it runs on Windows as well.
Running M1 in parallel with an M4 live broadcast lets you push fresh briefings and clips
into ContentIndex mid-stream.

### Requirements

- **Chrome or Edge** installed (used by DrissionPage)
- **ffmpeg** on PATH
- **16 GB RAM or more recommended** when running OBS + M4 + M1 simultaneously
  (Phase 1 browser automation is CPU- and memory-intensive)

### Concurrent access to ContentIndex

`content_index.py` uses `threading.Lock` and atomic file rename for writes, so M4 reading
while M1 writes will not corrupt the file. However, if M1 (`add_entry`) and M4 (`mark_used`)
write at almost exactly the same time, one change may be lost since there is no cross-process
lock. In practice this race condition is extremely rare, but worth noting.

### Operational patterns

- **Run Phase 1 on GCE only** → pull data via GCS → **run Phases 2–5 on Windows**
  (offload browser automation to GCE and reduce Windows resource usage)
- **Run all phases on Windows**
  (no GCS sync needed; monitor resource consumption)

---

## Cost Estimate (per briefing)

| STEP | Cost |
|------|------|
| STEP 2 Gemini Search × ~10 countries | ~$0.001 |
| STEP 3 Structured summary | ~$0.001 |
| STEP 4 Script generation | ~$0.001 |
| STEP 5 Image generation × 5–8 images | ~$0.20–$0.31 |
| STEP 6 TTS × ~15 paragraphs | Expected within free tier |
| STEP 7 ffmpeg | Free (local processing) |
| STEP 8 ContentIndex registration | Free (local processing) |
| **Total** | **~$0.21–$0.32** |

Generating 5 briefings per day costs approximately **~$1.05–$1.60 / day**.
