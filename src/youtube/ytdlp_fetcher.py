"""
yt-dlp YouTube Transcript Fetcher

Downloads YouTube transcripts using yt-dlp as a replacement for
youtube-transcript-api, which was being blocked by YouTube.

Key features:
- More resilient to YouTube blocking
- Downloads auto-generated or manual captions
- Parses VTT subtitles to plain text
"""

import logging
import tempfile
import time
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    import yt_dlp
except ImportError:
    raise ImportError("yt-dlp not installed. Run: pip install yt-dlp")

from .subtitle_parser import parse_subtitle_file

logger = logging.getLogger(__name__)


@dataclass
class TranscriptResult:
    """Result of a transcript fetch attempt."""
    video_id: str
    success: bool
    transcript_text: str = ""
    word_count: int = 0
    language: str = ""
    is_generated: bool = False
    error_message: str = ""
    fetch_time_seconds: float = 0.0


class YtdlpTranscriptFetcher:
    """
    Fetches YouTube transcripts using yt-dlp.

    More resilient to YouTube blocking than youtube-transcript-api.
    """

    def __init__(self, prefer_languages: List[str] = None):
        """
        Initialize the fetcher.

        Args:
            prefer_languages: List of language codes to prefer, in order
        """
        self.prefer_languages = prefer_languages or ['en', 'en-US', 'en-GB', 'en-AU']
        logger.info(f"YtdlpTranscriptFetcher initialized with languages: {self.prefer_languages}")

    def fetch_transcript(self, video_id: str) -> TranscriptResult:
        """
        Fetch transcript for a YouTube video.

        Args:
            video_id: YouTube video ID (11 characters)

        Returns:
            TranscriptResult with transcript text or error
        """
        start_time = time.time()
        url = f"https://www.youtube.com/watch?v={video_id}"

        logger.debug(f"Fetching transcript for video: {video_id}")

        # Create temp directory for subtitle files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, '%(id)s.%(ext)s')

            # yt-dlp options for subtitle extraction only
            # IMPORTANT: Only request specific languages to avoid 429 errors
            ydl_opts = {
                'writeautomaticsub': True,  # Download auto-generated subs
                'writesubtitles': True,     # Download manual subs (preferred)
                'subtitleslangs': self.prefer_languages,  # Only English variants
                'subtitlesformat': 'vtt',   # Use VTT format
                'skip_download': True,      # Don't download video
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'ignoreerrors': True,       # Continue on errors
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Extract info and download subtitles
                    info = ydl.extract_info(url, download=True)

                    if not info:
                        return TranscriptResult(
                            video_id=video_id,
                            success=False,
                            error_message="Failed to extract video info",
                            fetch_time_seconds=time.time() - start_time
                        )

                    # Check for downloaded subtitle files
                    subtitle_files = list(Path(tmpdir).glob(f"{video_id}*.vtt"))

                    if not subtitle_files:
                        # Check for other formats
                        subtitle_files = list(Path(tmpdir).glob(f"{video_id}*.*"))
                        subtitle_files = [f for f in subtitle_files if f.suffix in ['.vtt', '.srt', '.ttml']]

                    if not subtitle_files:
                        return TranscriptResult(
                            video_id=video_id,
                            success=False,
                            error_message="No subtitles available for this video",
                            fetch_time_seconds=time.time() - start_time
                        )

                    # Find best subtitle file (prefer English, prefer manual over auto)
                    best_file = self._select_best_subtitle(subtitle_files)

                    # Parse the subtitle file
                    parsed = parse_subtitle_file(str(best_file))

                    # Detect if auto-generated from filename
                    is_auto = '-orig' in best_file.name

                    # Extract language from filename
                    lang_match = best_file.stem.replace(video_id, '').strip('.')
                    language = lang_match.split('-')[0] if lang_match else 'en'

                    logger.info(
                        f"Successfully fetched transcript for {video_id}: "
                        f"{parsed.word_count} words, lang={language}, auto={is_auto}"
                    )

                    return TranscriptResult(
                        video_id=video_id,
                        success=True,
                        transcript_text=parsed.text,
                        word_count=parsed.word_count,
                        language=language,
                        is_generated=is_auto,
                        fetch_time_seconds=time.time() - start_time
                    )

            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)

                # Check if we got subtitle files despite the error
                subtitle_files = list(Path(tmpdir).glob(f"{video_id}*.vtt"))
                if subtitle_files:
                    best_file = self._select_best_subtitle(subtitle_files)
                    parsed = parse_subtitle_file(str(best_file))
                    lang_match = best_file.stem.replace(video_id, '').strip('.')
                    language = lang_match.split('-')[0] if lang_match else 'en'

                    logger.info(
                        f"Fetched transcript for {video_id} despite error: {parsed.word_count} words"
                    )

                    return TranscriptResult(
                        video_id=video_id,
                        success=True,
                        transcript_text=parsed.text,
                        word_count=parsed.word_count,
                        language=language,
                        is_generated='-orig' in best_file.name,
                        fetch_time_seconds=time.time() - start_time
                    )

                if 'HTTP Error 429' in error_msg:
                    error_msg = "Rate limited by YouTube (HTTP 429)"
                elif 'Video unavailable' in error_msg:
                    error_msg = "Video unavailable or private"

                logger.error(f"Failed to fetch transcript for {video_id}: {error_msg}")

                return TranscriptResult(
                    video_id=video_id,
                    success=False,
                    error_message=error_msg,
                    fetch_time_seconds=time.time() - start_time
                )

            except Exception as e:
                logger.error(f"Unexpected error fetching transcript for {video_id}: {e}")
                return TranscriptResult(
                    video_id=video_id,
                    success=False,
                    error_message=f"Unexpected error: {str(e)}",
                    fetch_time_seconds=time.time() - start_time
                )

    def _select_best_subtitle(self, subtitle_files: List[Path]) -> Path:
        """
        Select the best subtitle file from available options.

        Preference order:
        1. English manual subtitles
        2. English auto-generated subtitles
        3. Any other language

        Args:
            subtitle_files: List of subtitle file paths

        Returns:
            Best subtitle file path
        """
        scored_files = []
        for f in subtitle_files:
            score = 0
            name = f.name.lower()

            # Prefer English
            if '.en.' in name or '.en-' in name:
                score += 100

            # Prefer manual over auto-generated (orig = auto-generated in yt-dlp naming)
            if '-orig' not in name:
                score += 50

            # Prefer VTT format
            if f.suffix == '.vtt':
                score += 10

            scored_files.append((score, f))

        scored_files.sort(key=lambda x: x[0], reverse=True)
        return scored_files[0][1]
