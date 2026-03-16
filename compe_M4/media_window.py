"""
compe_M4/media_window.py

OBS-compatible media window.
Displays VLC video and Pillow still images in a tkinter window.
Runs the tkinter main loop in a separate thread to coexist with asyncio.

Features:
  - Video playback (VLC embedded in a tkinter Frame)
  - Still image display (Pillow -> tkinter Canvas)
  - Keyboard shortcuts (F5-F8)
  - Recognisable by OBS "Window Capture"

Dependencies:
  uv add python-vlc Pillow keyboard
"""

import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from typing import Optional

import vlc
from PIL import Image, ImageTk

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent

# Window dimensions
WINDOW_WIDTH = 1920
WINDOW_HEIGHT = 1080
WINDOW_TITLE = "WeaveCast Media"

# Still-image asset config file
ASSETS_JSON = _HERE / "media_assets.json"
IMAGES_DIR = _HERE / "assets"


# ══════════════════════════════════════════════════════════════════
# Still-image asset manager
# ══════════════════════════════════════════════════════════════════

class ImageAssetManager:
    """
    Manages still-image assets defined in media_assets.json.
    Downloads assets from source_url on first run if not present locally.
    """

    def __init__(self, assets_json: Path = ASSETS_JSON):
        self._assets: list[dict] = []
        self._asset_map: dict[str, dict] = {}
        self._load(assets_json)

    def _load(self, assets_json: Path):
        if not assets_json.exists():
            logger.warning(f"Asset config not found: {assets_json}")
            return
        try:
            with open(assets_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._assets = data.get("image_assets", [])
            self._asset_map = {a["id"]: a for a in self._assets}
            logger.info(f"Image assets loaded: {len(self._assets)} item(s)")
        except Exception as e:
            logger.error(f"Failed to load asset config: {e}")

    def ensure_downloaded(self):
        """Check that all assets exist locally; download any that are missing."""
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        for asset in self._assets:
            local = _HERE / asset["local_path"]
            if local.exists():
                logger.debug(f"Already exists: {asset['id']} -> {local}")
                continue
            url = asset.get("source_url")
            if not url:
                logger.warning(f"source_url not set: {asset['id']}")
                continue
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Downloading: {asset['id']} <- {url}")
                req = urllib.request.Request(url, headers={
                    "User-Agent": "WeaveCast/1.0 (media asset downloader)"
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    local.write_bytes(resp.read())
                logger.info(f"Download complete: {asset['id']} -> {local}")
            except Exception as e:
                logger.error(f"Download failed: {asset['id']}: {e}")

    def get(self, asset_id: str) -> Optional[dict]:
        return self._asset_map.get(asset_id)

    def get_path(self, asset_id: str) -> Optional[str]:
        asset = self._asset_map.get(asset_id)
        if not asset:
            return None
        local = _HERE / asset["local_path"]
        return str(local) if local.exists() else None

    def list_all(self) -> list[dict]:
        return list(self._assets)


# ══════════════════════════════════════════════════════════════════
# MediaWindow (tkinter + VLC + Pillow)
# ══════════════════════════════════════════════════════════════════

class MediaWindow:
    """
    Media display window running a tkinter window in a separate thread.

    Usage:
        window = MediaWindow()
        window.start()          # Launch window in a separate thread (starts off-screen)
        window.play_video(path) # Play video (restores window)
        window.show_image(path) # Display still image (restores window)
        window.stop()           # Stop playback
        window.minimize()       # Move window off-screen
        window.restore()        # Bring window back on-screen
        window.close()          # Shut down
    """

    def __init__(self):
        self._root: Optional[tk.Tk] = None
        self._video_frame: Optional[tk.Frame] = None
        self._canvas: Optional[tk.Canvas] = None
        self._tk_image: Optional[ImageTk.PhotoImage] = None  # Keep reference to prevent GC

        self._vlc_instance: Optional[vlc.Instance] = None
        self._media_player: Optional[vlc.MediaPlayer] = None

        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._mode: str = "idle"  # "idle" | "video" | "image"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Launch the tkinter window in a separate thread."""
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        logger.info("MediaWindow started")

    def _run_tk(self):
        """tkinter main loop (runs in a separate thread)."""
        self._root = tk.Tk()
        self._root.title(WINDOW_TITLE)
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self._root.configure(bg="black")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Frame for video (VLC embed target) ---
        self._video_frame = tk.Frame(self._root, bg="black",
                                     width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
        self._video_frame.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

        # --- Canvas for still images ---
        self._canvas = tk.Canvas(self._root, bg="black",
                                 width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
                                 highlightthickness=0)
        # Canvas is placed only when needed

        # --- VLC initialisation ---
        self._vlc_instance = vlc.Instance()
        self._media_player = self._vlc_instance.media_player_new()
        # Embed VLC video output into the tkinter Frame
        if sys.platform == "win32":
            self._media_player.set_hwnd(self._video_frame.winfo_id())
        elif sys.platform.startswith("linux"):
            self._media_player.set_xwindow(self._video_frame.winfo_id())
        elif sys.platform == "darwin":
            self._media_player.set_nsobject(self._video_frame.winfo_id())

        # --- Start off-screen (OBS cannot capture a minimised window) ---
        # Call update() first to confirm winfo_id, then move off-screen
        self._root.update()
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

        self._ready.set()
        self._root.mainloop()

    def _on_close(self):
        """When the window's X button is clicked -> move off-screen (do not close)."""
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

    def close(self):
        """Fully close the window."""
        if self._media_player:
            self._media_player.stop()
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Thread-safe tkinter helpers
    # ------------------------------------------------------------------

    def _invoke(self, func, *args):
        """Execute a function on the tkinter thread (thread-safe)."""
        if self._root:
            try:
                self._root.after(0, func, *args)
            except Exception:
                pass

    def _invoke_and_wait(self, func, *args, timeout: float = 2.0):
        """Execute a function on the tkinter thread and wait for completion."""
        if not self._root:
            return
        done = threading.Event()

        def _wrapper():
            try:
                func(*args)
            finally:
                done.set()

        try:
            self._root.after(0, _wrapper)
        except Exception:
            return
        done.wait(timeout=timeout)

    # ------------------------------------------------------------------
    # Video playback
    # ------------------------------------------------------------------

    def play_video(self, video_path: str) -> bool:
        """Play a video file. Restores the window if it is off-screen."""
        p = Path(video_path)
        if not p.exists():
            logger.error(f"Video file not found: {video_path}")
            return False

        # If currently in image mode, hide the canvas first (wait for completion)
        self._invoke_and_wait(self._switch_to_video_mode)

        media = self._vlc_instance.media_new(str(p))
        self._media_player.set_media(media)
        ret = self._media_player.play()
        if ret == -1:
            logger.error(f"VLC play() failed: {video_path}")
            return False

        self._mode = "video"
        self._invoke(self._move_onscreen)
        logger.info(f"Video playback started: {p.name}")
        return True

    def _switch_to_video_mode(self):
        """Hide the canvas and bring the video_frame to the front."""
        self._canvas.place_forget()
        self._video_frame.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

    def swap_video(self, video_path: str) -> bool:
        """Switch to a different video while one is already playing."""
        self._media_player.stop()
        # Wait briefly for VLC to stop
        for _ in range(20):
            if self._media_player.get_state() == vlc.State.Stopped:
                break
            time.sleep(0.1)
        return self.play_video(video_path)

    # ------------------------------------------------------------------
    # Still-image display
    # ------------------------------------------------------------------

    def show_image(self, image_path: str) -> bool:
        """Display a still image. Stops video playback first if active."""
        p = Path(image_path)
        if not p.exists():
            logger.error(f"Image file not found: {image_path}")
            return False

        # Stop video if playing
        if self._mode == "video":
            self._media_player.stop()

        self._mode = "image"
        self._invoke(self._display_image, str(p))
        self._invoke(self._move_onscreen)
        logger.info(f"Still image displayed: {p.name}")
        return True

    def _display_image(self, image_path: str):
        """Draw the image on the Canvas (runs on the tkinter thread)."""
        try:
            img = Image.open(image_path)
            # Fit to window size while preserving aspect ratio (upscaling allowed)
            img_w, img_h = img.size
            scale = min(WINDOW_WIDTH / img_w, WINDOW_HEIGHT / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Bring canvas to front (place_forget -> place puts it on top)
            self._video_frame.place_forget()
            self._canvas.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

            self._tk_image = ImageTk.PhotoImage(img)
            self._canvas.delete("all")
            # Centre the image
            x = WINDOW_WIDTH // 2
            y = WINDOW_HEIGHT // 2
            self._canvas.create_image(x, y, anchor=tk.CENTER, image=self._tk_image)
        except Exception as e:
            logger.error(f"Image display error: {e}")

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def pause(self):
        """Pause playback if a video is playing."""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Playing:
                self._media_player.pause()
                logger.info("Paused")

    def resume(self):
        """Resume playback if a video is paused."""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Paused:
                self._media_player.pause()  # toggle
                logger.info("Resumed")

    def toggle_pause(self):
        """Toggle between play and pause."""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Playing:
                self._media_player.pause()
                logger.info("Paused")
            elif state == vlc.State.Paused:
                self._media_player.pause()
                logger.info("Resumed")

    def stop(self):
        """Stop playback/display and move the window off-screen."""
        if self._media_player:
            self._media_player.stop()
        self._mode = "idle"
        self._invoke(self._clear_display)
        self._invoke(self._move_offscreen)
        logger.info("Stopped and moved off-screen")

    def _clear_display(self):
        """Clear the Canvas."""
        if self._canvas:
            self._canvas.delete("all")
            self._canvas.place_forget()
        self._tk_image = None

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def minimize(self):
        """Move the window off-screen (avoid iconify so OBS can still capture it)."""
        self._invoke(self._move_offscreen)
        logger.info("Window moved off-screen")

    def restore(self):
        """Bring the window back on-screen."""
        self._invoke(self._move_onscreen)
        logger.info("Window restored")

    def _move_offscreen(self):
        """Move the window off-screen."""
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

    def _move_onscreen(self):
        """Return the window to the visible area (0, 0)."""
        self._root.deiconify()
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+0+0")

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_mode(self) -> str:
        return self._mode

    def is_playing(self) -> bool:
        if self._mode == "video" and self._media_player:
            return self._media_player.get_state() == vlc.State.Playing
        return False

    def get_vlc_state(self):
        if self._media_player:
            return self._media_player.get_state()
        return None


# ══════════════════════════════════════════════════════════════════
# Keyboard shortcut registration
# ══════════════════════════════════════════════════════════════════

def register_hotkeys(window: MediaWindow):
    """
    Register global keyboard shortcuts.
    F5: Play / pause toggle
    F6: Stop
    F7: Minimize (move off-screen)
    F8: Restore
    """
    import keyboard as kb

    kb.on_press_key("f5", lambda _: window.toggle_pause(), suppress=False)
    kb.on_press_key("f6", lambda _: window.stop(), suppress=False)
    kb.on_press_key("f7", lambda _: window.minimize(), suppress=False)
    kb.on_press_key("f8", lambda _: window.restore(), suppress=False)

    logger.info("Hotkeys registered: F5=play/pause toggle, F6=stop, F7=minimize, F8=restore")


# ══════════════════════════════════════════════════════════════════
# ContentIndex integration helper
# ══════════════════════════════════════════════════════════════════

def resolve_content_path(entry: dict) -> Optional[str]:
    """Return the absolute path to the video or still image for a ContentIndex entry."""
    # Video
    for key in ("video_path_abs", "video_path"):
        p = entry.get(key)
        if p:
            path = Path(p)
            if not path.is_absolute():
                path = _PROJECT_ROOT / p
            if path.exists():
                return str(path)
    # Screenshot
    for key in ("screenshot_path_abs", "screenshot_path"):
        p = entry.get(key)
        if p:
            path = Path(p)
            if not path.is_absolute():
                path = _PROJECT_ROOT / p
            if path.exists():
                return str(path)
    return None


# ══════════════════════════════════════════════════════════════════
# Test entry point
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Download assets
    mgr = ImageAssetManager()
    mgr.ensure_downloaded()

    # Launch window
    window = MediaWindow()
    window.start()
    register_hotkeys(window)

    print("MediaWindow test launch")
    print("  F5: play/pause toggle")
    print("  F6: stop")
    print("  F7: minimize")
    print("  F8: restore")
    print()

    # Test: display a still image
    path = mgr.get_path("hormuz_map")
    if path:
        print(f"Still image test: {path}")
        window.show_image(path)
    else:
        print("hormuz_map not found")

    print("Ctrl+C to exit")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        window.close()
        print("Exited")
