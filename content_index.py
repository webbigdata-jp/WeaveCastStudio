"""
content_index.py — Shared content registry for WeaveCastStudio.

M1 and M3 register produced videos and screenshots here so that
M4 can search and play them during a live broadcast.

File path: WeaveCastStudio/content_index.json

Design:
  - Paths are stored as both relative (from content_index.json) and absolute.
  - Video entries include duration_seconds.
  - is_breaking flag supports urgent breaking-news interrupts from the crawler.
  - Thread-safe writes via threading.Lock.

Usage:
    # Register from M1 / M3
    mgr = ContentIndexManager()
    mgr.add_entry(make_entry(
        id="m1_20260310_172523",
        module="M1",
        content_type="video",
        title="Iran Conflict: Official Government Positions",
        ...
    ))

    # Search from M4
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

# Default path for content_index.json (same directory as this file)
_DEFAULT_INDEX_PATH = Path(__file__).parent / "content_index.json"


# ──────────────────────────────────────────────────────────────────────────────
# Entry data structure
# ──────────────────────────────────────────────────────────────────────────────

def make_entry(
    id: str,
    module: str,                        # "M1" | "M3"
    content_type: str,                  # "video" | "screenshot" | "image"
    title: str,
    topic_tags: list[str],
    description: str | None = None,
    created_at: str | None = None,
    source_id: str | None = None,       # M3 sources.yaml id
    source_name: str | None = None,
    importance_score: float | None = None,
    is_breaking: bool = False,
    video_path: str | Path | None = None,
    screenshot_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    duration_seconds: float | None = None,
    index_path: Path | None = None,     # Base for relative path calculation
) -> dict[str, Any]:
    """
    Build a ContentIndex entry dict.

    video_path / screenshot_path / manifest_path are stored as both
    relative (from index_path) and absolute paths.

    Args:
        id: Unique identifier (e.g. "m1_20260310_172523").
        module: "M1" or "M3".
        content_type: "video" | "screenshot" | "image".
        title: Human-readable title.
        topic_tags: Search tags.
        description: Summary text for M4 context awareness.
        created_at: ISO 8601 string; defaults to now (UTC).
        source_id: M3 sources.yaml id (None for M1).
        source_name: Display name of the source.
        importance_score: Relevance score (0.0–10.0).
        is_breaking: Breaking news flag.
        video_path: Path to the video file.
        screenshot_path: Path to the screenshot.
        manifest_path: Path to manifest.json / briefing_plan.json.
        duration_seconds: Video length; auto-detected via ffprobe if None.
        index_path: Path to content_index.json (for relative path computation).

    Returns:
        dict: ContentIndex entry.
    """
    base = index_path or _DEFAULT_INDEX_PATH

    def _resolve(p: str | Path | None) -> tuple[str | None, str | None]:
        """Resolve a path to (relative_str, absolute_str)."""
        if p is None:
            return None, None
        abs_p = Path(p).resolve()
        try:
            rel_p = abs_p.relative_to(base.parent.resolve())
            rel_str = str(rel_p).replace("\\", "/")  # Windows compatibility
        except ValueError:
            # Path is outside base directory — absolute only
            rel_str = None
        return rel_str, str(abs_p).replace("\\", "/")

    video_rel, video_abs = _resolve(video_path)
    shot_rel, shot_abs = _resolve(screenshot_path)
    manifest_rel, manifest_abs = _resolve(manifest_path)

    # Auto-detect video duration via ffprobe if not provided
    dur = duration_seconds
    if dur is None and video_abs:
        dur = _probe_duration(video_abs)

    return {
        "id": id,
        "module": module,
        "type": content_type,
        "title": title,
        "description": description,
        "topic_tags": topic_tags,
        "source_id": source_id,
        "source_name": source_name,
        "importance_score": importance_score,
        "is_breaking": is_breaking,
        # Video
        "video_path": video_rel,
        "video_path_abs": video_abs,
        "duration_seconds": dur,
        # Screenshot
        "screenshot_path": shot_rel,
        "screenshot_path_abs": shot_abs,
        # Manifest
        "manifest_path": manifest_rel,
        "manifest_path_abs": manifest_abs,
        # Metadata
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "used_in_broadcast": False,
    }


def _probe_duration(video_path: str) -> float | None:
    """
    Retrieve video duration in seconds via ffprobe.
    Returns None if ffprobe is unavailable or fails.
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


