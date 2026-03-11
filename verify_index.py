"""
verify_index.py

ContentIndex の整合性チェックツール。

video_path_abs / screenshot_path_abs が登録されているエントリに対して
ファイルの実在を確認し、存在しないものを一覧表示する。
--force オプション指定時は確認なしで削除する。

使い方:
  # チェックのみ（削除しない）
  python verify_index.py

  # 不正エントリを確認後に対話的に削除
  python verify_index.py --delete

  # 確認なしで自動削除
  python verify_index.py --delete --force

  # インデックスファイルのパスを明示する場合
  python verify_index.py --index /path/to/content_index.json
"""

import argparse
import logging
import sys
from pathlib import Path

# GeminiLiveAgent/ を sys.path に追加
sys.path.insert(0, str(Path(__file__).parent))

from content_index import ContentIndexManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_index")


def _get_file_paths(entry: dict) -> list[tuple[str, str]]:
    """
    エントリからチェック対象のファイルパスを取得する。
    戻り値: [(フィールド名, 絶対パス文字列), ...]
    """
    paths = []
    for field in ("video_path_abs", "screenshot_path_abs", "manifest_path_abs"):
        val = entry.get(field)
        if val:
            paths.append((field, val))
    return paths


def find_invalid_entries(mgr: ContentIndexManager) -> list[dict]:
    """
    ファイルが存在しないエントリを返す。
    各エントリに "missing_files" キーを付加して返す。
    """
    invalid = []
    all_entries = mgr.get_all(sort_by_importance=False)

    for entry in all_entries:
        missing = []
        for field, path_str in _get_file_paths(entry):
            if not Path(path_str).exists():
                missing.append((field, path_str))
        if missing:
            entry_copy = dict(entry)
            entry_copy["missing_files"] = missing
            invalid.append(entry_copy)

    return invalid


def print_invalid_entries(invalid: list[dict]) -> None:
    """不正エントリの一覧を整形して出力する"""
    print(f"\n{'='*70}")
    print(f"  不正エントリ: {len(invalid)} 件")
    print(f"{'='*70}")
    for i, entry in enumerate(invalid, start=1):
        print(f"\n[{i:03d}] id            : {entry['id']}")
        print(f"       module        : {entry.get('module', '-')}")
        print(f"       type          : {entry.get('type', '-')}")
        print(f"       title         : {entry.get('title', '')[:60]}")
        print(f"       created_at    : {entry.get('created_at', '-')}")
        for field, path_str in entry["missing_files"]:
            print(f"       ❌ {field}: {path_str}")
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="ContentIndex 整合性チェック: ファイルが存在しないエントリを検出・削除する"
    )
    parser.add_argument(
        "--index",
        default=None,
        help="content_index.json のパス（省略時はデフォルトパスを使用）",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="不正エントリを削除する（デフォルトはチェックのみ）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="--delete と組み合わせて使用。確認プロンプトをスキップして自動削除する",
    )
    args = parser.parse_args()

    if args.force and not args.delete:
        parser.error("--force は --delete と組み合わせて使用してください")

    # ── ContentIndexManager 初期化 ──
    try:
        mgr = ContentIndexManager(index_path=args.index)
    except Exception as e:
        logger.error(f"❌ ContentIndexManager の初期化に失敗しました: {e}")
        sys.exit(1)

    stats = mgr.get_stats()
    logger.info(f"ContentIndex: total={stats['total']} entries, last_updated={stats['last_updated']}")

    # ── 不正エントリの検出 ──
    logger.info("ファイル存在チェック中...")
    invalid = find_invalid_entries(mgr)

    if not invalid:
        logger.info("✅ 不正エントリは見つかりませんでした。インデックスは正常です。")
        sys.exit(0)

    # ── 一覧表示 ──
    print_invalid_entries(invalid)

    if not args.delete:
        logger.info(
            f"⚠️  {len(invalid)} 件の不正エントリが見つかりました。\n"
            "削除するには --delete オプションを指定してください。\n"
            "確認なしで自動削除するには --delete --force を指定してください。"
        )
        sys.exit(0)

    # ── 削除確認（--force なし）──
    if not args.force:
        print(f"上記 {len(invalid)} 件のエントリを ContentIndex から削除しますか？")
        answer = input("削除する場合は 'yes' と入力してください: ").strip().lower()
        if answer != "yes":
            logger.info("キャンセルしました。削除は行いませんでした。")
            sys.exit(0)

    # ── 削除実行 ──
    deleted = 0
    failed = 0
    for entry in invalid:
        if mgr.remove_entry(entry["id"]):
            deleted += 1
        else:
            logger.warning(f"削除失敗: {entry['id']}")
            failed += 1

    logger.info(f"✅ 削除完了: {deleted} 件削除, {failed} 件失敗")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
