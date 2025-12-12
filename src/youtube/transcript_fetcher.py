"""
YouTube Transcript Fetcher

Uses youtube-transcript-api to fetch transcripts from YouTube videos.
Prefers English transcripts but falls back to native language if unavailable.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

logger = logging.getLogger(__name__)


@dataclass
class TranscriptResult:
    """Result of a transcript fetch operation."""
    video_id: str
    success: bool
    transcript_text: Optional[str] = None
    word_count: int = 0
    language: Optional[str] = None
    is_generated: bool = False
    error_message: Optional[str] = None


class YouTubeTranscriptFetcher:
    """Fetches transcripts from YouTube videos using youtube-transcript-api."""

    def __init__(self):
        self.api = YouTubeTranscriptApi()
        self.formatter = TextFormatter()
        # Preferred languages in order of priority
        self.preferred_languages = ['en', 'en-US', 'en-GB']

    def extract_video_id(self, url: str) -> Optional[str]:
        """
        Extract video ID from various YouTube URL formats.

        Supports:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - Just the VIDEO_ID itself
        """
        import re

        # Already just an ID (11 characters, alphanumeric with - and _)
        if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
            return url

        # Standard watch URL
        match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        # Short URL (youtu.be)
        match = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        # Embed URL
        match = re.search(r'embed/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        return None

    def fetch_transcript(self, video_id: str) -> TranscriptResult:
        """
        Fetch transcript for a YouTube video.

        Tries English first, then falls back to any available language.

        Args:
            video_id: YouTube video ID (11 characters)

        Returns:
            TranscriptResult with transcript text or error information
        """
        try:
            # First, try to get English transcript
            try:
                transcript = self.api.fetch(video_id, languages=self.preferred_languages)
                language = transcript.language_code
                is_generated = transcript.is_generated
            except Exception:
                # Fall back to any available transcript
                logger.info(f"English transcript not available for {video_id}, trying native language")
                transcript = self.api.fetch(video_id)
                language = transcript.language_code
                is_generated = transcript.is_generated

            # Format transcript as plain text
            transcript_text = self.formatter.format_transcript(transcript)

            # Calculate word count
            word_count = len(transcript_text.split())

            logger.info(
                f"Successfully fetched transcript for {video_id}: "
                f"{word_count} words, language={language}, generated={is_generated}"
            )

            return TranscriptResult(
                video_id=video_id,
                success=True,
                transcript_text=transcript_text,
                word_count=word_count,
                language=language,
                is_generated=is_generated
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to fetch transcript for {video_id}: {error_msg}")

            return TranscriptResult(
                video_id=video_id,
                success=False,
                error_message=error_msg
            )

    def fetch_transcript_from_url(self, url: str) -> TranscriptResult:
        """
        Fetch transcript from a YouTube URL.

        Args:
            url: Full YouTube URL or video ID

        Returns:
            TranscriptResult with transcript text or error information
        """
        video_id = self.extract_video_id(url)

        if not video_id:
            return TranscriptResult(
                video_id=url,
                success=False,
                error_message=f"Could not extract video ID from URL: {url}"
            )

        return self.fetch_transcript(video_id)
