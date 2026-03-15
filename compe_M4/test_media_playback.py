"""
test_media_playback.py

MediaWindow の静止画・動画表示を単体テストするスクリプト。

【テスト対象】
  1. media_assets.json で定義された静止画アセット（assets/ 配下）
  2. ArticleStore 経由のスクリーンショット画像
  3. ArticleStore 経由の動画（ai_image_path / screenshot_path）
  4. .avif 形式の画像対応

【使い方】
  python test_media_playback.py [--db PATH_TO_ARTICLES_DB]

  --db を省略した場合は ../compe_M3/data/articles.db をデフォルトで使用。
  ArticleStore が見つからない場合でも assets テストは実行可能。

【操作】
  テスト項目ごとに 3 秒表示 → 自動で次へ進む。
  全テスト終了後、手動確認モードへ移行（番号入力で任意のアセットを表示）。
  q で終了。
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# ── パス設定 ──
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent

# media_window.py と同じディレクトリに配置する想定だが、
# テスト時は compe_M4 の場所を探す
_COMPE_M4 = None
for candidate in [_HERE, _HERE / "compe_M4", _PROJECT_ROOT / "compe_M4"]:
    if (candidate / "media_window.py").exists():
        _COMPE_M4 = candidate
        break

if _COMPE_M4 is None:
    print("ERROR: media_window.py が見つかりません。")
    print("  このスクリプトを compe_M4/ ディレクトリに配置するか、")
    print("  media_window.py と同じディレクトリで実行してください。")
    sys.exit(1)

# compe_M4 を sys.path に追加
if str(_COMPE_M4) not in sys.path:
    sys.path.insert(0, str(_COMPE_M4))

# プロジェクトルート（content_index.py がある場所）を sys.path に追加
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ArticleStore 用
_M3_ROOT = _PROJECT_ROOT / "compe_M3"
if str(_M3_ROOT) not in sys.path:
    sys.path.insert(0, str(_M3_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from media_window import MediaWindow, ImageAssetManager, IMAGES_DIR  # noqa: E402


# ══════════════════════════════════════════════════════════════════
# テスト結果の記録
# ══════════════════════════════════════════════════════════════════

class TestResult:
    def __init__(self):
        self.results: list[dict] = []

    def add(self, name: str, status: str, detail: str = ""):
        self.results.append({"name": name, "status": status, "detail": detail})
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
        print(f"  {icon} {name}: {status}  {detail}")

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        skipped = sum(1 for r in self.results if r["status"] == "SKIP")
        print(f"\n{'═' * 55}")
        print(f"  テスト結果: {passed}/{total} PASS, {failed} FAIL, {skipped} SKIP")
        print(f"{'═' * 55}")
        if failed > 0:
            print("\n  失敗したテスト:")
            for r in self.results:
                if r["status"] == "FAIL":
                    print(f"    ❌ {r['name']}: {r['detail']}")
        return failed == 0


# ══════════════════════════════════════════════════════════════════
# テスト 1: media_assets.json の静止画アセット
# ══════════════════════════════════════════════════════════════════

def test_image_assets(window: MediaWindow, result: TestResult, display_sec: float = 3.0):
    """media_assets.json 内の全静止画アセットを順番に表示テストする。"""
    print("\n" + "─" * 55)
    print("  テスト 1: media_assets.json 静止画アセット")
    print("─" * 55)

    mgr = ImageAssetManager()
    assets = mgr.list_all()

    if not assets:
        result.add("assets_load", "FAIL", "media_assets.json が空または読み込み失敗")
        return

    result.add("assets_load", "PASS", f"{len(assets)} 件ロード")

    for asset in assets:
        aid = asset["id"]
        path = mgr.get_path(aid)
        local_path = asset.get("local_path", "")

        # 動画アセットはスキップ（.mp4 など）
        ext = Path(local_path).suffix.lower()
        if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
            # 動画テスト（VLC）
            if path:
                try:
                    ok = window.play_video(path)
                    time.sleep(display_sec)
                    window.stop()
                    time.sleep(0.5)
                    if ok:
                        result.add(f"asset_video:{aid}", "PASS", f"{local_path}")
                    else:
                        result.add(f"asset_video:{aid}", "FAIL", f"play_video()=False")
                except Exception as e:
                    result.add(f"asset_video:{aid}", "FAIL", str(e))
            else:
                result.add(f"asset_video:{aid}", "FAIL", f"ファイル未存在: {local_path}")
            continue

        # 静止画テスト
        if not path:
            result.add(f"asset:{aid}", "FAIL", f"ファイル未存在: {local_path}")
            continue

        try:
            ok = window.show_image(path)
            time.sleep(display_sec)
            window.stop()
            time.sleep(0.5)
            if ok:
                result.add(f"asset:{aid}", "PASS", f"{local_path}")
            else:
                result.add(f"asset:{aid}", "FAIL", f"show_image()=False")
        except Exception as e:
            result.add(f"asset:{aid}", "FAIL", str(e))


# ══════════════════════════════════════════════════════════════════
# テスト 2: ContentIndex 経由の画像・動画
# ══════════════════════════════════════════════════════════════════

def test_content_index_media(
    window: MediaWindow, result: TestResult, display_sec: float = 3.0
):
    """ContentIndex (content_index.json) に登録されたメディアを表示テストする。"""
    print("\n" + "─" * 55)
    print("  テスト 2: ContentIndex 経由のメディア")
    print("─" * 55)

    try:
        from content_index import ContentIndexManager
    except ImportError:
        result.add("content_index_import", "SKIP", "ContentIndexManager が import できません")
        return

    try:
        mgr = ContentIndexManager()
        entries = mgr.get_all(sort_by_importance=True)
        result.add("content_index_load", "PASS", f"{len(entries)} 件登録")
    except Exception as e:
        result.add("content_index_load", "FAIL", str(e))
        return

    if not entries:
        result.add("content_index_entries", "SKIP", "ContentIndex にエントリがありません")
        return

    max_test = 5  # 最大5件テスト
    tested = 0

    for entry in entries[:max_test]:
        eid = entry.get("id", "?")
        title = (entry.get("title", "") or "")[:40]

        # 動画パスを探す
        media_path = None
        media_type = None
        for key in ("video_path_abs", "video_path"):
            p = entry.get(key)
            if p:
                path = Path(p)
                if not path.is_absolute():
                    path = _PROJECT_ROOT / p
                if path.exists():
                    media_path = str(path)
                    media_type = "video"
                    break

        # スクリーンショット/画像パスを探す
        if not media_path:
            for key in ("screenshot_path_abs", "screenshot_path"):
                p = entry.get(key)
                if p:
                    path = Path(p)
                    if not path.is_absolute():
                        path = _PROJECT_ROOT / p
                    if path.exists():
                        media_path = str(path)
                        media_type = "image"
                        break

        if not media_path:
            result.add(f"ci:{eid}", "FAIL", f"メディアファイル未存在: {title}")
            continue

        ext = Path(media_path).suffix.lower()
        # 拡張子から実際のメディアタイプを再判定
        if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
            media_type = "video"
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif", ".tiff"):
            media_type = "image"

        try:
            if media_type == "video":
                ok = window.play_video(media_path)
            else:
                ok = window.show_image(media_path)
            time.sleep(display_sec)
            window.stop()
            time.sleep(0.5)
            if ok:
                result.add(f"ci:{eid}", "PASS", f"[{media_type}] {title} ({Path(media_path).name})")
            else:
                result.add(f"ci:{eid}", "FAIL", f"表示失敗: {Path(media_path).name}")
            tested += 1
        except Exception as e:
            result.add(f"ci:{eid}", "FAIL", str(e))

    if tested == 0:
        result.add("content_index_media", "SKIP", "表示可能なメディアが見つかりません")


# ══════════════════════════════════════════════════════════════════
# テスト 3: フォーマット対応確認（.avif 等）
# ══════════════════════════════════════════════════════════════════

def test_format_support(window: MediaWindow, result: TestResult, display_sec: float = 2.0):
    """assets/ 配下の各画像形式の対応を確認する。"""
    print("\n" + "─" * 55)
    print("  テスト 3: 画像フォーマット対応")
    print("─" * 55)

    assets_dir = _COMPE_M4 / "assets"
    if not assets_dir.exists():
        result.add("format_check", "SKIP", f"assets/ ディレクトリが存在しません: {assets_dir}")
        return

    # 拡張子ごとに1ファイルずつテスト
    tested_formats = set()
    for f in sorted(assets_dir.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in tested_formats:
            continue
        if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
            # 動画
            try:
                ok = window.play_video(str(f))
                time.sleep(display_sec)
                window.stop()
                time.sleep(0.5)
                if ok:
                    result.add(f"format:{ext}", "PASS", f"{f.name}")
                else:
                    result.add(f"format:{ext}", "FAIL", f"play_video()=False: {f.name}")
            except Exception as e:
                result.add(f"format:{ext}", "FAIL", f"{f.name}: {e}")
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif", ".tiff"):
            # 静止画
            try:
                ok = window.show_image(str(f))
                time.sleep(display_sec)
                window.stop()
                time.sleep(0.5)
                if ok:
                    result.add(f"format:{ext}", "PASS", f"{f.name}")
                else:
                    result.add(f"format:{ext}", "FAIL", f"show_image()=False: {f.name}")
            except Exception as e:
                result.add(f"format:{ext}", "FAIL", f"{f.name}: {e}")
        else:
            continue
        tested_formats.add(ext)

    if not tested_formats:
        result.add("format_check", "SKIP", "テスト可能なファイルがありません")


# ══════════════════════════════════════════════════════════════════
# 手動確認モード
# ══════════════════════════════════════════════════════════════════

def interactive_mode(window: MediaWindow):
    """テスト後に手動で個別アセットを表示確認できるモード。"""
    print("\n" + "═" * 55)
    print("  手動確認モード")
    print("═" * 55)

    mgr = ImageAssetManager()
    assets = mgr.list_all()

    if assets:
        print("\n  静止画/動画アセット:")
        for i, a in enumerate(assets):
            path_status = "✅" if mgr.get_path(a["id"]) else "❌ 未存在"
            print(f"    {i}: [{a['id']}] {a['title']}  {path_status}")

    print("\n  番号を入力して表示 / パスを直接入力 / s=停止 / q=終了")

    while True:
        try:
            cmd = input("\n  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd.lower() == "q":
            break
        elif cmd.lower() == "s":
            window.stop()
            print("  停止しました")
            continue

        # 番号入力
        if cmd.isdigit():
            idx = int(cmd)
            if 0 <= idx < len(assets):
                a = assets[idx]
                path = mgr.get_path(a["id"])
                if not path:
                    print(f"  ❌ ファイルが存在しません: {a.get('local_path')}")
                    continue
                ext = Path(path).suffix.lower()
                if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
                    window.play_video(path)
                else:
                    window.show_image(path)
                print(f"  表示中: [{a['id']}] {a['title']}")
            else:
                print(f"  ❌ 範囲外: 0-{len(assets)-1}")
            continue

        # パス直接入力
        p = Path(cmd)
        if not p.exists():
            # compe_M4 基準で試す
            p = _COMPE_M4 / cmd
        if p.exists():
            ext = p.suffix.lower()
            if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
                window.play_video(str(p))
            else:
                window.show_image(str(p))
            print(f"  表示中: {p}")
        else:
            print(f"  ❌ ファイルが見つかりません: {cmd}")


# ══════════════════════════════════════════════════════════════════
# メイン
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="MediaWindow 表示テスト")
    parser.add_argument(
        "--display-sec",
        type=float,
        default=3.0,
        help="各テスト項目の表示秒数 (デフォルト: 3.0)",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="自動テストのみ実行し手動確認モードをスキップ",
    )
    args = parser.parse_args()

    print("═" * 55)
    print("  MediaWindow 表示テスト")
    print("═" * 55)
    print(f"  compe_M4: {_COMPE_M4}")
    print(f"  表示秒数: {args.display_sec}s")

    # MediaWindow 起動
    window = MediaWindow()
    window.start()
    print("  MediaWindow 起動完了\n")

    result = TestResult()

    try:
        # テスト 1: 静止画アセット
        test_image_assets(window, result, display_sec=args.display_sec)

        # テスト 2: ContentIndex 経由
        test_content_index_media(window, result, display_sec=args.display_sec)

        # テスト 3: フォーマット対応
        test_format_support(window, result, display_sec=args.display_sec)

        # 結果サマリ
        all_pass = result.summary()

        # 手動確認モード
        if not args.no_interactive:
            interactive_mode(window)

    except KeyboardInterrupt:
        print("\n中断されました")
    finally:
        window.stop()
        time.sleep(0.5)
        window.close()
        print("終了")


if __name__ == "__main__":
    main()
