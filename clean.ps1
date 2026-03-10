# clean.ps1
# M1 / M3 のキャッシュ・一時ファイルをクリアする
#
# 使い方:
#   .\clean.ps1        # DBとキャッシュのみ削除（出力動画は残す）
#   .\clean.ps1 -all   # 出力動画・ContentIndexも含めて全削除

param(
    [switch]$all
)

$ROOT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Path
$M1_DIR     = Join-Path $ROOT_DIR "compe_M1"
$M3_DIR     = Join-Path $ROOT_DIR "compe_M3"
$INDEX_FILE = Join-Path $ROOT_DIR "content_index.json"

Write-Host "=== StoryWire Cache Clean ==="
Write-Host "M1: $M1_DIR"
Write-Host "M3: $M3_DIR"
Write-Host ""

# ── Python キャッシュ ──
Write-Host "[1] Removing __pycache__ and .pyc files..."
Get-ChildItem -Path $M1_DIR, $M3_DIR -Recurse -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $M1_DIR, $M3_DIR -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "    Done."

# ── M3: SQLite DB ──
Write-Host "[2] Removing M3 ArticleStore DB..."
$dbPath = Join-Path $M3_DIR "data\articles.db"
if (Test-Path $dbPath) {
    Remove-Item $dbPath -Force
    Write-Host "    Removed: data\articles.db"
} else {
    Write-Host "    Not found: data\articles.db (skip)"
}

# ── M3: クロールキャッシュ（スクリーンショット・HTML）──
Write-Host "[3] Removing M3 crawl cache..."
$crawlPath = Join-Path $M3_DIR "data\crawl"
if (Test-Path $crawlPath) {
    Remove-Item $crawlPath -Recurse -Force
    Write-Host "    Removed: data\crawl\"
} else {
    Write-Host "    Not found: data\crawl\ (skip)"
}

# ── ログ ──
Write-Host "[4] Removing log files..."
@($ROOT_DIR, $M1_DIR, $M3_DIR) | ForEach-Object {
    Get-ChildItem -Path $_ -Filter "*.log" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $_ -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        Get-ChildItem -Path $_.FullName -Filter "*.log" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "    Done."

if ($all) {
    Write-Host ""
    Write-Host "--- -all mode: removing outputs and ContentIndex ---"

    # ── M1: 出力ディレクトリ ──
    Write-Host "[5] Removing M1 output\..."
    $m1Output = Join-Path $M1_DIR "output"
    if (Test-Path $m1Output) {
        Remove-Item $m1Output -Recurse -Force
        Write-Host "    Removed: compe_M1\output\"
    } else {
        Write-Host "    Not found: compe_M1\output\ (skip)"
    }

    # ── M3: 出力ディレクトリ ──
    Write-Host "[6] Removing M3 data\output\..."
    $m3Output = Join-Path $M3_DIR "data\output"
    if (Test-Path $m3Output) {
        Remove-Item $m3Output -Recurse -Force
        Write-Host "    Removed: compe_M3\data\output\"
    } else {
        Write-Host "    Not found: compe_M3\data\output\ (skip)"
    }

    # ── ContentIndex ──
    Write-Host "[7] Removing content_index.json..."
    if (Test-Path $INDEX_FILE) {
        Remove-Item $INDEX_FILE -Force
        Write-Host "    Removed: content_index.json"
    } else {
        Write-Host "    Not found: content_index.json (skip)"
    }
}

Write-Host ""
Write-Host "=== Clean complete ==="
if (-not $all) {
    Write-Host "Tip: Run '.\clean.ps1 -all' to also remove output videos and ContentIndex."
}