# ──────────────────────────────────────────────────────────────────────────────
# ContentIndexManager
# ──────────────────────────────────────────────────────────────────────────────

class ContentIndexManager:
    """
    Read/write/search interface for content_index.json.
    All write operations are thread-safe (Lock).

    Args:
        index_path: Path to content_index.json.
                    Defaults to WeaveCastStudio/content_index.json.
    """

    def __init__(self, index_path: str | Path | None = None):
        self._path = Path(index_path) if index_path else _DEFAULT_INDEX_PATH
        self._lock = Lock()
        self._ensure_file()

    # ── Write operations ──────────────────────────────────────────────────────

    def add_entry(self, entry: dict[str, Any]) -> None:
        """
        Add an entry. Overwrites any existing entry with the same id.

        Args:
            entry: Dict produced by make_entry().
        """
        with self._lock:
            data = self._load()
            data["entries"] = [e for e in data["entries"] if e["id"] != entry["id"]]
            data["entries"].append(entry)
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
        logger.info(f"[ContentIndex] Added: {entry['id']} ({entry['type']})")

    def remove_entry(self, entry_id: str) -> bool:
        """
        Remove the entry with the given id.

        Returns:
            True if the entry was found and removed.
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
        Update the is_breaking flag.
        Called by the crawler when a breaking news item is detected.

        Returns:
            True if the entry was found.
        """
        with self._lock:
            data = self._load()
            for entry in data["entries"]:
                if entry["id"] == entry_id:
                    entry["is_breaking"] = is_breaking
                    data["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self._save(data)
                    logger.info(
                        f"[ContentIndex] is_breaking={is_breaking} set for: {entry_id}"
                    )
                    return True
        logger.warning(f"[ContentIndex] set_breaking: id not found: {entry_id}")
        return False

    def mark_used(self, entry_id: str) -> bool:
        """
        Mark an entry as used in a broadcast. Called by M4 after playback.

        Returns:
            True if the entry was found.
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

    # ── Read operations ───────────────────────────────────────────────────────

    def get_all(self, sort_by_importance: bool = True) -> list[dict]:
        """
        Return all entries.

        Args:
            sort_by_importance: Sort descending by importance_score if True.
        """
        data = self._load()
        entries = data.get("entries", [])
        if sort_by_importance:
            entries = sorted(
                entries,
                key=lambda e: (
                    e.get("is_breaking", False),       # breaking items first
                    e.get("importance_score") or 0.0,
                ),
                reverse=True,
            )
        return entries

    def get_by_module(self, module: str) -> list[dict]:
        """Filter entries by module ("M1" or "M3")."""
        return [e for e in self.get_all() if e.get("module") == module]

    def get_by_tags(self, tags: list[str], match_any: bool = True) -> list[dict]:
        """
        Filter entries by topic_tags.

        Args:
            tags: Tags to search for.
            match_any: True = match any tag; False = match all tags.

        Returns:
            Entries sorted by importance_score descending.
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
        """Filter entries by type ("video" | "screenshot" | "image")."""
        return [e for e in self.get_all() if e.get("type") == content_type]

    def get_breaking(self) -> list[dict]:
        """Return entries with is_breaking=True. Used by M4 for urgent interrupts."""
        return [e for e in self.get_all() if e.get("is_breaking")]

    def get_today(self) -> list[dict]:
        """Return entries created today (UTC). Used by M4 for startup summary."""
        today = datetime.now(timezone.utc).date().isoformat()
        return [
            e for e in self.get_all()
            if e.get("created_at", "").startswith(today)
        ]

    def get_stats(self) -> dict:
        """Return index statistics (for debugging and monitoring)."""
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

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ensure_file(self) -> None:
        """Create an empty content_index.json if it does not exist."""
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._save({"last_updated": None, "entries": []})
            logger.info(f"[ContentIndex] File created: {self._path}")

    def _load(self) -> dict:
        """Load and return the JSON index."""
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        """Write JSON atomically via a temp file + rename."""
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self._path)  # atomic rename
