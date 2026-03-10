"""
compe_M4/vlc_player.py

M4 用 VLC プレーヤーラッパー。
- ContentIndex から動的に動画パスを取得して再生
- 再生・一時停止・停止・スキップ（次の動画へ切り替え）
- ウィンドウの最小化・復元（pygetwindow 使用）
"""

import sys
import time
import logging
from pathlib import Path

import vlc
import pygetwindow as gw

# プロジェクトルートを sys.path に追加（content_index.py を import するため）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from content_index import ContentIndexManager  # noqa: E402

logger = logging.getLogger(__name__)

# VLC ネイティブウィンドウのタイトルに含まれる文字列候補
# python-vlc は環境によって "VLC media player" または "VLC (Direct3D11 output)" 等になる
_VLC_WINDOW_TITLES = ["VLC media player", "Direct3D11 output", "Direct3D9 output", "VLC"]


class VLCPlayer:
    """
    python-vlc を使った動画プレーヤー。

    使い方:
        player = VLCPlayer()
        player.play_by_index_id("m3_clip_42_20260310_082606")
        time.sleep(5)
        player.pause()
        player.resume()
        player.swap("m1_briefing_20260310_080000")  # 再生中に別動画へ切り替え
        player.stop()
    """

    def __init__(self):
        self._instance = vlc.Instance()
        self._media_player: vlc.MediaPlayer = self._instance.media_player_new()
        self._content_mgr = ContentIndexManager()
        self._current_entry: dict | None = None

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    def _get_vlc_window(self):
        """
        VLC ウィンドウを取得して返す。見つからなければ None。

        python-vlc は環境によってウィンドウタイトルが異なる。
        - 独立ウィンドウ時: "VLC media player"
        - Python 子ウィンドウ時: "VLC (Direct3D11 output)" 等
        全ウィンドウを走査してタイトル候補にマッチするものを返す。
        """
        for _ in range(20):
            for win in gw.getAllWindows():
                title = win.title or ""
                if any(candidate in title for candidate in _VLC_WINDOW_TITLES):
                    logger.debug(f"VLC ウィンドウ発見: '{title}'")
                    return win
            time.sleep(0.1)
        logger.warning("VLC ウィンドウが見つかりませんでした")
        return None

    def _load_and_play(self, video_path: str) -> bool:
        """指定パスの動画をロードして再生開始する。"""
        p = Path(video_path)
        if not p.exists():
            logger.error(f"動画ファイルが見つかりません: {video_path}")
            return False

        media = self._instance.media_new(str(p))
        self._media_player.set_media(media)
        ret = self._media_player.play()
        if ret == -1:
            logger.error(f"VLC play() が失敗しました: {video_path}")
            return False

        logger.info(f"再生開始: {p.name}")
        return True

    def _resolve_video_path(self, entry: dict) -> str | None:
        """
        ContentIndex エントリから絶対パスを返す。
        video_path_abs があればそちらを優先。
        """
        abs_path = entry.get("video_path_abs")
        if abs_path and Path(abs_path).exists():
            return abs_path

        rel_path = entry.get("video_path")
        if rel_path:
            candidate = _PROJECT_ROOT / rel_path
            if candidate.exists():
                return str(candidate)

        return None

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def play_by_index_id(self, content_id: str) -> bool:
        """
        ContentIndex の ID を指定して再生する。

        Args:
            content_id: ContentIndex の id フィールド値
        Returns:
            成功なら True
        """
        # get_all() で全件取得して id で絞る（ContentIndexManager に get_by_id はない）
        entry = next(
            (e for e in self._content_mgr.get_all(sort_by_importance=False)
             if e.get("id") == content_id),
            None,
        )
        if entry is None:
            logger.error(f"ContentIndex にエントリが見つかりません: {content_id}")
            return False

        video_path = self._resolve_video_path(entry)
        if video_path is None:
            logger.error(f"動画パスが解決できません: {entry}")
            return False

        self._current_entry = entry
        return self._load_and_play(video_path)

    def play_by_path(self, video_path: str) -> bool:
        """
        動画ファイルパスを直接指定して再生する（テスト・デバッグ用）。

        Args:
            video_path: 動画ファイルの絶対/相対パス
        Returns:
            成功なら True
        """
        self._current_entry = None
        return self._load_and_play(video_path)

    def pause(self):
        """再生中なら一時停止する。"""
        state = self._media_player.get_state()
        if state == vlc.State.Playing:
            self._media_player.pause()
            logger.info("一時停止")
        else:
            logger.warning(f"pause() 呼び出し時の状態: {state}")

    def resume(self):
        """一時停止中なら再開する。"""
        state = self._media_player.get_state()
        if state == vlc.State.Paused:
            self._media_player.pause()  # pause() は toggle
            logger.info("再生再開")
        else:
            logger.warning(f"resume() 呼び出し時の状態: {state}")

    def stop(self):
        """再生を停止する。"""
        self._media_player.stop()
        self._current_entry = None
        logger.info("停止")

    def swap(self, content_id: str) -> bool:
        """
        再生中に別の動画へシームレスに切り替える。
        内部では stop → play を行うため一瞬ブランクが入る。

        Args:
            content_id: 切り替え先の ContentIndex id
        Returns:
            成功なら True
        """
        logger.info(f"動画切り替え開始: {content_id}")
        self._media_player.stop()
        # stop 直後に play すると VLC が間に合わないことがあるので少し待つ
        time.sleep(0.3)
        return self.play_by_index_id(content_id)

    def swap_by_path(self, video_path: str) -> bool:
        """
        再生中に別のパスの動画へ切り替える（テスト・デバッグ用）。
        """
        logger.info(f"動画切り替え開始 (path): {video_path}")
        self._media_player.stop()
        time.sleep(0.3)
        return self.play_by_path(video_path)

    # ------------------------------------------------------------------
    # ウィンドウ操作（pygetwindow）
    # ------------------------------------------------------------------

    def minimize(self):
        """VLC ウィンドウを最小化する。"""
        win = self._get_vlc_window()
        if win:
            win.minimize()
            logger.info("VLC ウィンドウを最小化")
        else:
            logger.warning("最小化: VLC ウィンドウが見つかりません")

    def restore(self):
        """最小化された VLC ウィンドウを復元する。"""
        win = self._get_vlc_window()
        if win:
            win.restore()
            logger.info("VLC ウィンドウを復元")
        else:
            logger.warning("復元: VLC ウィンドウが見つかりません")

    # ------------------------------------------------------------------
    # 状態確認
    # ------------------------------------------------------------------

    def get_state(self) -> vlc.State:
        """現在の VLC 再生状態を返す。"""
        return self._media_player.get_state()

    def get_time_ms(self) -> int:
        """現在の再生位置をミリ秒で返す。"""
        return self._media_player.get_time()

    def get_length_ms(self) -> int:
        """動画の総長をミリ秒で返す。"""
        return self._media_player.get_length()

    def is_playing(self) -> bool:
        return self._media_player.get_state() == vlc.State.Playing

    def current_entry(self) -> dict | None:
        """現在再生中の ContentIndex エントリを返す。"""
        return self._current_entry

    # ------------------------------------------------------------------
    # クリーンアップ
    # ------------------------------------------------------------------

    def release(self):
        """VLC リソースを解放する。"""
        self._media_player.stop()
        self._media_player.release()
        self._instance.release()
        logger.info("VLC リソースを解放")
