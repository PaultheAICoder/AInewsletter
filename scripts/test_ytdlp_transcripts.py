#!/usr/bin/env python3
"""
yt-dlp YouTube Transcript Test Script

Tests yt-dlp's ability to download YouTube subtitles as a replacement
for the blocked youtube-transcript-api.

Usage:
    python scripts/test_ytdlp_transcripts.py [--video-id VIDEO_ID]
"""

import argparse
import sys
import time
import tempfile
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

from src.youtube.subtitle_parser import parse_subtitle_file, parse_vtt, ParsedSubtitle


@dataclass
class TranscriptResult:
    """Result of a transcript fetch attempt."""
    video_id: str
    success: bool
    transcript_text: str = ""
    word_count: int = 0
    language: str = ""
    is_auto_generated: bool = False
    error_message: str = ""
    fetch_time_seconds: float = 0.0


class YtdlpTranscriptFetcher:
    """Fetches YouTube transcripts using yt-dlp."""

    def __init__(self, prefer_languages: List[str] = None):
        """
        Initialize the fetcher.

        Args:
            prefer_languages: List of language codes to prefer, in order
        """
        self.prefer_languages = prefer_languages or ['en', 'en-US', 'en-GB', 'en-AU']

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

        # Create temp directory for subtitle files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, '%(id)s.%(ext)s')

            # yt-dlp options for subtitle extraction only
            # IMPORTANT: Only request specific languages to avoid 429 errors
            # when YouTube rate-limits requests for additional languages
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
                    is_auto = '.en-orig' not in best_file.name and '.en.' not in best_file.name

                    # Extract language from filename
                    # Format: video_id.lang.vtt or video_id.lang-orig.vtt
                    lang_match = best_file.stem.replace(video_id, '').strip('.')
                    language = lang_match.split('-')[0] if lang_match else 'unknown'

                    return TranscriptResult(
                        video_id=video_id,
                        success=True,
                        transcript_text=parsed.text,
                        word_count=parsed.word_count,
                        language=language,
                        is_auto_generated=is_auto,
                        fetch_time_seconds=time.time() - start_time
                    )

            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)

                # Check if we got subtitle files despite the error
                # (yt-dlp may error on some languages but succeed on others)
                subtitle_files = list(Path(tmpdir).glob(f"{video_id}*.vtt"))
                if subtitle_files:
                    # We got some subtitles, parse them
                    best_file = self._select_best_subtitle(subtitle_files)
                    parsed = parse_subtitle_file(str(best_file))
                    lang_match = best_file.stem.replace(video_id, '').strip('.')
                    language = lang_match.split('-')[0] if lang_match else 'unknown'

                    return TranscriptResult(
                        video_id=video_id,
                        success=True,
                        transcript_text=parsed.text,
                        word_count=parsed.word_count,
                        language=language,
                        is_auto_generated='-orig' in best_file.name,
                        fetch_time_seconds=time.time() - start_time
                    )

                if 'HTTP Error 429' in error_msg:
                    error_msg = "Rate limited by YouTube (HTTP 429)"
                elif 'Video unavailable' in error_msg:
                    error_msg = "Video unavailable or private"

                return TranscriptResult(
                    video_id=video_id,
                    success=False,
                    error_message=error_msg,
                    fetch_time_seconds=time.time() - start_time
                )

            except Exception as e:
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
        # Score each file
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

        # Sort by score descending
        scored_files.sort(key=lambda x: x[0], reverse=True)

        return scored_files[0][1]


def test_single_video(fetcher: YtdlpTranscriptFetcher, video_id: str) -> TranscriptResult:
    """Test fetching transcript for a single video."""
    print(f"\n{'='*60}")
    print(f"Testing video: {video_id}")
    print(f"URL: https://www.youtube.com/watch?v={video_id}")
    print(f"{'='*60}")

    result = fetcher.fetch_transcript(video_id)

    if result.success:
        print(f"SUCCESS!")
        print(f"  Language: {result.language}")
        print(f"  Auto-generated: {result.is_auto_generated}")
        print(f"  Word count: {result.word_count}")
        print(f"  Fetch time: {result.fetch_time_seconds:.2f}s")
        print(f"  Preview (first 200 chars):")
        print(f"    {result.transcript_text[:200]}...")
    else:
        print(f"FAILED!")
        print(f"  Error: {result.error_message}")
        print(f"  Fetch time: {result.fetch_time_seconds:.2f}s")

    return result


def main():
    """Main test function."""
    parser = argparse.ArgumentParser(description='Test yt-dlp transcript fetching')
    parser.add_argument('--video-id', '-v', type=str, help='Test specific video ID')
    parser.add_argument('--all', '-a', action='store_true', help='Test all sample videos')

    args = parser.parse_args()

    # Sample video IDs from the database (mix of channels)
    sample_videos = [
        # Recent videos from YouTube feeds
        ('3kgx0YxCriM', 'Indy Dev Dan - Claude Opus 4.5'),
        ('7SrkvlgIKFs', 'All About AI - Claude Opus 4.5 Video Automation'),
        ('VSKIS5D-gQg', 'The AI Advantage - Notion AI'),
        ('8uGqOAYmPfw', 'Matt Wolfe - OpenAI Code Red'),
        # Known good test cases
        ('dQw4w9WgXcQ', 'Rick Astley - Never Gonna Give You Up (popular, has captions)'),
    ]

    fetcher = YtdlpTranscriptFetcher()

    if args.video_id:
        # Test single video
        result = test_single_video(fetcher, args.video_id)
        sys.exit(0 if result.success else 1)

    # Test multiple videos
    videos_to_test = sample_videos if args.all else sample_videos[:3]

    print("\n" + "="*60)
    print("yt-dlp YouTube Transcript Test")
    print("="*60)
    print(f"Testing {len(videos_to_test)} videos...")

    results = []
    for video_id, description in videos_to_test:
        print(f"\n[{description}]")
        result = test_single_video(fetcher, video_id)
        results.append((description, result))

        # Small delay between requests to be respectful
        time.sleep(2)

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    successful = sum(1 for _, r in results if r.success)
    failed = len(results) - successful

    print(f"Total: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Success rate: {successful/len(results)*100:.1f}%")

    print("\nDetailed Results:")
    for desc, result in results:
        status = "OK" if result.success else "FAIL"
        if result.success:
            print(f"  [{status}] {desc}: {result.word_count} words, {result.fetch_time_seconds:.2f}s")
        else:
            print(f"  [{status}] {desc}: {result.error_message}")

    # Exit with error if any failed
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
