#!/bin/bash
# sync_to_gcs.sh — M1/M3の生成データをGCSバケットにpush
#
# 使い方:
#   ./sync_to_gcs.sh          # 全同期
#   ./sync_to_gcs.sh --m3     # M3のみ
#   ./sync_to_gcs.sh --m1     # M1のみ

set -euo pipefail

BUCKET="gs://weavecaststudio-sync"
BASE_DIR="$HOME/WeaveCastStudio"
LOG_FILE="$BASE_DIR/logs/gcs_sync.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

sync_m3() {
    log "=== M3 sync start ==="

    # articles.db
    log "Uploading articles.db ..."
    gcloud storage cp \
        "$BASE_DIR/compe_M3/data/articles.db" \
        "$BUCKET/compe_M3/data/articles.db"

    # crawl/ 配下（スクリーンショット・HTML）— 差分同期
    log "Syncing compe_M3/data/crawl/ ..."
    gcloud storage rsync \
        "$BASE_DIR/compe_M3/data/crawl/" \
        "$BUCKET/compe_M3/data/crawl/" \
        --recursive \

    log "=== M3 sync done ==="
}

sync_m1() {
    log "=== M1 sync start ==="

    # output/ 配下のJSON — 差分同期
    log "Syncing compe_M1/output/ ..."
    gcloud storage rsync \
        "$BASE_DIR/compe_M1/output/" \
        "$BUCKET/compe_M1/output/" \
        --recursive \

    log "=== M1 sync done ==="
}

sync_shared() {
    log "=== Shared files sync start ==="

    # content_index.json（ルート直下）
    if [ -f "$BASE_DIR/content_index.json" ]; then
        log "Uploading content_index.json ..."
        gcloud storage cp \
            "$BASE_DIR/content_index.json" \
            "$BUCKET/content_index.json"
    fi

    log "=== Shared files sync done ==="
}

# --- メイン ---
mkdir -p "$BASE_DIR/logs"

case "${1:-all}" in
    --m3)  sync_m3; sync_shared ;;
    --m1)  sync_m1; sync_shared ;;
    *)     sync_m3; sync_m1; sync_shared ;;
esac

log "✅ GCS sync complete"
