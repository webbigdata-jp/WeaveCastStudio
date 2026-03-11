"""
content_index.py

GeminiLiveAgent/ 共有モジュール。
M1・M3が生成した動画・スクリーンショットを一元管理し、
M4がコンテンツを検索・再生できるようにする。

ファイルパス: GeminiLiveAgent/content_index.json

設計:
  - パスは登録時に相対パス（content_index.jsonからの相対）と
    絶対パスの両方を保持する
  - 動画エントリには duration_seconds を含む
  - is_breaking フラグでクローラーからの緊急割り込みに対応
  - スレッドセーフな書き込み（threading.Lock）

使い方:
  # M1/M3から登録
  mgr = ContentIndexManager()
  mgr.add_entry(ContentEntry(
      id="m1_20260310_172523",
      module="M1",
      type="video",
      title="イラン紛争：各国政府の公式見解",
      ...
  ))

  # M4から検索
  mgr = ContentIndexManager()
  entries = mgr.get_by_tags(["iran", "diplomatic"])
  breaking = mgr.get_breaking()
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

# content_index.json のデフォルトパス（このファイルと同じディレクトリ）
_DEFAULT_INDEX_PATH = Path(__file__).parent / "content_index.json"


# ──────────────────────────────────────────
# エントリのデータ構造
# ──────────────────────────────────────────

def make_entry(
    id: str,
    module: str,                        # "M1" | "M3"
    content_type: str,                  # "video" | "screenshot" | "image"
    title: str,
    topic_tags: list[str],
    created_at: str | None = None,
    source_id: str | None = None,       # M3のsources.yaml id
    source_name: str | None = None,
    importance_score: float | None = None,
    is_breaking: bool = False,
    video_path: str | Path | None = None,
    screenshot_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    duration_seconds: float | None = None,
    index_path: Path | None = None,     # 相対パス計算の基準
) -> dict[str, Any]:
    """
    ContentIndex エントリ dict を生成する。

    video_path / screenshot_path / manifest_path は
    相対パス（index_path からの相対）と絶対パスの両方を保持する。

    Args:
        id: 一意なID（例: "m1_20260310_172523"）
        module: "M1" または "M3"
        content_type: "video" | "screenshot" | "image"
        title: コンテンツのタイトル
        topic_tags: 検索用タグリスト
        created_at: ISO8601文字列。None の場合は現在時刻
        source_id: M3 sources.yaml の id（M1の場合は None）
        source_name: ソース表示名
        importance_score: 重要度スコア（0.0-10.0）
        is_breaking: 緊急ニュースフラグ
        video_path: 動画ファイルのパス
        screenshot_path: スクリーンショットのパス
        manifest_path: manifest.json / briefing_plan.json のパス
        duration_seconds: 動画の長さ（秒）。None の場合は自動検出を試みる
        index_path: content_index.json のパス（相対パス計算用）
    Returns:
        dict: ContentIndex エントリ
    """
    base = index_path or _DEFAULT_INDEX_PATH

    def _resolve(p: str | Path | None) -> tuple[str | None, str | None]:
        """パスを (相対パス文字列, 絶対パス文字列) に解決する"""
        if p is None:
            return None, None
        abs_p = Path(p).resolve()
        try:
            rel_p = abs_p.relative_to(base.parent.resolve())
            rel_str = str(rel_p).replace("\\", "/")  # Windows対応
        except ValueError:
            # base の外にある場合は相対パス計算不可 → 絶対パスのみ
            rel_str = None
        return rel_str, str(abs_p).replace("\\", "/")

    video_rel, video_abs = _resolve(video_path)
    shot_rel, shot_abs = _resolve(screenshot_path)
    manifest_rel, manifest_abs = _resolve(manifest_path)

    # 動画の長さを自動検出（ffprobe が使える場合）
    dur = duration_seconds
    if dur is None and video_abs:
        dur = _probe_duration(video_abs)

    return {
        "id": id,
        "module": module,
        "type": content_type,
        "title": title,
        "topic_tags": topic_tags,
        "source_id": source_id,
        "source_name": source_name,
        "importance_score": importance_score,
        "is_breaking": is_breaking,
        # 動画パス
        "video_path": video_rel,
        "video_path_abs": video_abs,
        "duration_seconds": dur,
        # スクリーンショットパス
        "screenshot_path": shot_rel,
        "screenshot_path_abs": shot_abs,
        # マニフェストパス
        "manifest_path": manifest_rel,
        "manifest_path_abs": manifest_abs,
        # 管理フィールド
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "used_in_broadcast": False,
    }


def _probe_duration(video_path: str) -> float | None:
    """
    ffprobe で動画の長さ（秒）を取得する。
    ffprobe が存在しない場合や失敗した場合は None を返す。
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


# ──────────────────────────────────────────
# ContentIndexManager
# ──────────────────────────────────────────

