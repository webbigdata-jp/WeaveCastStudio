"""
check_env.py — WeaveCastStudio Windows 環境チェックスクリプト (M4 用)

実行方法:
    python check_env.py

チェック内容:
  1. ディレクトリ構成が揃っているか
  2. .env ファイルが存在するか
  3. .env に GOOGLE_API_KEY / LANGUAGE キーが存在するか（値は確認しない）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
PASS = "  ✅"
FAIL = "  ❌"

errors: list[str] = []


def check(condition: bool, ok_msg: str, err_msg: str) -> None:
    if condition:
        print(f"{PASS} {ok_msg}")
    else:
        print(f"{FAIL} {err_msg}")
        errors.append(err_msg)


# ── 1. ディレクトリ構成 ───────────────────────────────────────────────────────
print("\n[1] ディレクトリ構成")

required_dirs = [
    "shared",
    "compe_M1",
    "compe_M3",
    "compe_M4",
    "IaC",
    "gcp",
    "docs",
]
for d in required_dirs:
    check((ROOT / d).is_dir(), f"{d}/ が存在する", f"{d}/ が見つからない")

required_files = [
    "content_index.py",
    "pull_from_gcs.ps1",
    "pyproject.toml",
    ".env.sample",
    "compe_M4/gemini_live_client.py",
    "compe_M4/media_window.py",
    "compe_M4/breaking_news_server.py",
    "compe_M4/overlay/ticker.html",
]
for f in required_files:
    check((ROOT / f).exists(), f"{f} が存在する", f"{f} が見つからない")

# ── 2. .env ファイル ──────────────────────────────────────────────────────────
print("\n[2] .env ファイル")

env_path = ROOT / ".env"
env_exists = env_path.exists()
check(env_exists, ".env が存在する", ".env が見つからない → .env.sample をコピーして作成してください")

# ── 3. .env のキー確認 ────────────────────────────────────────────────────────
print("\n[3] .env キー確認")

if env_exists:
    env_keys: set[str] = set()
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                env_keys.add(key)

    required_keys = ["GOOGLE_API_KEY", "LANGUAGE"]
    for key in required_keys:
        check(key in env_keys, f"{key} キーが存在する", f"{key} キーが .env に見つからない")
else:
    print(f"{FAIL} .env が存在しないためキー確認をスキップ")
    errors.append(".env が存在しないためキー確認をスキップ")

# ── 結果サマリー ──────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
if not errors:
    print("✅ すべてのチェックが通りました。M4 を起動できます。")
    print("   cd compe_M4 && python gemini_live_client.py")
    sys.exit(0)
else:
    print(f"❌ {len(errors)} 件の問題が見つかりました:")
    for e in errors:
        print(f"   - {e}")
    sys.exit(1)
