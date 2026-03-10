"""
compe_M4/test_vlc.py

VLCPlayer の手動動作確認用 CLI スクリプト。

使い方:
    # ContentIndex から動画を選んで対話テスト
    python test_vlc.py

    # 動画パスを直接指定してテスト
    python test_vlc.py --path "C:/path/to/video.mp4"

    # ContentIndex の ID を指定してテスト
    python test_vlc.py --id "m3_clip_42_20260310_082606"
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from vlc_player import VLCPlayer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# ContentIndex から動画を選択するヘルパー
# ------------------------------------------------------------------

def pick_video_from_index() -> tuple[str | None, str | None]:
    """
    ContentIndex の動画エントリ一覧を表示し、ユーザーに選択させる。

    Returns:
        (content_id, video_path_abs) のタプル。
        エントリがなければ (None, None)。
    """
    try:
        from content_index import ContentIndexManager
        mgr = ContentIndexManager()
        entries = mgr.get_all(sort_by_importance=False)
    except Exception as e:
        logger.error(f"ContentIndex の読み込みに失敗: {e}")
        return None, None

    # 動画ファイルが存在するエントリのみ抽出
    valid = []
    for e in entries:
        abs_path = e.get("video_path_abs") or ""
        rel_path = e.get("video_path") or ""
        resolved = (
            abs_path if Path(abs_path).exists()
            else str(_PROJECT_ROOT / rel_path) if rel_path and (_PROJECT_ROOT / rel_path).exists()
            else None
        )
        if resolved:
            valid.append((e, resolved))

    if not valid:
        print("\n[!] ContentIndex に再生可能な動画エントリがありません。")
        print("    --path オプションで直接ファイルパスを指定してください。")
        return None, None

    print("\n--- ContentIndex の動画一覧 ---")
    for i, (e, path) in enumerate(valid):
        score = e.get("importance_score", "?")
        breaking = " [BREAKING]" if e.get("is_breaking") else ""
        ctype = e.get("type", "?")
        print(f"  [{i}] {e.get('id')}")
        print(f"       title : {e.get('title', '(no title)')}{breaking}")
        print(f"       type  : {ctype}  score: {score}")
        print(f"       path  : {path}")

    while True:
        raw = input(f"\n番号を選択してください (0-{len(valid)-1}): ").strip()
        try:
            idx = int(raw)
            if 0 <= idx < len(valid):
                entry, path = valid[idx]
                return entry["id"], path
        except ValueError:
            pass
        print("  有効な番号を入力してください。")


# ------------------------------------------------------------------
# 対話ループ
# ------------------------------------------------------------------

HELP_TEXT = """
コマンド一覧:
  p        一時停止 / 再開（トグル）
  s        停止
  t        現在の再生位置と長さを表示
  m        ウィンドウを最小化
  r        ウィンドウを復元
  sw       別の動画に切り替え（ContentIndex から選択）
  swp      別の動画に切り替え（パスを直接入力）
  q        終了
  h        このヘルプを表示
"""


def run_interactive(player: VLCPlayer, swap_entries: list):
    """対話コマンドループ。"""
    print(HELP_TEXT)
    paused = False

    while True:
        try:
            cmd = input("コマンド> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します。")
            break

        if cmd == "h":
            print(HELP_TEXT)

        elif cmd == "p":
            if paused:
                player.resume()
                paused = False
                print("  ▶ 再生再開")
            else:
                player.pause()
                paused = True
                print("  ⏸ 一時停止")

        elif cmd == "s":
            player.stop()
            print("  ⏹ 停止")
            break

        elif cmd == "t":
            pos_ms = player.get_time_ms()
            len_ms = player.get_length_ms()
            state = player.get_state()
            pos_s = pos_ms / 1000
            len_s = len_ms / 1000 if len_ms > 0 else 0
            print(f"  状態: {state}  位置: {pos_s:.1f}s / {len_s:.1f}s")

        elif cmd == "m":
            player.minimize()
            print("  🔽 最小化")

        elif cmd == "r":
            player.restore()
            print("  🔼 復元")

        elif cmd == "sw":
            if not swap_entries:
                print("  [!] ContentIndex に切り替え候補がありません。")
                continue
            print("\n--- 切り替え先を選択 ---")
            for i, (e, path) in enumerate(swap_entries):
                print(f"  [{i}] {e.get('id')}  {e.get('title', '')}")
            raw = input(f"番号 (0-{len(swap_entries)-1}): ").strip()
            try:
                idx = int(raw)
                if 0 <= idx < len(swap_entries):
                    entry, _ = swap_entries[idx]
                    ok = player.swap(entry["id"])
                    paused = False
                    print(f"  ↩ 切り替え {'成功' if ok else '失敗'}: {entry['id']}")
                else:
                    print("  有効な番号を入力してください。")
            except ValueError:
                print("  有効な番号を入力してください。")

        elif cmd == "swp":
            raw_path = input("動画パスを入力: ").strip().strip('"')
            if not raw_path:
                continue
            ok = player.swap_by_path(raw_path)
            paused = False
            print(f"  ↩ 切り替え {'成功' if ok else '失敗'}: {raw_path}")

        elif cmd == "q":
            print("終了します。")
            break

        else:
            print(f"  不明なコマンド: {cmd}  (h でヘルプ)")


# ------------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="VLCPlayer 動作確認 CLI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--path", help="再生する動画ファイルのパス")
    group.add_argument("--id", help="ContentIndex の content_id")
    args = parser.parse_args()

    player = VLCPlayer()

    try:
        # --- 再生開始 ---
        if args.path:
            print(f"\n[path モード] {args.path}")
            ok = player.play_by_path(args.path)
        elif args.id:
            print(f"\n[id モード] {args.id}")
            ok = player.play_by_index_id(args.id)
        else:
            # ContentIndex から選択
            content_id, video_path = pick_video_from_index()
            if content_id is None and video_path is None:
                sys.exit(1)
            if content_id:
                ok = player.play_by_index_id(content_id)
            else:
                ok = player.play_by_path(video_path)

        if not ok:
            print("[ERROR] 再生開始に失敗しました。ログを確認してください。")
            sys.exit(1)

        # VLC ウィンドウが開くまで少し待つ
        time.sleep(1.0)
        print("\n▶ 再生中...")

        # --- swap 候補をプリロード（ContentIndex の他エントリ）---
        swap_entries = []
        try:
            from content_index import ContentIndexManager
            mgr = ContentIndexManager()
            all_entries = mgr.get_all(sort_by_importance=False)
            for e in all_entries:
                abs_path = e.get("video_path_abs") or ""
                rel_path = e.get("video_path") or ""
                resolved = (
                    abs_path if Path(abs_path).exists()
                    else str(_PROJECT_ROOT / rel_path)
                    if rel_path and (_PROJECT_ROOT / rel_path).exists()
                    else None
                )
                if resolved:
                    swap_entries.append((e, resolved))
        except Exception as e:
            logger.warning(f"swap 候補のロードに失敗: {e}")

        # --- 対話ループ ---
        run_interactive(player, swap_entries)

    finally:
        player.release()


if __name__ == "__main__":
    main()
