#!/bin/bash
# clean.sh
# M1 / M3 のキャッシュ・一時ファイルをクリアする
#
# 使い方:
#   bash clean.sh          # DBとキャッシュのみ削除（出力動画は残す）
#   bash clean.sh --all    # 出力動画・ContentIndexも含めて全削除

set -e

M1_DIR="$(cd "$(dirname "$0")/compe_M1" && pwd)"
M3_DIR="$(cd "$(dirname "$0")/compe_M3" && pwd)"
INDEX_FILE="$(dirname "$0")/content_index.json"

ALL=false
if [[ "$1" == "--all" ]]; then
  ALL=true
fi

echo "=== StoryWire Cache Clean ==="
echo "M1: $M1_DIR"
echo "M3: $M3_DIR"
echo ""

# ── Python キャッシュ ──
echo "[1] Removing __pycache__ and .pyc files..."
find "$M1_DIR" "$M3_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$M1_DIR" "$M3_DIR" -name "*.pyc" -delete 2>/dev/null || true
echo "    Done."

# ── M3: SQLite DB ──
echo "[2] Removing M3 ArticleStore DB..."
if [ -f "$M3_DIR/data/articles.db" ]; then
  rm -f "$M3_DIR/data/articles.db"
  echo "    Removed: data/articles.db"
else
  echo "    Not found: data/articles.db (skip)"
fi

# ── M3: クロールキャッシュ（スクリーンショット・HTML）──
echo "[3] Removing M3 crawl cache..."
if [ -d "$M3_DIR/data/crawl" ]; then
  rm -rf "$M3_DIR/data/crawl"
  echo "    Removed: data/crawl/"
else
  echo "    Not found: data/crawl/ (skip)"
fi

# ── M3: ログ ──
echo "[4] Removing log files..."
find "$M1_DIR" "$M3_DIR" "$(dirname "$0")" -maxdepth 2 -name "*.log" -delete 2>/dev/null || true
echo "    Done."

if $ALL; then
  echo ""
  echo "--- --all mode: removing outputs and ContentIndex ---"

  # ── M1: 出力ディレクトリ ──
  echo "[5] Removing M1 output/..."
  if [ -d "$M1_DIR/output" ]; then
    rm -rf "$M1_DIR/output"
    echo "    Removed: compe_M1/output/"
  else
    echo "    Not found: compe_M1/output/ (skip)"
  fi

  # ── M3: 出力ディレクトリ ──
  echo "[6] Removing M3 data/output/..."
  if [ -d "$M3_DIR/data/output" ]; then
    rm -rf "$M3_DIR/data/output"
    echo "    Removed: compe_M3/data/output/"
  else
    echo "    Not found: compe_M3/data/output/ (skip)"
  fi

  # ── ContentIndex ──
  echo "[7] Removing content_index.json..."
  if [ -f "$INDEX_FILE" ]; then
    rm -f "$INDEX_FILE"
    echo "    Removed: content_index.json"
  else
    echo "    Not found: content_index.json (skip)"
  fi
fi

echo ""
echo "=== Clean complete ==="
if ! $ALL; then
  echo "Tip: Run 'bash clean.sh --all' to also remove output videos and ContentIndex."
fi

