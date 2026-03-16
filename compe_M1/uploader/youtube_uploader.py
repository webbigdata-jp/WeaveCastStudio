"""
Phase 8: YouTube upload.
Uploads a generated video to YouTube using the YouTube Data API v3.

Prerequisites:
  1. Enable the YouTube Data API v3 in Google Cloud Console.
  2. Create an OAuth2 Client ID (Desktop application) and download the JSON.
  3. On first run, authenticate via browser -> config/youtube_token.json is
     created automatically.

Quota: 1 video upload = 1,600 units / default daily quota = 10,000 units.
"""

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"
CATEGORY_NEWS_POLITICS = "25"


def _get_credentials(
    client_secrets_path: Path,
    token_path: Path,
) -> Credentials:
    """
    Handle the OAuth2 authentication flow and return Credentials.
    Loads from token.json if it exists; otherwise opens a browser for auth.
    """
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired token ...")
            creds.refresh(Request())
        else:
            logger.info("Starting OAuth2 flow (browser will open) ...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(str(token_path), "w") as f:
            f.write(creds.to_json())
        logger.info(f"Token saved to {token_path}")

    return creds


def upload_to_youtube(
    video_path: Path,
    briefing_data: dict,
    client_secrets_path: Path,
    token_path: Path,
    privacy_status: str = "unlisted",  # Use "unlisted" for testing
) -> str:
    """
    Upload a video to YouTube and return its video_id.

    Args:
        video_path: Path to the MP4 file to upload.
        briefing_data: Structured data from Phase 3 (used to build metadata).
        client_secrets_path: Path to the OAuth2 client secrets JSON.
        token_path: Token save path (auto-created on first run).
        privacy_status: "public" | "unlisted" | "private".

    Returns:
        YouTube video ID.
    """
    creds = _get_credentials(client_secrets_path, token_path)
    youtube = build(
        YOUTUBE_API_SERVICE,
        YOUTUBE_API_VERSION,
        credentials=creds,
        cache_discovery=False,
    )

    sections = briefing_data.get("briefing_sections", [])
    countries_covered = [s["country"] for s in sections]
    date_str = briefing_data.get("generated_at", "")[:10]
    analysis = briefing_data.get("analysis", {})

    # Summary block
    overall_summary = analysis.get("summary", "")
    consensus = analysis.get("consensus_points", [])
    divergence = analysis.get("divergence_points", [])

    summary_block = ""
    if overall_summary:
        summary_block += f"📋 SUMMARY\n{overall_summary}\n\n"
    if consensus:
        summary_block += "✅ Points of Consensus:\n"
        summary_block += "\n".join(f"  • {p}" for p in consensus) + "\n\n"
    if divergence:
        summary_block += "⚡ Points of Divergence:\n"
        summary_block += "\n".join(f"  • {p}" for p in divergence) + "\n\n"

    # Per-country stance list
    stance_lines = []
    for s in sections:
        stance_emoji = {
            "supportive": "🟦", "opposed": "🟥",
            "neutral": "⬜", "cautious": "🟨",
        }.get(s.get("stance", "neutral"), "⬜")
        stance_lines.append(
            f"{stance_emoji} {s['country']}: {s.get('position', '')}"
        )
    stance_block = (
        "🌍 COUNTRY POSITIONS\n" + "\n".join(stance_lines) + "\n\n"
        if stance_lines else ""
    )

    # Source URL list
    url_lines = []
    seen_urls = set()
    for s in sections:
        for url in s.get("source_urls", []):
            if url and url not in seen_urls:
                url_lines.append(f"  • {s['country']}: {url}")
                seen_urls.add(url)
        # Backward-compat: also check legacy source_url field
        single_url = s.get("source_url")
        if single_url and single_url not in seen_urls:
            url_lines.append(f"  • {s['country']}: {single_url}")
            seen_urls.add(single_url)
    sources_block = (
        "🔗 SOURCES\n" + "\n".join(url_lines) + "\n\n"
        if url_lines else ""
    )

    title = f"[StoryWire] Global Response: {briefing_data['topic']} — {date_str}"

    description = (
        f"AI-generated diplomatic briefing on {briefing_data['topic']}.\n\n"
        f"{summary_block}"
        f"{stance_block}"
        f"{sources_block}"
        f"Countries covered: {', '.join(countries_covered)}\n\n"
        f"Generated by StoryWire — an AI agent that monitors global crisis information, "
        f"verifies sources, and produces multimedia briefings for journalists and analysts.\n\n"
        f"⚠️ This content contains AI-generated synthetic media (images, narration). "
        f"Always verify information with official sources.\n\n"
        f"#StoryWire #OSINT #CrisisIntelligence #GeminiLiveAgentChallenge"
    )
    tags = [
        "StoryWire", "AI News", "OSINT", "Crisis Intelligence",
        "Gemini", "GeminiLiveAgentChallenge",
        briefing_data.get("topic", ""),
    ]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_NEWS_POLITICS,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # AI-generated content flag
        },
    }

    logger.info(f"Starting YouTube upload: {video_path.name} ({privacy_status})")
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
    )

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info(f"  Upload progress: {pct}%")

        video_id = response["id"]
        url = f"https://youtube.com/watch?v={video_id}"
        logger.info(f"Upload complete: {url}")
        return video_id

    except HttpError as e:
        logger.error(f"YouTube API error: {e}")
        raise
