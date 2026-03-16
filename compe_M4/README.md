# WeaveCastStudio M4: Live Broadcast Client

**Real-time broadcast assistant powered by Gemini Live API**

During an OBS live stream, the journalist speaks voice commands and Gemini AI automatically plays video clips, shows images, and searches articles. A Breaking News ticker runs at the bottom of the screen, automatically highlighting breaking stories as they arrive.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows PC (Broadcast Station)                                  │
│                                                                  │
│  ┌──────────────────────┐    ┌───────────────────────────────┐  │
│  │  gemini_live_client   │    │  OBS Studio                   │  │
│  │  ┌────────────────┐   │    │  ┌─────────────────────────┐  │  │
│  │  │ PTT Mic Input   │   │    │  │  Window Capture         │  │  │
│  │  │ (Hold F9)       │   │    │  │  ← MediaWindow          │  │  │
│  │  └───────┬────────┘   │    │  ├─────────────────────────┤  │  │
│  │          ▼            │    │  │  Browser Source          │  │  │
│  │  ┌────────────────┐   │    │  │  ← ticker.html          │  │  │
│  │  │ Gemini Live API │   │    │  │    (localhost:8765)      │  │  │
│  │  │  Voice + FC     │   │    │  └─────────────────────────┘  │  │
│  │  └───────┬────────┘   │    └───────────────────────────────┘  │
│  │          ▼            │                                       │
│  │  ┌────────────────┐   │    ┌───────────────────────────────┐  │
│  │  │ ToolExecutor    │   │    │  breaking_news_server         │  │
│  │  │ - play_video    │───┼───▶│  :8765/overlay → ticker.html  │  │
│  │  │ - show_image    │   │    │  :8765/events  → SSE          │  │
│  │  │ - search_articles│  │    │  :8765/status  → JSON          │  │
│  │  └───────┬────────┘   │    └──────────┬────────────────────┘  │
│  │          ▼            │               │                       │
│  │  ┌────────────────┐   │    ┌──────────▼────────────────────┐  │
│  │  │ MediaWindow     │   │    │  ArticleStore (SQLite)        │  │
│  │  │ tkinter + VLC   │   │    │  ← compe_M3/data/articles.db  │  │
│  │  └────────────────┘   │    └──────────▲────────────────────┘  │
│  │                        │               │                       │
│  │  ┌────────────────┐   │    ┌──────────┴────────────────────┐  │
│  │  │ TrumpMonitor    │───┼───▶│  monitor/trump_monitor.py     │  │
│  │  │ (background     │   │    │  poll trumpstruth.org (5min)  │  │
│  │  │  thread)        │   │    │  → Gemini judge               │  │
│  │  └────────────────┘   │    │  → mark_breaking() if score   │  │
│  │                        │    │    >= 7.5                      │  │
│  └──────────────────────┘    └───────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **M1/M3 (GCE)** generate video clips and the article database, then upload to GCS
2. **Windows PC** pulls the data via `pull_from_gcs.ps1`
3. **gemini_live_client.py** loads ContentIndex and ArticleStore at startup
4. Journalist presses **F9 (PTT)** to give voice commands → Gemini responds via Function Calling
5. **MediaWindow** displays video/images, **ticker server** streams breaking news
6. **TrumpMonitor** (background thread) polls trumpstruth.org every 5 minutes, judges posts via Gemini, and injects breaking news into ArticleStore automatically

## File Structure

```
compe_M4/
├── gemini_live_client.py      # Main entry point
├── media_window.py            # tkinter + VLC media window
├── breaking_news_server.py    # HTTP + SSE ticker server
├── media_assets.json          # Image asset definitions
├── demo_setup.py              # Demo recording data setup script
├── test_trump_monitor.py      # TrumpMonitor operation test script
├── trump_monitor_state.json   # TrumpMonitor last-seen status ID (auto-generated)
├── monitor/                   # Background news monitor modules
│   ├── __init__.py
│   └── trump_monitor.py       # Truth Social polling + Gemini judge
├── overlay/
│   └── ticker.html            # OBS browser source (ticker display)
├── assets/                    # Image asset files
│   ├── iranstrikemap.png
│   ├── trump_truth_kharg.png
│   ├── kharg_island_map.png
│   └── ...
├── OBS_SETUP.md               # OBS configuration guide
└── DEMO_SCRIPT.md             # Demo recording script
```

