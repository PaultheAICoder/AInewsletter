"""
YouTube Feed Processor

Parses YouTube RSS feeds to detect new videos and filter by duration.
"""

import logging
import re
import time
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import feedparser

logger = logging.getLogger(__name__)

# Retry configuration for feed fetching
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
REQUEST_TIMEOUT = 30


@dataclass
class YouTubeVideo:
    """Represents a YouTube video from an RSS feed."""
    video_id: str
    title: str
    published_date: datetime
    channel_id: str
    channel_name: str
    description: Optional[str] = None
    duration_seconds: Optional[int] = None
    video_url: str = ""

    def __post_init__(self):
        if not self.video_url:
            self.video_url = f"https://www.youtube.com/watch?v={self.video_id}"


class YouTubeFeedProcessor:
    """Processes YouTube RSS feeds to find new videos."""

    # Minimum video duration in seconds (3 minutes)
    MIN_DURATION_SECONDS = 180

    def __init__(self, lookback_days: int = 5):
        """
        Initialize the feed processor.

        Args:
            lookback_days: Number of days to look back for new videos
        """
        self.lookback_days = lookback_days

    def is_youtube_feed(self, feed_url: str) -> bool:
        """Check if a feed URL is a YouTube RSS feed."""
        return 'youtube.com/feeds/videos.xml' in feed_url

    def extract_channel_id(self, feed_url: str) -> Optional[str]:
        """Extract channel ID from YouTube RSS feed URL."""
        match = re.search(r'channel_id=([a-zA-Z0-9_-]+)', feed_url)
        return match.group(1) if match else None

    def parse_feed(self, feed_url: str) -> List[YouTubeVideo]:
        """
        Parse a YouTube RSS feed and return video entries.
        Includes retry logic for transient failures.

        Args:
            feed_url: YouTube RSS feed URL

        Returns:
            List of YouTubeVideo objects
        """
        if not self.is_youtube_feed(feed_url):
            logger.warning(f"Not a YouTube feed URL: {feed_url}")
            return []

        channel_id = self.extract_channel_id(feed_url)
        if not channel_id:
            logger.error(f"Could not extract channel ID from: {feed_url}")
            return []

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Use requests to fetch with proper headers and timeout
                headers = {
                    'User-Agent': 'Mozilla/5.0 (compatible; PodcastDigest/1.0)',
                    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                }
                response = requests.get(feed_url, headers=headers, timeout=REQUEST_TIMEOUT)

                # Check for non-200 responses
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}: {response.reason}"
                    logger.warning(f"Feed fetch attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY_SECONDS * attempt)  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Feed fetch failed after {MAX_RETRIES} attempts for {feed_url}: {last_error}")
                        return []

                # Check if response looks like XML (not HTML error page)
                content_type = response.headers.get('content-type', '')
                content_start = response.text[:500].strip().lower()

                if 'html' in content_type or content_start.startswith('<!doctype html') or '<html' in content_start:
                    last_error = "YouTube returned HTML instead of XML (possible rate limiting/captcha)"
                    logger.warning(f"Feed fetch attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY_SECONDS * attempt)
                        continue
                    else:
                        logger.error(f"Feed fetch failed after {MAX_RETRIES} attempts for {feed_url}: {last_error}")
                        return []

                # Parse the XML content
                feed = feedparser.parse(response.content)

                if feed.bozo and feed.bozo_exception:
                    last_error = f"XML parse error: {feed.bozo_exception}"
                    logger.warning(f"Feed parse attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY_SECONDS * attempt)
                        continue
                    else:
                        logger.error(f"Feed parse failed after {MAX_RETRIES} attempts for {feed_url}: {last_error}")
                        return []

                videos = []
                channel_name = feed.feed.get('title', 'Unknown Channel')

                for entry in feed.entries:
                    video = self._parse_entry(entry, channel_id, channel_name)
                    if video:
                        videos.append(video)

                logger.info(f"Parsed {len(videos)} videos from {channel_name}")
                return videos

            except requests.exceptions.Timeout:
                last_error = f"Request timeout after {REQUEST_TIMEOUT}s"
                logger.warning(f"Feed fetch attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue
            except requests.exceptions.RequestException as e:
                last_error = f"Request error: {e}"
                logger.warning(f"Feed fetch attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.warning(f"Feed parse attempt {attempt}/{MAX_RETRIES} failed for {feed_url}: {last_error}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue

        logger.error(f"Feed processing failed after {MAX_RETRIES} attempts for {feed_url}: {last_error}")
        return []

    def _parse_entry(self, entry: dict, channel_id: str, channel_name: str) -> Optional[YouTubeVideo]:
        """Parse a single feed entry into a YouTubeVideo."""
        try:
            # Extract video ID from the entry ID (yt:video:VIDEO_ID format)
            video_id = None
            entry_id = entry.get('id', '')

            if entry_id.startswith('yt:video:'):
                video_id = entry_id.replace('yt:video:', '')
            elif 'yt_videoid' in entry:
                video_id = entry.yt_videoid

            if not video_id:
                # Try to extract from link
                link = entry.get('link', '')
                match = re.search(r'v=([a-zA-Z0-9_-]{11})', link)
                if match:
                    video_id = match.group(1)

            if not video_id:
                logger.warning(f"Could not extract video ID from entry: {entry.get('title', 'Unknown')}")
                return None

            # Parse published date
            published = entry.get('published_parsed') or entry.get('updated_parsed')
            if published:
                published_date = datetime(*published[:6], tzinfo=timezone.utc)
            else:
                published_date = datetime.now(timezone.utc)

            # Get description
            description = None
            if 'media_group' in entry and 'media_description' in entry.media_group:
                description = entry.media_group.media_description
            elif 'summary' in entry:
                description = entry.summary

            return YouTubeVideo(
                video_id=video_id,
                title=entry.get('title', 'Unknown Title'),
                published_date=published_date,
                channel_id=channel_id,
                channel_name=channel_name,
                description=description,
                duration_seconds=None  # YouTube RSS doesn't include duration
            )

        except Exception as e:
            logger.error(f"Failed to parse entry: {e}")
            return None

    def filter_new_videos(
        self,
        videos: List[YouTubeVideo],
        existing_video_ids: set
    ) -> List[YouTubeVideo]:
        """
        Filter videos to only include new ones within the lookback period.

        Args:
            videos: List of videos from feed
            existing_video_ids: Set of video IDs already in database

        Returns:
            Filtered list of new videos
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        new_videos = []
        for video in videos:
            # Skip if already processed
            if video.video_id in existing_video_ids:
                logger.debug(f"Skipping already processed video: {video.video_id}")
                continue

            # Skip if too old
            if video.published_date < cutoff_date:
                logger.debug(f"Skipping old video: {video.video_id} ({video.published_date})")
                continue

            new_videos.append(video)

        logger.info(f"Found {len(new_videos)} new videos within lookback period")
        return new_videos

    def filter_by_duration(
        self,
        videos: List[YouTubeVideo],
        min_seconds: int = None
    ) -> List[YouTubeVideo]:
        """
        Filter videos by minimum duration.

        Note: YouTube RSS feeds don't include duration, so this needs to be
        checked after fetching video metadata or transcript.

        Args:
            videos: List of videos
            min_seconds: Minimum duration in seconds (default: 180 = 3 minutes)

        Returns:
            Filtered list of videos meeting duration requirement
        """
        if min_seconds is None:
            min_seconds = self.MIN_DURATION_SECONDS

        filtered = []
        for video in videos:
            if video.duration_seconds is None:
                # Duration unknown - include for now, check later
                filtered.append(video)
            elif video.duration_seconds >= min_seconds:
                filtered.append(video)
            else:
                logger.debug(
                    f"Skipping short video: {video.video_id} "
                    f"({video.duration_seconds}s < {min_seconds}s)"
                )

        return filtered
