# pull_from_gcs.ps1 — GCSバケットからローカルにpull

param(
    [switch]$m3only,
    [switch]$m1only
)

$BUCKET = "gs://weavecaststudio-sync"
$SYNC_DIR = "C:\Users\dev1\Desktop\devpost\WeaveCastStudio\gcs_data"  # ← 実際のパスに変更

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$timestamp] === GCS Pull Start ==="

if (-not $m1only) {
    Write-Host "Pulling M3 data..."

    # articles.db
    gcloud storage cp "$BUCKET/compe_M3/data/articles.db" "$SYNC_DIR\compe_M3\data\articles.db"

    # crawl/ 配下
    gcloud storage rsync "$BUCKET/compe_M3/data/crawl/" "$SYNC_DIR\compe_M3\data\crawl\" --recursive
}

if (-not $m3only) {
    Write-Host "Pulling M1 data..."

    # output/ 配下
    gcloud storage rsync "$BUCKET/compe_M1/output/" "$SYNC_DIR\compe_M1\output\" --recursive
}

# source_index.json
Write-Host "Pulling source_index.json..."
gcloud storage cp "$BUCKET/source_index.json" "$SYNC_DIR\source_index.json" 2>$null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$timestamp] === GCS Pull Complete ==="