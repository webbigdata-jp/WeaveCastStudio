"""
pull_from_gcs.py — GCSバケットからローカルにpull (pull_from_gcs.ps1 の置き換え)

使い方:
    python pull_from_gcs.py           # 全同期
    python pull_from_gcs.py --m3only  # M3のみ
    python pull_from_gcs.py --m1only  # M1のみ

content_index.json の扱い:
    GCS 側の content_index.json をダウンロードし、ローカルの
    WeaveCastStudio/content_index.json に entries をマージする。
    id が重複する場合は GCS 側のエントリで上書きする。
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── 設定 ──────────────────────────────────────────────────────────────────────

BUCKET = "gs://weavecaststudio-sync"

# このスクリプトは WeaveCastStudio/ 直下に置く想定
BASE_DIR = Path(__file__).parent.resolve()

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "gcs_pull.log"

# ── ロガー設定 ────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── gcloud ラッパー ───────────────────────────────────────────────────────────

def _run(cmd: list[str]) -> None:
    """gcloud コマンドを実行する。失敗時は例外を送出する。"""
    log.info(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # gcloud はエラーを stderr に出すことが多い
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"gcloud failed (exit {result.returncode}): {msg}")


# ── 同期処理 ──────────────────────────────────────────────────────────────────

def pull_m3() -> None:
    log.info("=== M3 pull start ===")

    # articles.db
    log.info("Downloading articles.db ...")
    dest_db = BASE_DIR / "compe_M3" / "data" / "articles.db"
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "gcloud", "storage", "cp",
        f"{BUCKET}/compe_M3/data/articles.db",
        str(dest_db),
    ])

    # crawl/ 配下（スクリーンショット・HTML）— 差分同期
    log.info("Syncing compe_M3/data/crawl/ ...")
    dest_crawl = BASE_DIR / "compe_M3" / "data" / "crawl"
    dest_crawl.mkdir(parents=True, exist_ok=True)
    _run([
        "gcloud", "storage", "rsync",
        f"{BUCKET}/compe_M3/data/crawl/",
        str(dest_crawl) + "/",
        "--recursive",
    ])

    log.info("=== M3 pull done ===")


def pull_m1() -> None:
    log.info("=== M1 pull start ===")

    # output/ 配下 — 差分同期
    log.info("Syncing compe_M1/output/ ...")
    dest_output = BASE_DIR / "compe_M1" / "output"
    dest_output.mkdir(parents=True, exist_ok=True)
    _run([
        "gcloud", "storage", "rsync",
        f"{BUCKET}/compe_M1/output/",
        str(dest_output) + "/",
        "--recursive",
    ])

    log.info("=== M1 pull done ===")


def pull_content_index() -> None:
    """
    GCS の content_index.json をローカルにマージする。

    マージ方針:
      - GCS 側の entries をローカルの entries に取り込む。
      - id が重複する場合は GCS 側のエントリで上書きする。
      - GCS に content_index.json が存在しない場合はスキップする。
    """
    log.info("=== content_index.json merge start ===")

    local_index_path = BASE_DIR / "content_index.json"

    # GCS からテンポラリファイルにダウンロード
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        _run([
            "gcloud", "storage", "cp",
            f"{BUCKET}/content_index.json",
            str(tmp_path),
        ])
    except RuntimeError as e:
        # GCS 側にファイルが存在しない場合はスキップ
        log.warning(f"content_index.json not found in GCS, skipping. ({e})")
        tmp_path.unlink(missing_ok=True)
        return

    # GCS 側を読み込む
    try:
        with open(tmp_path, encoding="utf-8") as f:
            gcs_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Failed to parse GCS content_index.json: {e}")
        tmp_path.unlink(missing_ok=True)
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    gcs_entries: list[dict] = gcs_data.get("entries", [])
    log.info(f"  GCS entries: {len(gcs_entries)}")

    # ローカル側を読み込む（存在しない場合は空で初期化）
    if local_index_path.exists():
        with open(local_index_path, encoding="utf-8") as f:
            local_data = json.load(f)
        local_entries: list[dict] = local_data.get("entries", [])
    else:
        local_data = {"last_updated": None, "entries": []}
        local_entries = []

    log.info(f"  Local entries before merge: {len(local_entries)}")

    # マージ: ローカルを id → entry の dict に変換し、GCS 側で上書き
    merged: dict[str, dict] = {e["id"]: e for e in local_entries}
    overwritten = 0
    added = 0
    for entry in gcs_entries:
        if entry["id"] in merged:
            overwritten += 1
        else:
            added += 1
        merged[entry["id"]] = entry

    # 元の順序を保ちつつ新規エントリを末尾に追加するため、
    # ローカルの id 順を基準に並べ直す
    local_ids_order = [e["id"] for e in local_entries]
    new_ids = [e["id"] for e in gcs_entries if e["id"] not in set(local_ids_order)]
    final_entries = [merged[eid] for eid in local_ids_order] + [merged[eid] for eid in new_ids]

    local_data["entries"] = final_entries
    local_data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # アトミック書き込み（temp + rename）
    tmp_out = local_index_path.with_suffix(".tmp")
    with open(tmp_out, "w", encoding="utf-8") as f:
        json.dump(local_data, f, indent=2, ensure_ascii=False)
    tmp_out.replace(local_index_path)

    log.info(
        f"  Merge complete — added: {added}, overwritten: {overwritten}, "
        f"total: {len(final_entries)}"
    )
    log.info("=== content_index.json merge done ===")


# ── エントリポイント ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull data from GCS bucket to local WeaveCastStudio directory."
    )
    parser.add_argument("--m3only", action="store_true", help="Pull M3 data only")
    parser.add_argument("--m1only", action="store_true", help="Pull M1 data only")
    args = parser.parse_args()

    log.info("===== GCS Pull Start =====")

    try:
        if args.m3only:
            pull_m3()
        elif args.m1only:
            pull_m1()
        else:
            pull_m3()
            pull_m1()

        # content_index.json は --m3only / --m1only に関わらず常にマージ
        pull_content_index()

    except RuntimeError as e:
        log.error(f"Pull failed: {e}")
        sys.exit(1)

    log.info("✅ GCS Pull Complete")


if __name__ == "__main__":
    main()
