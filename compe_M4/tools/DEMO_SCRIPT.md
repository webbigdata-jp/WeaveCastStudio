# WeaveCastStudio Demo Video Script

> **Constraints**: Under 4 minutes / Demo ~2:30 + Pitch ~1:20
> **Language**: Japanese (ticker and voice)

---

## Pre-recording Checklist

```bash
# 1. Seed demo news into the DB (regular news only — no breaking yet)
python demo_setup.py --seed-db

# 2. Verify M1 videos are registered in content_index

# 3. Verify image assets are in place
#    images/trump_truth_kharg.png     ← Truth Social screenshot
#    images/iranstrikemap.png          ← OSINT Middle East map
#    images/kharg_island_map.png       ← Kharg Island map

# 4. Verify OBS browser source is configured (http://localhost:8765/overlay)

# 5. Launch gemini_live_client.py (ticker server starts automatically)
#    Or run demo_setup.py --run --breaking-delay 0 for server only

# 6. Start OBS recording
```

---

## PART 1: Demo (~2:30)

### Scene 1: Opening + Normal Broadcast (0:00 – 0:25)

**Screen layout**:
- MediaWindow: Iran Strike Map (Middle East map) displayed
- Ticker: 7 regular news items scrolling

**Narration / Subtitles**:

> WeaveCastStudio is an AI-powered tool that supports journalists during live reporting in real time.
> We are currently live-reporting on the Iran conflict.
> The screen shows a Middle East situation map, with the latest news scrolling in the ticker below.

**Actions**:
- Display the map before recording with `show_image iranstrikemap`
- Ticker scrolls automatically

---

### Scene 2: Voice Command to Play Video (0:25 – 0:55)

**Actions**:
- Press F9 (PTT) and give voice command

**Journalist (voice)**:
> "Show me the video about the Strait of Hormuz blockade"

**Gemini response** (voice):
> "Playing the Strait of Hormuz blockade report"

**Screen**: M1-generated video clip "Strait of Hormuz Blockade" plays (~10 seconds shown)

**Journalist (voice), during playback**:
> "Next, show me the report on Iran's new leader"

**Gemini response**: Video switches → M1-generated "Iran's New Supreme Leader" clip

---

### Scene 3: Breaking News Injection (0:55 – 1:25)

**Actions** (separate terminal or timer):
```bash
python demo_setup.py --trigger-breaking
```

**Screen changes**:
1. Red-background item appears in ticker scroll:
   "**BREAKING** President Trump announces US military has struck Iranian military targets on Kharg Island"
2. Top banner fades in (disappears after 8 seconds)
3. Shortly after, a second breaking item appears:
   "**BREAKING** Oil price WTI surpasses $100 — surging on Kharg Island strike reports"

**Narration / Subtitles**:
> When the news collection pipeline detects breaking news, it automatically appears in the ticker highlighted in red.

---

### Scene 4: Journalist Dives into Breaking News (1:25 – 1:55)

**Journalist (voice)**:
> "Tell me more about that breaking news"

**Gemini response** (voice):
> "President Trump posted a statement on Truth Social.
>  US Central Command has struck all military targets on Kharg Island.
>  He claims oil facilities were not targeted,
>  but markets are concerned about Iranian retaliation, pushing oil above $100.
>  Displaying Trump's post now."

**Screen**: `show_image trump_truth_kharg` → Truth Social post screenshot displayed

---

### Scene 5: Map Verification (1:55 – 2:20)

**Journalist (voice)**:
> "Show me where Kharg Island is on the map"

**Gemini response**:
> "Displaying the Kharg Island map.
>  It's an island off Iran's coast in the Persian Gulf,
>  home to Iran's largest oil export terminal."

**Screen**: `show_image kharg_island_map` → Wikipedia map displayed

**Journalist (voice)**:
> "Thanks. Switch back to the overview map"

**Gemini response**: `show_image iranstrikemap` → Middle East map returns

---

### Scene 6: Demo Wrap-up (2:20 – 2:30)

**Screen**: Middle East map + ticker (including breaking) scrolling

**Narration**:
> As you can see, WeaveCastStudio seamlessly supports the journalist's workflow
> from news collection to live broadcast.

---

## PART 2: Pitch (~2:30 – 3:50)

**Screen**: Presentation slides or screen recording with text overlay

### The Problem (2:30 – 3:00)

> During live reporting, journalists are overwhelmed with multitasking:
> checking breaking news, switching footage, displaying maps and sources.
>
> Traditionally, this required a dedicated team of directors and switchers.
> For a solo journalist, handling all of this is nearly impossible.

### WeaveCastStudio's Value (3:00 – 3:30)

> WeaveCastStudio solves this problem by using Gemini as a real-time AI director.
>
> Just speak, and the AI instantly displays the optimal video or map.
> From automated news collection and analysis to video clip generation,
> AI handles the entire pipeline.
>
> When breaking news arrives, it automatically appears on the ticker,
> letting the journalist focus on reporting.

### Multimodal / Agent Technology (3:30 – 3:50)

> Technically, we achieve real-time bidirectional conversation using
> Gemini Live API's voice I/O and Function Calling.
> The news collection pipeline uses Gemini to score article importance
> and automatically flag breaking news.
> Video clip generation combines Gemini's image generation with TTS.
>
> WeaveCastStudio — AI-powered, next-generation journalism.

---

## Recording Timeline

| Time | Action | Screen |
|------|--------|--------|
| 0:00 | Start recording, iranstrikemap already displayed | Middle East map + ticker |
| 0:25 | F9 → "Show me the Hormuz video" | Switch to video playback |
| 0:40 | F9 → "Show the new leader report" | Switch video |
| 0:55 | Run `--trigger-breaking` | Breaking news appears in ticker |
| 1:25 | F9 → "Tell me about the breaking news" | Gemini explains |
| 1:40 | Gemini auto-displays | Truth Social post |
| 1:55 | F9 → "Show Kharg Island on the map" | Map displayed |
| 2:10 | F9 → "Switch back to the overview map" | Middle East map |
| 2:30 | Transition to pitch | Slides or text overlay |
| 3:50 | End recording | — |

---

## Recording Commands

```bash
# Terminal 1: Launch main app
python gemini_live_client.py

# Terminal 2: Inject breaking news (at Scene 3 timing)
python demo_setup.py --trigger-breaking

# To retry: Clear breaking news flags
python demo_setup.py --clear-breaking
```
