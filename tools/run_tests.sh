#!/bin/bash
# run_tests.sh
# M1 / M3 の全テストを順番に実行する
#
# 使い方:
#   bash run_tests.sh           # 全テスト実行
#   bash run_tests.sh --m3-only # M3テストのみ
#   bash run_tests.sh --dry-run # Phase 3 を dry-run モードで実行

set -e

M1_DIR="$(cd "$(dirname "$0")/compe_M1" && pwd)"
M3_DIR="$(cd "$(dirname "$0")/compe_M3" && pwd)"

M3_ONLY=false
DRY_RUN=false
for arg in "$@"; do
  case $arg in
    --m3-only) M3_ONLY=true ;;
    --dry-run) DRY_RUN=true ;;
  esac
done

PASS=0
FAIL=0
SKIP=0

run_test() {
  local label="$1"
  local dir="$2"
  local cmd="$3"
  local implemented="$4"  # "yes" or "no"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "▶ $label"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [[ "$implemented" == "no" ]]; then
    echo "  [実装なし] スキップします"
    SKIP=$((SKIP + 1))
    return
  fi

  pushd "$dir" > /dev/null
  if eval "$cmd"; then
    echo "  ✅ PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "  ❌ FAIL: $label (exit code: $?)"
    FAIL=$((FAIL + 1))
  fi
  popd > /dev/null
}

echo "========================================"
echo " StoryWire Test Runner"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# ────────────────────────────────────────
# M1 テスト
# ────────────────────────────────────────
if ! $M3_ONLY; then
  echo ""
  echo "════════ M1 Tests ════════"

  run_test \
    "M1: source_collector (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: summarizer (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: script_writer (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: image_generator (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: narrator (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: video_composer (単体)" \
    "$M1_DIR" \
    "echo '[実装なし]'" \
    "no"

  run_test \
    "M1: フルパイプライン (--skip-upload)" \
    "$M1_DIR" \
    "uv run main.py --skip-upload" \
    "yes"
fi

# ────────────────────────────────────────
# M3 テスト
# ────────────────────────────────────────
echo ""
echo "════════ M3 Tests ════════"

run_test \
  "M3 Phase 1: DrissionCrawler + ArticleStore" \
  "$M3_DIR" \
  "uv run test_phase1.py" \
  "yes"

run_test \
  "M3 Phase 2: GeminiClient + GeminiAnalyst" \
  "$M3_DIR" \
  "uv run test_phase2.py" \
  "yes"

if $DRY_RUN; then
  run_test \
    "M3 Phase 3: BriefingComposer (dry-run)" \
    "$M3_DIR" \
    "uv run test_phase3.py --dry-run" \
    "yes"
else
  run_test \
    "M3 Phase 3: BriefingComposer (フル)" \
    "$M3_DIR" \
    "uv run test_phase3.py" \
    "yes"
fi

run_test \
  "M3 Phase 4: CrawlScheduler (単一ソース)" \
  "$M3_DIR" \
  "uv run test_phase4.py --source un_news" \
  "yes"

run_test \
  "M3 Phase 4: CrawlScheduler (スケジューラ起動確認)" \
  "$M3_DIR" \
  "uv run test_phase4.py --scheduler --duration 10" \
  "yes"

run_test \
  "M3 ArticleStore: M4向け検索メソッド" \
  "$M3_DIR" \
  "python3 -c \"
import sys, os, tempfile, json
sys.path.insert(0, '.')
from store.article_store import ArticleStore
from datetime import datetime, timezone

# 一時DBでテスト
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    tmp_db = f.name

try:
    store = ArticleStore(db_path=tmp_db)

    # テスト用記事を直接INSERT
    import sqlite3
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(tmp_db) as conn:
        conn.execute('''
            INSERT INTO articles
              (source_id, source_name, url, url_hash, title, summary,
               topics, key_entities, importance_score, credibility, tier,
               crawled_at, analyzed_at, is_breaking)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            'un_news', 'UN News',
            'https://news.un.org/test1', 'hash001',
            'UN Security Council meets on Iran sanctions',
            'The UN Security Council convened to discuss Iran.',
            json.dumps(['diplomatic','military']),
            json.dumps(['UN','Iran','Security Council']),
            8.5, 5, 1, now, now, False
        ))
        conn.execute('''
            INSERT INTO articles
              (source_id, source_name, url, url_hash, title, summary,
               topics, key_entities, importance_score, credibility, tier,
               crawled_at, analyzed_at, is_breaking)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            'reuters', 'Reuters',
            'https://reuters.com/test2', 'hash002',
            'BREAKING: Major earthquake hits Turkey',
            'A 7.8 magnitude earthquake struck southern Turkey.',
            json.dumps(['humanitarian','disaster']),
            json.dumps(['Turkey','AFAD']),
            9.5, 5, 1, now, now, True
        ))
        conn.commit()

    # search(): キーワード横断OR検索
    results = store.search('UN')
    assert len(results) >= 1, f'search(UN) expected >=1, got {len(results)}'
    assert any('UN' in (r.get('title') or '') or 'UN' in (r.get('key_entities') or '') for r in results)
    print(f'  search(UN): {len(results)} hit(s) ✅')

    # search(): min_importance フィルタ
    results_hi = store.search('UN', min_importance=9.0)
    assert len(results_hi) == 0, f'search(UN, min=9.0) expected 0, got {len(results_hi)}'
    print(f'  search(UN, min_importance=9.0): {len(results_hi)} hit(s) ✅')

    # get_breaking(): BREAKINGフラグ検索
    breaking = store.get_breaking()
    assert len(breaking) == 1, f'get_breaking() expected 1, got {len(breaking)}'
    assert breaking[0]['title'].startswith('BREAKING')
    print(f'  get_breaking(): {len(breaking)} hit(s) ✅')

    # mark_breaking(): フラグ操作
    store.mark_breaking([1], breaking=True)
    breaking2 = store.get_breaking()
    assert len(breaking2) == 2, f'after mark_breaking, expected 2, got {len(breaking2)}'
    print(f'  mark_breaking([1]): now {len(breaking2)} breaking ✅')

    # get_today_titles(): 直近24時間タイトル一覧
    titles = store.get_today_titles()
    assert len(titles) == 2, f'get_today_titles() expected 2, got {len(titles)}'
    assert 'title' in titles[0] and 'importance_score' in titles[0]
    # 軽量dictであること（text_content等の重フィールドが含まれない）
    assert 'text_content' not in titles[0], 'get_today_titles should return lightweight dict'
    print(f'  get_today_titles(): {len(titles)} articles, lightweight={\"text_content\" not in titles[0]} ✅')

    # get_today_titles(): min_importance フィルタ
    titles_hi = store.get_today_titles(min_importance=9.0)
    assert len(titles_hi) == 1, f'get_today_titles(min=9.0) expected 1, got {len(titles_hi)}'
    print(f'  get_today_titles(min_importance=9.0): {len(titles_hi)} article(s) ✅')

    print('  ArticleStore M4 search methods: ALL PASSED')