## Prerequisites

### Software

- **Python 3.11+** (managed via [uv](https://docs.astral.sh/uv/))
- **OBS Studio** (for live streaming)
- **VLC** (video playback engine; required by python-vlc)

### Data

- **M1/M3 output data** pulled via `pull_from_gcs.ps1`:
  - `compe_M3/data/articles.db` — Article database (ArticleStore)
  - Video clips registered in ContentIndex
- **Gemini API key** set in `.env` at the project root

### Hardware

- **Microphone** — for PTT voice input
- **Speakers / Headphones** — for Gemini voice responses

## Setup

### 1. Install Dependencies

Run from the project root:

```bash
uv sync
```

Key dependencies: `google-genai`, `pyaudio`, `keyboard`, `python-vlc`, `Pillow`, `python-dotenv`, `aiohttp`

### 2. Set Environment Variables

Create a `.env` file in the project root (`WeaveCastStudio/`):

```bash
GOOGLE_API_KEY=your_api_key_here
```

### 3. Pull Data

Pull M1/M3 output data from GCS:

```powershell
# Pull all module data
.\pull_from_gcs.ps1

# Pull M3 only
.\pull_from_gcs.ps1 -m3only
```

### 4. Prepare Image Assets

Image assets defined in `media_assets.json` are automatically downloaded from `source_url` on first launch. If `source_url` is empty, place the files manually in the `assets/` directory.

### 5. Configure OBS

See [OBS_SETUP.md](OBS_SETUP.md) for detailed instructions.

## Running

```bash
cd compe_M4
python gemini_live_client.py
```

On startup, the following happens automatically:

1. Image asset download check
2. Load today's article titles from ArticleStore
3. Load playable content list from ContentIndex
4. Launch MediaWindow (tkinter) in a separate thread
5. Start Breaking News ticker server at `http://localhost:8765`
6. **Start TrumpMonitor** in a background thread (polls trumpstruth.org every 5 minutes)
7. Connect to Gemini Live API and enter PTT standby

After startup, the terminal displays the list of playable content and image assets.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **F9** | **Push-to-talk** (sends mic audio to Gemini while held) |
| F5 | Play / Pause toggle |
| F6 | Stop (move media window off-screen) |
| F7 | Move media window off-screen |
| F8 | Restore media window on-screen |

## Gemini Tools (Function Calling)

Gemini automatically invokes the following tools in response to the journalist's voice commands.

| Tool | Description | Parameters |
|------|-------------|------------|
| `play_video` | Play a video from ContentIndex | `content_id` |
| `stop_video` | Stop playback and move window off-screen | — |
| `pause_video` | Pause playback | — |
| `resume_video` | Resume playback | — |
| `show_image` | Display a still image | `image_id` or `file_path` |
| `minimize_window` | Move window off-screen | — |
| `restore_window` | Restore window on-screen | — |
| `search_articles` | Search ArticleStore by keyword | `query`, `limit` |
| `list_videos` | Return the list of playable content | — |

### How Content Selection Works

At startup, the full ContentIndex and today's article titles (up to 30) from ArticleStore are passed to Gemini's system instruction. When the journalist says something like "show me the video about …", Gemini autonomously selects the best `content_id` based on titles, topic tags, and importance scores, then calls `play_video`.

## Component Details

### gemini_live_client.py

The main client that manages the voice session with Gemini Live API.

**Key features:**

- **PTT (Push-to-Talk)**: Sends mic audio as 16 kHz PCM to Gemini while F9 is held. Sends `audio_stream_end` on release to signal end of speech.
- **Voice response playback**: Outputs 24 kHz PCM audio from Gemini to the speaker.
- **Function Calling**: Executes Gemini's tool calls via `ToolExecutor` and sends results back.
- **Automatic session reconnection**: Uses Session Resumption handles to reconnect while preserving conversation context (up to 5 attempts with exponential backoff).
- **GoAway handling**: Receives server disconnect warnings and reconnects automatically.
- **Transcription display**: Shows input (journalist) and output (Gemini) transcriptions in the terminal.

**Model**: `gemini-2.5-flash-native-audio-preview-12-2025`

### media_window.py

A media player that displays VLC video and Pillow still images in a tkinter window.

**Key features:**

- **Video playback**: Embeds VLC into a tkinter Frame at 1920×1080. Supports mid-playback video switching (`swap_video`).
- **Image display**: Loads images via Pillow, fits them to the window size while maintaining aspect ratio, and draws on Canvas.
- **Thread separation**: The tkinter main loop runs in a separate thread, coexisting with the asyncio event loop.
- **OBS compatibility**: Hides the window by moving it off-screen (`-1920, 0`) instead of minimizing, so OBS Window Capture always recognizes the window.

**Window title**: `WeaveCast Media` (used to identify the capture target in OBS)

### breaking_news_server.py

An aiohttp-based HTTP + SSE server that streams the news ticker to OBS browser sources.

**Endpoints:**

| Path | Method | Description |
|------|--------|-------------|
| `/overlay` | GET | Returns the ticker HTML (`ticker.html`) |
| `/events` | GET | SSE stream (ticker updates and breaking news events) |
| `/breaking` | POST | Manual breaking news injection (JSON: `{"headline": "...", "source": "..."}`) |
| `/status` | GET | Returns current ticker state as JSON (for debugging) |

**Behavior:**

- Polls ArticleStore every 30 seconds and displays articles with `importance_score >= 3.0` on the ticker
- When an `is_breaking: true` article is detected, sends a `breaking` event via SSE
- Sends heartbeats every 30 seconds to keep SSE connections alive

### ticker.html

The ticker UI displayed as an OBS browser source.

**Display elements:**

- **Ticker bar** (bottom 72px): News headlines scroll left at 120 px/sec
- **Breaking items**: Red background + yellow text + `BREAKING` tag, mixed in with regular news
- **Breaking banner** (top of screen): Shown for 8 seconds when a new breaking story is first detected
- **Breaking flash**: A subtle red full-screen flash on the first breaking news event

When a `ticker` or `breaking` SSE event is received, headlines are rebuilt and scrolling restarts. On disconnect, the client automatically reconnects after 3 seconds.

### media_assets.json

Definition file for image assets. `ImageAssetManager` reads this file to manage and download assets.

**Schema:**

```json
{
  "image_assets": [
    {
      "id": "asset_id",
      "title": "Display name",
      "description": "Description text",
      "local_path": "assets/filename.png",
      "source_url": "https://...",
      "topic_tags": ["tag1", "tag2"]
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique asset identifier. Specified as `image_id` in Gemini's `show_image` tool |
| `title` | Yes | Display name. Passed to Gemini's system instruction |
| `description` | No | Description text |
| `local_path` | Yes | Relative path from `compe_M4/` |
| `source_url` | No | URL for automatic download on first launch (if empty, place manually) |
| `topic_tags` | No | Topic tags. Referenced by Gemini when selecting assets |

### monitor/trump_monitor.py

Background thread that polls [trumpstruth.org](https://trumpstruth.org) every 5 minutes and automatically injects new Trump posts into ArticleStore.

**Key features:**

- **Scraping**: Fetches the trumpstruth.org top page with `requests` + `BeautifulSoup`. ReTruths with no body text are automatically skipped.
- **Deduplication**: Saves the highest `status_id` seen to `trump_monitor_state.json` and only processes posts with a newer ID on each poll.
- **Gemini judgment**: Sends each new post to `gemini-2.5-flash` to get `importance_score` (0–10), `is_breaking` flag, a one-sentence `summary` (in the configured language), and English `topics`.
- **Filtering**: Posts with `importance_score < 3.0` are discarded. Posts with `score >= 7.5` are saved with `mark_breaking(True)` and appear as red BREAKING items on the ticker.
- **Language-aware**: Summary language follows the project-wide `LANGUAGE` setting in `.env`.
- **Extensible**: Designed as a `threading.Thread` subclass with a consistent interface so additional sources (Reddit, etc.) can be added as sibling modules under `monitor/`.

**State file**: `compe_M4/trump_monitor_state.json` (auto-generated, safe to delete to re-process recent posts)

**Breaking threshold constants** (in `trump_monitor.py`):

| Constant | Default | Description |
|----------|---------|-------------|
| `_DEFAULT_INTERVAL` | `300` | Poll interval (seconds) |
| `_BREAKING_THRESHOLD` | `7.5` | Minimum importance_score for breaking flag |
| `_JUDGE_MODEL` | `gemini-2.5-flash` | Gemini model used for judgment |

### demo_setup.py

Data preparation script for demo recording. See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for details.

**Commands:**

```bash
# Seed demo news articles into the DB
python demo_setup.py --seed-db

# Start ticker server + auto-inject breaking news after 30 seconds
python demo_setup.py --run --breaking-delay 30

# Full setup (seed DB + start server + breaking timer)
python demo_setup.py --seed-db --run --breaking-delay 30

# Inject breaking news immediately from another terminal
python demo_setup.py --trigger-breaking

# Clear breaking news and reset to initial demo state
python demo_setup.py --clear-breaking
```

## Configuration Reference

### gemini_live_client.py

| Constant | Default | Description |
|----------|---------|-------------|
| `MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini model |
| `SEND_RATE` | `16000` | Mic input sample rate (Hz) |
| `RECEIVE_RATE` | `24000` | Gemini output sample rate (Hz) |
| `CHUNK_SIZE` | `1024` | Audio chunk size |
| `PTT_KEY` | `f9` | PTT key |

### monitor/trump_monitor.py

| Constant | Default | Description |
|----------|---------|-------------|
| `_DEFAULT_INTERVAL` | `300` | Poll interval (seconds) |
| `_BREAKING_THRESHOLD` | `7.5` | Minimum importance_score to mark as breaking |
| `_JUDGE_MODEL` | `gemini-2.5-flash` | Gemini model for importance judgment |
| `_REQUEST_TIMEOUT` | `20` | HTTP request timeout (seconds) |

### breaking_news_server.py

| Constant | Default | Description |
|----------|---------|-------------|
| `DEFAULT_PORT` | `8765` | HTTP server port |
| `POLL_INTERVAL_SEC` | `30` | ArticleStore polling interval (seconds) |
| `TICKER_HEADLINE_MAX` | `20` | Max headlines shown on the ticker |
| `MIN_IMPORTANCE` | `3.0` | Minimum importance_score to appear on the ticker |

### media_window.py

| Constant | Default | Description |
|----------|---------|-------------|
| `WINDOW_WIDTH` | `1920` | Window width (px) |
| `WINDOW_HEIGHT` | `1080` | Window height (px) |
| `WINDOW_TITLE` | `WeaveCast Media` | Window title |

### ticker.html

| Constant | Default | Description |
|----------|---------|-------------|
| `SCROLL_SPEED` | `120` | Scroll speed (px/sec) |
| `BREAKING_BANNER_DURATION` | `8000` | Breaking banner display duration (ms) |
| `RECONNECT_DELAY` | `3000` | SSE reconnection delay (ms) |

## Troubleshooting

### Cannot connect to Gemini

- Verify that `GOOGLE_API_KEY` is correctly set in `.env`
- Check your network connection
- Review error messages in the terminal. Session reconnection is attempted up to 5 times automatically

### Microphone not recognized

- Verify that PyAudio can detect the default input device
- Check that the microphone is enabled in Windows Sound Settings
- Ensure no other application is exclusively using the microphone

### Video does not play

- Verify that VLC is installed (python-vlc requires the VLC runtime)
- Check that videos are registered in ContentIndex (the list is displayed in the terminal at startup)
- Verify that data has been correctly pulled via `pull_from_gcs.ps1`

### Ticker not showing

- Open `http://localhost:8765/overlay` in a browser to verify display
- Check that `http://localhost:8765/status` returns JSON
- See [OBS_SETUP.md](OBS_SETUP.md) for OBS browser source configuration

### TrumpMonitor not working

- Run `python test_trump_monitor.py` from `compe_M4/` to verify scraping and Gemini judgment independently
- Check terminal logs for `[TrumpMonitor]` lines after launching `gemini_live_client.py`
- If `trump_monitor_state.json` has a very high `last_status_id`, delete the file to re-process recent posts
- Verify network access to `https://trumpstruth.org`

### Permission error with the keyboard module (Linux)

The `keyboard` library requires root privileges on Linux. Run with `sudo` or configure `udev` rules. No admin privileges are required on Windows.
