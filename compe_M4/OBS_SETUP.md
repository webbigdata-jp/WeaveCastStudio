# OBS Browser Source Setup — Breaking News Ticker

## Prerequisites

- OBS Studio installed
- WeaveCastStudio M4 server running (`http://localhost:8765/overlay` is accessible)

---

## Steps

### 1. Verify Ticker Server

When running with gemini_live_client.py, the server starts automatically on launch.

To test standalone:

```bash
cd compe_M4
python breaking_news_server.py [db_path]
```

Open `http://localhost:8765/overlay` in a browser and verify the ticker is displayed.

### 2. Add a Browser Source in OBS

1. Open OBS
2. Select the scene you want to use
3. Click the "+" button in the "Sources" panel
4. Select **"Browser"**
5. Name it "Breaking News Ticker" (or similar) and click "OK"

### 3. Configure Browser Source Properties

| Property | Value |
|----------|-------|
| **URL** | `http://localhost:8765/overlay` |
| **Width** | `1920` |
| **Height** | `1080` |
| **Custom CSS** | (Clear this field — remove all default CSS) |
| **Page permissions** | Basic access |

**Important**: **Delete all CSS** in the "Custom CSS" field.
The default CSS includes `body { background-color: rgba(0,0,0,0); }` which is fine to keep,
but other rules like `overflow: hidden` may interfere with the ticker.

### 4. Set Layer Order

Arrange sources in the "Sources" panel in the following order (top = foreground):

```
Breaking News Ticker  ← Topmost (ticker overlay)
WeaveCast Media       ← Window Capture of MediaWindow
(Other sources)
```

Drag and drop to reorder sources.

### 5. Adjust Position and Size

The browser source is treated as a 1920×1080 transparent overlay.
Align it with the MediaWindow window capture at the same position and size.

- Right-click the browser source → "Transform" → "Fit to screen"
  or manually set position (0, 0) and size (1920, 1080).

### 6. Verify

1. Run `demo_setup.py --seed-db --run --breaking-delay 15`
2. Check the OBS preview:
   - News ticker appears at the bottom of the screen
   - After 15 seconds, the red "BREAKING NEWS" banner appears
   - After ~45 seconds, the ticker returns to normal mode
3. If everything works, integrate into your production scene

---

## Troubleshooting

### Ticker not showing

- Open `http://localhost:8765/overlay` directly in a browser to check
- Verify the server is running (`http://localhost:8765/status` should return JSON)
- In OBS browser source properties, click "Refresh cache of current page"

### Background not transparent (black background visible)

- Clear the "Custom CSS" field in browser source properties
- Do not use OBS "Color Key" filter on the browser source
  (the HTML already sets `background: transparent`)

### Ticker position is offset

- Verify the browser source size is set to 1920×1080
- Verify the OBS canvas size (Settings → Video → Base Resolution) is 1920×1080

### Breaking news animation not appearing

- Check `http://localhost:8765/status` — verify `has_breaking: true`
- Check SSE connection in browser DevTools (F12) → Network tab

---

## Changing the Port

To change the default port `8765`,
update `DEFAULT_PORT` in `breaking_news_server.py`
and update the OBS browser source URL accordingly.
