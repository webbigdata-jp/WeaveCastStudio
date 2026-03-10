# run_tests.ps1
# M1 / M3 の全テストを順番に実行する
#
# 使い方:
#   .\run_tests.ps1                            # 全テスト実行
#   .\run_tests.ps1 -m3only                    # M3テストのみ
#   .\run_tests.ps1 -dryrun                    # Phase 3 を dry-run モードで実行
#   .\run_tests.ps1 -only phase3               # Phase3のみ
#   .\run_tests.ps1 -only phase3,articlestore  # 複数指定
#
# -only に指定できるキーワード:
#   m1full      ... M1 フルパイプライン
#   phase1      ... M3 Phase1 (DrissionCrawler + ArticleStore)
#   phase2      ... M3 Phase2 (GeminiClient + GeminiAnalyst)
#   phase3      ... M3 Phase3 (BriefingComposer)
#   phase4      ... M3 Phase4 (CrawlScheduler)
#   articlestore... ArticleStore M4向け検索メソッド
#   manifest    ... M1 manifest.json 生成・差分更新
#   contentindex... ContentIndex 基本動作

param(
    [switch]$m3only,
    [switch]$dryrun,
    [string]$only = ""
)

$ROOT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$M1_DIR   = Join-Path $ROOT_DIR "compe_M1"
$M3_DIR   = Join-Path $ROOT_DIR "compe_M3"

$PASS = 0
$FAIL = 0
$SKIP = 0

# -only で指定されたキーワードをセットに変換
$onlySet = @()
if ($only -ne "") {
    $onlySet = $only.Split(",") | ForEach-Object { $_.Trim().ToLower() }
}

function Run-Test {
    param(
        [string]$Label,
        [string]$Dir,
        [scriptblock]$Cmd,
        [string]$Implemented = "yes",
        [string]$Key = ""
    )

    # -only 指定があり、このテストのキーが含まれていなければスキップ
    if ($script:onlySet.Count -gt 0 -and $Key -ne "" -and -not ($script:onlySet -contains $Key.ToLower())) {
        return
    }

    Write-Host ""
    Write-Host "----------------------------------------"
    Write-Host ">> $Label"
    Write-Host "----------------------------------------"

    if ($Implemented -eq "no") {
        Write-Host "  [未実装] スキップします"
        $script:SKIP++
        return
    }

    Push-Location $Dir
    try {
        & $Cmd
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            Write-Host "  PASS: $Label"
            $script:PASS++
        } else {
            Write-Host "  FAIL: $Label (exit code: $LASTEXITCODE)"
            $script:FAIL++
        }
    } catch {
        Write-Host "  FAIL: $Label (exception: $_)"
        $script:FAIL++
    } finally {
        Pop-Location
    }
}

Write-Host "========================================"
Write-Host " StoryWire Test Runner"
Write-Host " $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($onlySet.Count -gt 0) {
    Write-Host " [フィルタ] $($onlySet -join ', ')"
}
Write-Host "========================================"

# ────────────────────────────────────────
# M1 テスト
# ────────────────────────────────────────
if (-not $m3only) {
    Write-Host ""
    Write-Host "==== M1 Tests ===="

    Run-Test "M1: source_collector (単体)"  $M1_DIR {} "no" "m1unit"
    Run-Test "M1: summarizer (単体)"        $M1_DIR {} "no" "m1unit"
    Run-Test "M1: script_writer (単体)"     $M1_DIR {} "no" "m1unit"
    Run-Test "M1: image_generator (単体)"   $M1_DIR {} "no" "m1unit"
    Run-Test "M1: narrator (単体)"          $M1_DIR {} "no" "m1unit"
    Run-Test "M1: video_composer (単体)"    $M1_DIR {} "no" "m1unit"

    Run-Test "M1: フルパイプライン (--skip-upload)" $M1_DIR {
        uv run main.py --skip-upload
    } "yes" "m1full"
}

# ────────────────────────────────────────
# M3 テスト
# ────────────────────────────────────────
Write-Host ""
Write-Host "==== M3 Tests ===="

Run-Test "M3 Phase 1: DrissionCrawler + ArticleStore" $M3_DIR {
    uv run test_phase1.py
} "yes" "phase1"

Run-Test "M3 Phase 2: GeminiClient + GeminiAnalyst" $M3_DIR {
    uv run test_phase2.py
} "yes" "phase2"

if ($dryrun) {
    Run-Test "M3 Phase 3: BriefingComposer (dry-run)" $M3_DIR {
        uv run test_phase3.py --dry-run
    } "yes" "phase3"
} else {
    Run-Test "M3 Phase 3: BriefingComposer (フル)" $M3_DIR {
        uv run test_phase3.py
    } "yes" "phase3"
}

Run-Test "M3 Phase 4: CrawlScheduler (単一ソース)" $M3_DIR {
    uv run test_phase4.py --source un_news
} "yes" "phase4"

Run-Test "M3 Phase 4: CrawlScheduler (スケジューラ起動確認)" $M3_DIR {
    uv run test_phase4.py --scheduler --duration 10
} "yes" "phase4"