class ContentIndexManager:
    """
    content_index.json の読み書き・検索を担うクラス。
    スレッドセーフ（Lock使用）。

    Args:
        index_path: content_index.json のパス。
                    None の場合は GeminiLiveAgent/content_index.json
    """

    def __init__(self, index_path: str | Path | None = None):
        self._path = Path(index_path) if index_path else _DEFAULT_INDEX_PATH
        self._lock = Lock()
        self._ensure_file()

    # ──────────────────────────────────────
    # 書き込み系
    # ──────────────────────────────────────

    def add_entry(self, entry: dict[str, Any]) -> None:
        """
        エントリを追加する。同一 id が存在する場合は上書きする。

        Args:
            entry: make_entry() で生成した dict
        """
        with self._lock:
            data = self._load()
            # 同一IDは上書き
            data["entries"] = [
                e for e in data["entries"] if e["id"] != entry["id"]
            ]
            data["entries"].append(entry)
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
        logger.info(f"[ContentIndex] Added: {entry['id']} ({entry['type']})")

    def remove_entry(self, entry_id: str) -> bool:
        """
        指定 id のエントリを削除する。

        Args:
            entry_id: 削除対象のエントリ id
        Returns:
            bool: 対象エントリが見つかり削除できた場合 True
        """
        with self._lock:
            data = self._load()
            before = len(data["entries"])
            data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
            if len(data["entries"]) == before:
                logger.warning(f"[ContentIndex] remove_entry: id not found: {entry_id}")
                return False
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
        logger.info(f"[ContentIndex] Removed: {entry_id}")
        return True

    def set_breaking(self, entry_id: str, is_breaking: bool = True) -> bool:
        """
        is_breaking フラグを更新する。
        クローラーが緊急ニュースを検知したときに呼び出す。

        Returns:
            bool: 対象エントリが見つかった場合 True
        """
        with self._lock:
            data = self._load()
            for entry in data["entries"]:
                if entry["id"] == entry_id:
                    entry["is_breaking"] = is_breaking
                    data["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self._save(data)
                    logger.info(
                        f"[ContentIndex] set_breaking={is_breaking}: {entry_id}"
                    )
                    return True
        logger.warning(f"[ContentIndex] set_breaking: id not found: {entry_id}")
        return False

    def mark_used(self, entry_id: str) -> bool:
        """
        放送で使用済みフラグを立てる。M4が再生後に呼び出す。

        Returns:
            bool: 対象エントリが見つかった場合 True
        """
        with self._lock:
            data = self._load()
            for entry in data["entries"]:
                if entry["id"] == entry_id:
                    entry["used_in_broadcast"] = True
                    data["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self._save(data)
                    return True
        return False

    # ──────────────────────────────────────
    # 読み取り系
    # ──────────────────────────────────────

    def get_all(self, sort_by_importance: bool = True) -> list[dict]:
        """
        全エントリを返す。

        Args:
            sort_by_importance: True の場合 importance_score 降順でソート
        Returns:
            list[dict]: エントリのリスト
        """
        data = self._load()
        entries = data.get("entries", [])
        if sort_by_importance:
            entries = sorted(
                entries,
                key=lambda e: (
                    e.get("is_breaking", False),      # BREAKING優先
                    e.get("importance_score") or 0.0,
                ),
                reverse=True,
            )
        return entries

    def get_by_module(self, module: str) -> list[dict]:
        """
        モジュール（"M1" or "M3"）でフィルタして返す。
        """
        return [e for e in self.get_all() if e.get("module") == module]

    def get_by_tags(self, tags: list[str], match_any: bool = True) -> list[dict]:
        """
        topic_tags でフィルタして返す。

        Args:
            tags: 検索タグリスト
            match_any: True=いずれか1つ一致 / False=すべて一致
        Returns:
            importance_score 降順のエントリリスト
        """
        tags_lower = [t.lower() for t in tags]
        result = []
        for entry in self.get_all():
            entry_tags = [t.lower() for t in entry.get("topic_tags", [])]
            if match_any:
                if any(t in entry_tags for t in tags_lower):
                    result.append(entry)
            else:
                if all(t in entry_tags for t in tags_lower):
                    result.append(entry)
        return result

    def get_by_type(self, content_type: str) -> list[dict]:
        """
        type（"video" | "screenshot" | "image"）でフィルタして返す。
        """
        return [e for e in self.get_all() if e.get("type") == content_type]

    def get_breaking(self) -> list[dict]:
        """
        is_breaking=True のエントリを返す。M4の緊急割り込み検知用。
        """
        return [e for e in self.get_all() if e.get("is_breaking")]

    def get_today(self) -> list[dict]:
        """
        本日（UTC）に生成されたエントリを返す。M4の起動時サマリ用。
        """
        today = datetime.now(timezone.utc).date().isoformat()
        return [
            e for e in self.get_all()
            if e.get("created_at", "").startswith(today)
        ]

    def get_stats(self) -> dict:
        """
        インデックスのサマリを返す（デバッグ・モニタリング用）。
        """
        entries = self.get_all(sort_by_importance=False)
        return {
            "total": len(entries),
            "by_module": {
                "M1": len([e for e in entries if e.get("module") == "M1"]),
                "M3": len([e for e in entries if e.get("module") == "M3"]),
            },
            "by_type": {
                "video": len([e for e in entries if e.get("type") == "video"]),
                "screenshot": len([e for e in entries if e.get("type") == "screenshot"]),
                "image": len([e for e in entries if e.get("type") == "image"]),
            },
            "breaking": len(self.get_breaking()),
            "last_updated": self._load().get("last_updated"),
        }

    # ──────────────────────────────────────
    # 内部処理
    # ──────────────────────────────────────

    def _ensure_file(self) -> None:
        """content_index.json が存在しない場合は空ファイルを作成する"""
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._save({"last_updated": None, "entries": []})
            logger.info(f"[ContentIndex] Created: {self._path}")

    def _load(self) -> dict:
        """JSON を読み込んで返す"""
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        """JSON に書き込む（アトミックな上書き）"""
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self._path)  # アトミックリネーム


