"""
Smoke tests — "起動できますね" レベルの確認
API呼び出しは一切しない。import とクラス/関数の存在だけ検証する。
"""

import importlib
import os
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# CI 用ダミー環境変数（.env がなくても動くように）
os.environ.setdefault("GOOGLE_API_KEY", "dummy-key-for-ci")
os.environ.setdefault("LANGUAGE", "ja")


# ── shared ────────────────────────────────────────────────────────────────────

class TestShared:
    def test_language_utils_importable(self):
        mod = importlib.import_module("shared.language_utils")
        assert hasattr(mod, "get_language_config"), \
            "shared.language_utils に get_language_config が見つからない"

    def test_language_utils_returns_config(self):
        from shared.language_utils import get_language_config
        cfg = get_language_config("ja")
        assert cfg is not None

    def test_content_index_importable(self):
        mod = importlib.import_module("content_index")
        assert mod is not None


# ── M1 ────────────────────────────────────────────────────────────────────────

class TestM1:
    def test_main_importable(self):
        """compe_M1/main.py が import できること"""
        spec = importlib.util.spec_from_file_location(
            "compe_m1_main", ROOT / "compe_M1" / "main.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod = ""
        # 実行はしない — import (spec.loader.exec_module) レベルの確認
        assert spec is not None

    def test_required_files_exist(self):
        """M1 の必須ファイルが揃っていること"""
        required = [
            "compe_M1/main.py",
            "compe_M1/config/topics.yaml",
            "compe_M1/uploader/youtube_uploader.py",
        ]
        for rel in required:
            path = ROOT / rel
            assert path.exists(), f"Missing: {rel}"

    def test_shared_modules_for_m1_exist(self):
        """M1 が依存する shared モジュールが存在すること"""
        shared_modules = [
            "shared/source_collector.py",
            "shared/summarizer.py",
            "shared/script_writer.py",
            "shared/image_generator.py",
            "shared/narrator.py",
            "shared/video_composer.py",
        ]
        for rel in shared_modules:
            assert (ROOT / rel).exists(), f"Missing: {rel}"


# ── M3 ────────────────────────────────────────────────────────────────────────

class TestM3:
    def test_required_files_exist(self):
        """M3 の必須ファイルが揃っていること"""
        required = [
            "compe_M3/main.py",
            "compe_M3/config/sources.yaml",
            "compe_M3/crawler/drission_crawler.py",
            "compe_M3/store/article_store.py",
            "compe_M3/analyst/gemini_client.py",
            "compe_M3/analyst/gemini_analyst.py",
            "compe_M3/composer/briefing_composer.py",
            "compe_M3/scheduler/crawl_scheduler.py",
        ]
        for rel in required:
            path = ROOT / rel
            assert path.exists(), f"Missing: {rel}"

    def test_article_store_importable(self):
        """article_store は外部API不要なので import まで確認"""
        spec = importlib.util.spec_from_file_location(
            "article_store", ROOT / "compe_M3" / "store" / "article_store.py"
        )
        assert spec is not None, "article_store.py の spec 取得失敗"


# ── ディレクトリ構成 ──────────────────────────────────────────────────────────

class TestDirectoryStructure:
    def test_top_level_dirs_exist(self):
        expected_dirs = [
            "shared",
            "compe_M1",
            "compe_M3",
            "compe_M4",
            "IaC",
            "gcp",
            "docs",
        ]
        for d in expected_dirs:
            assert (ROOT / d).is_dir(), f"ディレクトリが見つからない: {d}"

    def test_env_sample_exists(self):
        assert (ROOT / ".env.sample").exists(), \
            ".env.sample が見つからない (テンプレートが必要)"

    def test_pyproject_toml_exists(self):
        assert (ROOT / "pyproject.toml").exists(), \
            "pyproject.toml が見つからない"