finally:
    os.unlink(tmp_db)
\"" \
  "yes"

if $DRY_RUN; then
  run_test \
    "M3 Phase 3: BriefingComposer short_clips (dry-run)" \
    "$M3_DIR" \
    "uv run test_phase3.py --short-clips --dry-run" \
    "yes"
fi

# ────────────────────────────────────────
# 共有モジュール テスト
# ────────────────────────────────────────
echo ""
echo "════════ Shared Module Tests ════════"

run_test \
  "M1 manifest.json: 生成・差分更新確認" \
  "$M1_DIR" \
  "python3 -c \"
import sys, json, tempfile, os
from pathlib import Path
from datetime import datetime, timezone

# main.pyと同じ_write_manifest/_register_content_indexを直接importして単体テスト
sys.path.insert(0, '.')
from main import OutputDirs, _write_manifest

# 一時ディレクトリでOutputDirsを模倣
with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    # OutputDirs._makeと同等の構造を作成
    for d in ['data','images','audio','video']:
        (root / d).mkdir()

    class FakeDirs:
        def __init__(self, r):
            self.root = r
            self.data  = r / 'data'
            self.images = r / 'images'
            self.audio  = r / 'audio'
            self.video  = r / 'video'

    dirs = FakeDirs(root)
    topic = {
        'title': 'Iran Nuclear Talks',
        'tags': ['iran', 'nuclear', 'diplomacy'],
        'importance_score': 8.5,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    # Phase 4相当: video_pathのみでmanifest書き出し
    fake_video = dirs.video / 'briefing.mp4'
    fake_video.write_text('dummy')
    _write_manifest(dirs, topic, video_path=fake_video)

    manifest_path = root / 'manifest.json'
    assert manifest_path.exists(), 'manifest.json not created'
    m = json.loads(manifest_path.read_text())

    assert m['module'] == 'M1', f'module: {m[\"module\"]}'
    assert m['topic']['title'] == 'Iran Nuclear Talks'
    assert m['artifacts']['video'] == str(fake_video)
    assert 'generated_at' in m
    print('  Phase 4 manifest write: ✅')

    # Phase 5相当: youtube_url + content_index_id を差分追記
    _write_manifest(
        dirs, topic,
        video_path=fake_video,
        youtube_url='https://youtube.com/watch?v=test123',
        content_index_id='m1_briefing_20260310_test',
    )
    m2 = json.loads(manifest_path.read_text())
    assert m2['youtube_url'] == 'https://youtube.com/watch?v=test123'
    assert m2['content_index_id'] == 'm1_briefing_20260310_test'
    # generated_atが変わっていないこと（差分更新）
    assert m2['generated_at'] == m['generated_at'], 'generated_at changed on update'
    print('  Phase 5 manifest update (diff): ✅')

    print('  M1 manifest.json: ALL PASSED')
\"" \
  "yes"

run_test \
  "ContentIndex: 基本動作確認" \
  "$(dirname "$0")" \
  "python3 -c \"
from content_index import ContentIndexManager, make_entry
from pathlib import Path
import tempfile, os

# 一時ファイルでテスト
with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
    tmp = f.name

try:
    mgr = ContentIndexManager(index_path=tmp)
    entry = make_entry(
        id='test_001',
        module='M3',
        content_type='screenshot',
        title='Test Entry',
        topic_tags=['test', 'unit'],
        importance_score=7.5,
    )
    mgr.add_entry(entry)
    results = mgr.get_by_tags(['test'])
    assert len(results) == 1, f'Expected 1, got {len(results)}'
    assert results[0]['id'] == 'test_001'
    stats = mgr.get_stats()
    assert stats['total'] == 1
    print('ContentIndex test passed:', stats)
finally:
    os.unlink(tmp)
\"" \
  "yes"

# ────────────────────────────────────────
# 結果サマリ
# ────────────────────────────────────────
echo ""
echo "========================================"
echo " Test Results"
echo "========================================"
echo "  ✅ PASS : $PASS"
echo "  ❌ FAIL : $FAIL"
echo "  ⏭  SKIP : $SKIP"
echo "========================================"

if [ $FAIL -gt 0 ]; then
  echo "Some tests FAILED."
  exit 1
else
  echo "All implemented tests PASSED."
  exit 0
fi