Run-Test "M3 ArticleStore: M4向け検索メソッド" $M3_DIR {
    $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
    @'
import sys, os, tempfile, json
sys.path.insert(0, '.')
from store.article_store import ArticleStore
from datetime import datetime, timezone

tmp_db = tempfile.mktemp(suffix='.db')

try:
    store = ArticleStore(db_path=tmp_db)

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

    results = store.search('UN')
    assert len(results) >= 1, f'search(UN) expected >=1, got {len(results)}'
    assert any('UN' in (r.get('title') or '') or 'UN' in (r.get('key_entities') or '') for r in results)
    print(f'  search(UN): {len(results)} hit(s) OK')

    results_hi = store.search('UN', min_importance=9.0)
    assert len(results_hi) == 0, f'search(UN, min=9.0) expected 0, got {len(results_hi)}'
    print(f'  search(UN, min_importance=9.0): {len(results_hi)} hit(s) OK')

    breaking = store.get_breaking()
    assert len(breaking) == 1, f'get_breaking() expected 1, got {len(breaking)}'
    assert breaking[0]['title'].startswith('BREAKING')
    print(f'  get_breaking(): {len(breaking)} hit(s) OK')

    store.mark_breaking([1], breaking=True)
    breaking2 = store.get_breaking()
    assert len(breaking2) == 2, f'after mark_breaking, expected 2, got {len(breaking2)}'
    print(f'  mark_breaking([1]): now {len(breaking2)} breaking OK')

    titles = store.get_today_titles()
    assert len(titles) == 2, f'get_today_titles() expected 2, got {len(titles)}'
    assert 'title' in titles[0] and 'importance_score' in titles[0]
    assert 'text_content' not in titles[0], 'get_today_titles should return lightweight dict'
    print(f'  get_today_titles(): {len(titles)} articles OK')

    titles_hi = store.get_today_titles(min_importance=9.0)
    assert len(titles_hi) == 1, f'get_today_titles(min=9.0) expected 1, got {len(titles_hi)}'
    print(f'  get_today_titles(min_importance=9.0): {len(titles_hi)} article(s) OK')

    print('  ArticleStore M4 search methods: ALL PASSED')

finally:
    import gc
    gc.collect()
    try:
        os.unlink(tmp_db)
    except PermissionError:
        pass  # Windowsでロック中でも致命的ではない
'@ | Set-Content -Encoding UTF8 $tmpScript
    try {
        uv run python $tmpScript
    } finally {
        Remove-Item $tmpScript -ErrorAction SilentlyContinue
    }
} "yes" "articlestore"

if ($dryrun) {
    Run-Test "M3 Phase 3: BriefingComposer short_clips (dry-run)" $M3_DIR {
        uv run test_phase3.py --short-clips --dry-run
    } "yes" "phase3"
}

# ────────────────────────────────────────
# 共有モジュール テスト
# ────────────────────────────────────────
Write-Host ""
Write-Host "==== Shared Module Tests ===="

Run-Test "M1 manifest.json: 生成・差分更新確認" $M1_DIR {
    $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
    @'
import sys, json, tempfile, os
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, '.')
from main import OutputDirs, _write_manifest

with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    for d in ['data','images','audio','video']:
        (root / d).mkdir()

    class FakeDirs:
        def __init__(self, r):
            self.root   = r
            self.data   = r / 'data'
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

    fake_video = dirs.video / 'briefing.mp4'
    fake_video.write_text('dummy')
    _write_manifest(dirs, topic, video_path=fake_video)

    manifest_path = root / 'manifest.json'
    assert manifest_path.exists(), 'manifest.json not created'
    m = json.loads(manifest_path.read_text())

    assert m['module'] == 'M1', f'module: {m["module"]}'
    assert m['topic']['title'] == 'Iran Nuclear Talks'
    assert m['artifacts']['video'] == str(fake_video)
    assert 'generated_at' in m
    print('  Phase 4 manifest write: OK')

    _write_manifest(
        dirs, topic,
        video_path=fake_video,
        youtube_url='https://youtube.com/watch?v=test123',
        content_index_id='m1_briefing_20260310_test',
    )
    m2 = json.loads(manifest_path.read_text())
    assert m2['youtube_url'] == 'https://youtube.com/watch?v=test123'
    assert m2['content_index_id'] == 'm1_briefing_20260310_test'
    assert m2['generated_at'] == m['generated_at'], 'generated_at changed on update'
    print('  Phase 5 manifest update (diff): OK')

    print('  M1 manifest.json: ALL PASSED')
'@ | Set-Content -Encoding UTF8 $tmpScript
    try {
        uv run python $tmpScript
    } finally {
        Remove-Item $tmpScript -ErrorAction SilentlyContinue
    }
} "yes" "manifest"

Run-Test "ContentIndex: 基本動作確認" $ROOT_DIR {
    $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
    @'
import sys, os, tempfile, json
sys.path.insert(0, '.')
from content_index import ContentIndexManager, make_entry

tmp = tempfile.mktemp(suffix='.json')

try:
    # 空ファイルではなく初期JSONを書き込んでから渡す
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({"entries": []}, f)

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
    try:
        os.unlink(tmp)
    except Exception:
        pass
'@ | Set-Content -Encoding UTF8 $tmpScript
    try {
        uv run python $tmpScript
    } finally {
        Remove-Item $tmpScript -ErrorAction SilentlyContinue
    }
} "yes" "contentindex"

# ────────────────────────────────────────
# 結果サマリ
# ────────────────────────────────────────
Write-Host ""
Write-Host "========================================"
Write-Host " Test Results"
Write-Host "========================================"
Write-Host "  PASS : $PASS"
Write-Host "  FAIL : $FAIL"
Write-Host "  SKIP : $SKIP"
Write-Host "========================================"

if ($FAIL -gt 0) {
    Write-Host "Some tests FAILED."
    exit 1
} else {
    Write-Host "All implemented tests PASSED."
    exit 0
}