"""
compe_M4/media_window.py

OBS キャプチャ対応メディアウィンドウ。
tkinter ウィンドウに VLC 動画と Pillow 静止画を表示する。
別スレッドで tkinter メインループを動かし、asyncio と共存する。

【機能】
  - 動画再生（VLC を tkinter Frame に埋め込み）
  - 静止画表示（Pillow → tkinter Canvas）
  - キーボードショートカット（F5〜F8）
  - OBS「ウィンドウキャプチャ」から認識可能

【依存】
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

# ウィンドウサイズ
WINDOW_WIDTH = 1920
WINDOW_HEIGHT = 1080
WINDOW_TITLE = "StoryWire Media"

# 静止画アセット設定ファイル
ASSETS_JSON = _HERE / "media_assets.json"
IMAGES_DIR = _HERE / "images"


# ══════════════════════════════════════════════════════════════════
# 静止画アセット管理
# ══════════════════════════════════════════════════════════════════

class ImageAssetManager:
    """
    media_assets.json から静止画アセットを管理する。
    初回起動時に source_url からダウンロードする。
    """

    def __init__(self, assets_json: Path = ASSETS_JSON):
        self._assets: list[dict] = []
        self._asset_map: dict[str, dict] = {}
        self._load(assets_json)

    def _load(self, assets_json: Path):
        if not assets_json.exists():
            logger.warning(f"アセット設定が見つかりません: {assets_json}")
            return
        try:
            with open(assets_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._assets = data.get("image_assets", [])
            self._asset_map = {a["id"]: a for a in self._assets}
            logger.info(f"静止画アセット: {len(self._assets)} 件ロード")
        except Exception as e:
            logger.error(f"アセット設定の読み込みに失敗: {e}")

    def ensure_downloaded(self):
        """全アセットがローカルに存在するか確認し、なければダウンロードする。"""
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        for asset in self._assets:
            local = _HERE / asset["local_path"]
            if local.exists():
                logger.debug(f"既存: {asset['id']} → {local}")
                continue
            url = asset.get("source_url")
            if not url:
                logger.warning(f"source_url が未設定: {asset['id']}")
                continue
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"ダウンロード中: {asset['id']} ← {url}")
                req = urllib.request.Request(url, headers={
                    "User-Agent": "StoryWire/1.0 (media asset downloader)"
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    local.write_bytes(resp.read())
                logger.info(f"ダウンロード完了: {asset['id']} → {local}")
            except Exception as e:
                logger.error(f"ダウンロード失敗: {asset['id']}: {e}")

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
# MediaWindow（tkinter + VLC + Pillow）
# ══════════════════════════════════════════════════════════════════

class MediaWindow:
    """
    別スレッドで tkinter ウィンドウを動かすメディア表示ウィンドウ。

    使い方:
        window = MediaWindow()
        window.start()          # 別スレッドでウィンドウ起動（最小化状態）
        window.play_video(path) # 動画再生（ウィンドウ復元）
        window.show_image(path) # 静止画表示（ウィンドウ復元）
        window.stop()           # 再生停止
        window.minimize()       # ウィンドウ最小化
        window.restore()        # ウィンドウ復元
        window.close()          # 終了
    """

    def __init__(self):
        self._root: Optional[tk.Tk] = None
        self._video_frame: Optional[tk.Frame] = None
        self._canvas: Optional[tk.Canvas] = None
        self._tk_image: Optional[ImageTk.PhotoImage] = None  # GC防止用参照保持

        self._vlc_instance: Optional[vlc.Instance] = None
        self._media_player: Optional[vlc.MediaPlayer] = None

        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._mode: str = "idle"  # "idle" | "video" | "image"

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    def start(self):
        """別スレッドで tkinter ウィンドウを起動する。"""
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        logger.info("MediaWindow 起動完了")

    def _run_tk(self):
        """tkinter メインループ（別スレッドで実行）。"""
        self._root = tk.Tk()
        self._root.title(WINDOW_TITLE)
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self._root.configure(bg="black")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- 動画用 Frame（VLC 埋め込み先）---
        self._video_frame = tk.Frame(self._root, bg="black",
                                     width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
        self._video_frame.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

        # --- 静止画用 Canvas ---
        self._canvas = tk.Canvas(self._root, bg="black",
                                 width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
                                 highlightthickness=0)
        # canvas は必要時にのみ place する

        # --- VLC 初期化 ---
        self._vlc_instance = vlc.Instance()
        self._media_player = self._vlc_instance.media_player_new()
        # VLC の映像出力先を tkinter Frame に埋め込む
        if sys.platform == "win32":
            self._media_player.set_hwnd(self._video_frame.winfo_id())
        elif sys.platform.startswith("linux"):
            self._media_player.set_xwindow(self._video_frame.winfo_id())
        elif sys.platform == "darwin":
            self._media_player.set_nsobject(self._video_frame.winfo_id())

        # --- 起動時は画面外に配置（OBSは最小化ウィンドウを認識できないため）---
        # 一度 update して winfo_id を確定させた後、画面外に移動
        self._root.update()
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

        self._ready.set()
        self._root.mainloop()

    def _on_close(self):
        """ウィンドウの×ボタンが押された場合 → 画面外に移動（閉じない）。"""
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

    def close(self):
        """ウィンドウを完全に閉じる。"""
        if self._media_player:
            self._media_player.stop()
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # スレッドセーフな tkinter 操作ヘルパー
    # ------------------------------------------------------------------

    def _invoke(self, func, *args):
        """tkinter スレッド上で関数を実行する（スレッドセーフ）。"""
        if self._root:
            try:
                self._root.after(0, func, *args)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 動画再生
    # ------------------------------------------------------------------

    def play_video(self, video_path: str) -> bool:
        """動画ファイルを再生する。ウィンドウが最小化されていたら復元する。"""
        p = Path(video_path)
        if not p.exists():
            logger.error(f"動画ファイルが見つかりません: {video_path}")
            return False

        # 静止画モードだった場合、canvas を非表示にする
        self._invoke(self._switch_to_video_mode)

        media = self._vlc_instance.media_new(str(p))
        self._media_player.set_media(media)
        ret = self._media_player.play()
        if ret == -1:
            logger.error(f"VLC play() が失敗: {video_path}")
            return False

        self._mode = "video"
        self._invoke(self._move_onscreen)
        logger.info(f"動画再生開始: {p.name}")
        return True

    def _switch_to_video_mode(self):
        """canvas を非表示にして video_frame を前面に出す。"""
        self._canvas.place_forget()
        self._video_frame.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
        self._video_frame.lift()

    def swap_video(self, video_path: str) -> bool:
        """再生中に別の動画へ切り替える。"""
        self._media_player.stop()
        # VLC が停止するまで少し待つ
        for _ in range(20):
            if self._media_player.get_state() == vlc.State.Stopped:
                break
            time.sleep(0.1)
        return self.play_video(video_path)

    # ------------------------------------------------------------------
    # 静止画表示
    # ------------------------------------------------------------------

    def show_image(self, image_path: str) -> bool:
        """静止画を表示する。動画再生中なら停止してから切り替える。"""
        p = Path(image_path)
        if not p.exists():
            logger.error(f"画像ファイルが見つかりません: {image_path}")
            return False

        # 動画を停止
        if self._mode == "video":
            self._media_player.stop()

        self._mode = "image"
        self._invoke(self._display_image, str(p))
        self._invoke(self._move_onscreen)
        logger.info(f"静止画表示: {p.name}")
        return True

    def _display_image(self, image_path: str):
        """tkinter スレッド上で画像を Canvas に描画する。"""
        try:
            img = Image.open(image_path)
            # ウィンドウサイズにフィットさせる（アスペクト比維持）
            img.thumbnail((WINDOW_WIDTH, WINDOW_HEIGHT), Image.Resampling.LANCZOS)

            # Canvas を前面に出す
            self._video_frame.place_forget()
            self._canvas.place(x=0, y=0, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
            self._canvas.lift()

            self._tk_image = ImageTk.PhotoImage(img)
            self._canvas.delete("all")
            # 中央に配置
            x = WINDOW_WIDTH // 2
            y = WINDOW_HEIGHT // 2
            self._canvas.create_image(x, y, anchor=tk.CENTER, image=self._tk_image)
        except Exception as e:
            logger.error(f"画像表示エラー: {e}")

    # ------------------------------------------------------------------
    # 再生コントロール
    # ------------------------------------------------------------------

    def pause(self):
        """再生中なら一時停止する。"""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Playing:
                self._media_player.pause()
                logger.info("一時停止")

    def resume(self):
        """一時停止中なら再開する。"""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Paused:
                self._media_player.pause()  # toggle
                logger.info("再生再開")

    def toggle_pause(self):
        """再生/一時停止をトグルする。"""
        if self._mode == "video" and self._media_player:
            state = self._media_player.get_state()
            if state == vlc.State.Playing:
                self._media_player.pause()
                logger.info("一時停止")
            elif state == vlc.State.Paused:
                self._media_player.pause()
                logger.info("再生再開")

    def stop(self):
        """再生・表示を停止し、ウィンドウを画面外に移動する。"""
        if self._media_player:
            self._media_player.stop()
        self._mode = "idle"
        self._invoke(self._clear_display)
        self._invoke(self._move_offscreen)
        logger.info("停止・画面外移動")

    def _clear_display(self):
        """Canvas をクリアする。"""
        if self._canvas:
            self._canvas.delete("all")
            self._canvas.place_forget()
        self._tk_image = None

    # ------------------------------------------------------------------
    # ウィンドウ操作
    # ------------------------------------------------------------------

    def minimize(self):
        """ウィンドウを画面外に移動する（OBSが認識できるよう最小化は使わない）。"""
        self._invoke(self._move_offscreen)
        logger.info("ウィンドウ画面外移動")

    def restore(self):
        """ウィンドウを画面内に戻す。"""
        self._invoke(self._move_onscreen)
        logger.info("ウィンドウ復元")

    def _move_offscreen(self):
        """ウィンドウを画面外に移動する。"""
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+-{WINDOW_WIDTH + 100}+0")

    def _move_onscreen(self):
        """ウィンドウを画面内（0,0）に戻す。"""
        self._root.deiconify()
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+0+0")

    # ------------------------------------------------------------------
    # 状態確認
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
# キーボードショートカット登録
# ══════════════════════════════════════════════════════════════════

def register_hotkeys(window: MediaWindow):
    """
    グローバルキーボードショートカットを登録する。
    F5: 再生/一時停止トグル
    F6: 停止
    F7: 最小化
    F8: 復元
    """
    import keyboard as kb

    kb.on_press_key("f5", lambda _: window.toggle_pause(), suppress=False)
    kb.on_press_key("f6", lambda _: window.stop(), suppress=False)
    kb.on_press_key("f7", lambda _: window.minimize(), suppress=False)
    kb.on_press_key("f8", lambda _: window.restore(), suppress=False)

    logger.info("ホットキー登録: F5=再生/停止トグル, F6=停止, F7=最小化, F8=復元")


# ══════════════════════════════════════════════════════════════════
# ContentIndex 連携ヘルパー
# ══════════════════════════════════════════════════════════════════

def resolve_content_path(entry: dict) -> Optional[str]:
    """ContentIndex エントリから動画/静止画の絶対パスを返す。"""
    # 動画
    for key in ("video_path_abs", "video_path"):
        p = entry.get(key)
        if p:
            path = Path(p)
            if not path.is_absolute():
                path = _PROJECT_ROOT / p
            if path.exists():
                return str(path)
    # スクリーンショット
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
# テスト用エントリポイント
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # アセットダウンロード
    mgr = ImageAssetManager()
    mgr.ensure_downloaded()

    # ウィンドウ起動
    window = MediaWindow()
    window.start()
    register_hotkeys(window)

    print("MediaWindow テスト起動")
    print("  F5: 再生/一時停止トグル")
    print("  F6: 停止")
    print("  F7: 最小化")
    print("  F8: 復元")
    print()

    # テスト: 静止画表示
    path = mgr.get_path("hormuz_map")
    if path:
        print(f"静止画テスト: {path}")
        window.show_image(path)
    else:
        print("hormuz_map が見つかりません")

    print("Ctrl+C で終了")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        window.close()
        print("終了")
