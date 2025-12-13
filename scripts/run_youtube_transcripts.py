#!/usr/bin/env python3
"""
YouTube Transcript Pipeline

Downloads transcripts from YouTube feeds, scores them, and stores in Supabase.
Designed to run as a daily cron job.

Usage:
    python scripts/run_youtube_transcripts.py [--dry-run] [--feed-id ID] [--verbose]
"""

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.youtube.transcript_fetcher import YouTubeTranscriptFetcher
from src.youtube.feed_processor import YouTubeFeedProcessor
from src.database.supabase_client import SupabaseClient
from src.scoring.content_scorer import ContentScorer
from src.topic_tracking.topic_extractor import TopicExtractor

# Minimum video duration in seconds (3 minutes)
MIN_DURATION_SECONDS = 180

# Delay range between feeds (seconds)
MIN_DELAY = 5
MAX_DELAY = 30


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    # Create logs directory if needed
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    # Log file with date
    log_file = log_dir / f"youtube_transcripts_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)


def estimate_duration_from_transcript(word_count: int) -> int:
    """
    Estimate video duration from transcript word count.
    Average speaking rate is ~150 words per minute.
    """
    words_per_minute = 150
    return int((word_count / words_per_minute) * 60)


def process_feed(
    feed: dict,
    db: SupabaseClient,
    fetcher: YouTubeTranscriptFetcher,
    feed_processor: YouTubeFeedProcessor,
    scorer: ContentScorer,
    score_threshold: float,
    topic_extractor: TopicExtractor = None,
    topics_with_tracking: list = None,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> dict:
    """
    Process a single YouTube feed.

    Returns:
        Dictionary with processing results
    """
    feed_id = feed['id']
    feed_title = feed['title']
    feed_url = feed['feed_url']

    logger.info(f"Processing feed: {feed_title} (ID: {feed_id})")

    results = {
        'feed_id': feed_id,
        'feed_title': feed_title,
        'videos_found': 0,
        'videos_new': 0,
        'transcripts_downloaded': 0,
        'transcripts_scored': 0,
        'episodes_relevant': 0,
        'episodes_not_relevant': 0,
        'topics_extracted': 0,
        'errors': []
    }

    try:
        # Parse feed to get videos
        videos = feed_processor.parse_feed(feed_url)
        results['videos_found'] = len(videos)

        if not videos:
            logger.info(f"No videos found in feed: {feed_title}")
            return results

        # Get existing episode GUIDs to skip duplicates
        existing_guids = db.get_existing_episode_guids(feed_id)

        # Filter to new videos within lookback period
        new_videos = feed_processor.filter_new_videos(videos, existing_guids)
        results['videos_new'] = len(new_videos)

        if not new_videos:
            logger.info(f"No new videos in feed: {feed_title}")
            return results

        logger.info(f"Found {len(new_videos)} new videos to process")

        # Process each new video
        for video in new_videos:
            video_id = video.video_id

            # Skip if already exists (double-check)
            if db.episode_exists(video_id):
                logger.debug(f"Skipping existing video: {video_id}")
                continue

            logger.info(f"Processing video: {video.title} ({video_id})")

            # Fetch transcript
            transcript_result = fetcher.fetch_transcript(video_id)

            if not transcript_result.success:
                error_msg = f"Failed to fetch transcript for {video_id}: {transcript_result.error_message}"
                logger.error(error_msg)
                results['errors'].append(error_msg)

                if not dry_run:
                    # Create failed episode record
                    try:
                        db.create_episode(
                            episode_guid=video_id,
                            feed_id=feed_id,
                            title=video.title,
                            published_date=video.published_date,
                            video_url=video.video_url,
                            duration_seconds=None,
                            description=video.description,
                            transcript_content="",
                            transcript_word_count=0,
                            status='failed'
                        )
                        db.update_episode_failed(video_id, transcript_result.error_message)
                    except Exception as e:
                        logger.error(f"Failed to create failed episode record: {e}")
                continue

            results['transcripts_downloaded'] += 1

            # Estimate duration from word count
            estimated_duration = estimate_duration_from_transcript(transcript_result.word_count)

            # Skip short videos
            if estimated_duration < MIN_DURATION_SECONDS:
                logger.info(
                    f"Skipping short video: {video_id} "
                    f"(estimated {estimated_duration}s < {MIN_DURATION_SECONDS}s)"
                )
                continue

            if dry_run:
                logger.info(f"[DRY RUN] Would store transcript: {video_id} ({transcript_result.word_count} words)")
                continue

            # Create episode record with transcript
            try:
                episode_id = db.create_episode(
                    episode_guid=video_id,
                    feed_id=feed_id,
                    title=video.title,
                    published_date=video.published_date,
                    video_url=video.video_url,
                    duration_seconds=estimated_duration,
                    description=video.description,
                    transcript_content=transcript_result.transcript_text,
                    transcript_word_count=transcript_result.word_count,
                    status='transcribed'
                )
                logger.info(f"Created episode record: {episode_id}")
            except Exception as e:
                error_msg = f"Failed to create episode for {video_id}: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                continue

            # Score the transcript
            scoring_result = scorer.score_transcript(
                transcript_result.transcript_text,
                episode_id=video_id
            )

            if not scoring_result.success:
                error_msg = f"Scoring failed for {video_id}: {scoring_result.error_message}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                continue

            results['transcripts_scored'] += 1

            # Determine relevance and update status
            is_relevant = scorer.is_relevant(scoring_result.scores)
            status = 'scored' if is_relevant else 'not_relevant'

            db.update_episode_scores(video_id, scoring_result.scores, status)

            if is_relevant:
                results['episodes_relevant'] += 1
                relevant_topics = scorer.get_relevant_topics(scoring_result.scores)
                logger.info(f"Episode {video_id} is RELEVANT for topics: {relevant_topics}")

                # Extract topics for topics with tracking enabled AND score >= min_score_for_extraction
                # This aligns with podscrape2's approach
                if topic_extractor and topics_with_tracking and not dry_run:
                    # Get episode ID from database
                    episode = db.get_episode_by_guid(video_id)
                    if episode:
                        # Only extract for topics that have tracking enabled
                        tracking_topic_names = {t['name'] for t in topics_with_tracking}

                        for topic_name in relevant_topics:
                            # Skip if topic doesn't have tracking enabled
                            if topic_name not in tracking_topic_names:
                                logger.debug(f"Skipping topic extraction for '{topic_name}' (tracking not enabled)")
                                continue

                            topic_score = scoring_result.scores.get(topic_name, 0.0)

                            # Skip if score below threshold (podscrape2 alignment)
                            if topic_score < score_threshold:
                                logger.debug(
                                    f"Skipping topic extraction for '{topic_name}' "
                                    f"(score {topic_score:.2f} < {score_threshold})"
                                )
                                continue

                            try:
                                extracted = topic_extractor.extract_and_store_topics(
                                    episode_id=episode['id'],
                                    episode_guid=video_id,
                                    digest_topic=topic_name,
                                    transcript=transcript_result.transcript_text,
                                    relevance_score=topic_score
                                )
                                results['topics_extracted'] += len(extracted)
                                logger.info(
                                    f"Extracted {len(extracted)} topics for {video_id} "
                                    f"under '{topic_name}'"
                                )
                            except Exception as e:
                                error_msg = f"Topic extraction failed for {video_id}/{topic_name}: {e}"
                                logger.error(error_msg)
                                results['errors'].append(error_msg)
            else:
                results['episodes_not_relevant'] += 1
                logger.info(f"Episode {video_id} is NOT RELEVANT (scores: {scoring_result.scores})")

        return results

    except Exception as e:
        error_msg = f"Error processing feed {feed_title}: {e}"
        logger.error(error_msg)
        results['errors'].append(error_msg)
        return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='YouTube Transcript Pipeline')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--feed-id', type=int, help='Process only specific feed ID')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--no-delay', action='store_true', help='Skip delays between feeds (for testing)')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info("YouTube Transcript Pipeline Starting")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    try:
        # Initialize components
        db = SupabaseClient()
        fetcher = YouTubeTranscriptFetcher()

        # Get lookback days from settings
        lookback_days = db.get_setting('pipeline', 'discovery_lookback_days', 5)
        logger.info(f"Using lookback period: {lookback_days} days")

        feed_processor = YouTubeFeedProcessor(lookback_days=lookback_days)

        # Get active topics for scoring
        topics = db.get_active_topics()
        if not topics:
            logger.error("No active topics found in database")
            return 1

        logger.info(f"Loaded {len(topics)} active topics for scoring")

        # Get score threshold from settings (content_filtering.score_threshold)
        score_threshold = db.get_setting('content_filtering', 'score_threshold', 0.6)
        logger.info(f"Using score threshold: {score_threshold}")

        scorer = ContentScorer(
            topics=topics,
            score_threshold=score_threshold,
            db_client=db  # Load model from web_settings
        )

        # Initialize topic extractor (aligned with podscrape2 settings)
        max_topics_per_episode = db.get_setting('topic_tracking', 'max_topics_per_episode', 15)
        novelty_threshold = db.get_setting('topic_evolution', 'novelty_threshold', 0.30)
        enable_novelty = db.get_setting('topic_evolution', 'enable_novelty_detection', True)

        # Get topics with topic tracking enabled (like podscrape2)
        topics_with_tracking = db.get_topics_with_tracking_enabled()
        logger.info(f"Topics with tracking enabled: {[t['name'] for t in topics_with_tracking]}")

        topic_extractor = TopicExtractor(
            db_client=db,
            max_topics=max_topics_per_episode,
            novelty_threshold=novelty_threshold,
            enable_novelty_detection=enable_novelty
        )
        logger.info(
            f"TopicExtractor initialized: max_topics={max_topics_per_episode}, "
            f"score_threshold={score_threshold}, novelty_threshold={novelty_threshold}"
        )

        # Get YouTube feeds
        if args.feed_id:
            feeds = [f for f in db.get_youtube_feeds() if f['id'] == args.feed_id]
            if not feeds:
                logger.error(f"Feed ID {args.feed_id} not found or not a YouTube feed")
                return 1
        else:
            feeds = db.get_youtube_feeds()

        logger.info(f"Found {len(feeds)} YouTube feeds to process")

        # Process each feed
        all_results = []
        for i, feed in enumerate(feeds):
            # Add random delay between feeds (except for first one)
            if i > 0 and not args.no_delay:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                logger.info(f"Waiting {delay:.1f}s before next feed...")
                time.sleep(delay)

            results = process_feed(
                feed=feed,
                db=db,
                fetcher=fetcher,
                feed_processor=feed_processor,
                scorer=scorer,
                score_threshold=score_threshold,
                topic_extractor=topic_extractor,
                topics_with_tracking=topics_with_tracking,
                dry_run=args.dry_run,
                logger=logger
            )
            all_results.append(results)

        # Summary
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE - SUMMARY")
        logger.info("=" * 60)

        total_videos = sum(r['videos_found'] for r in all_results)
        total_new = sum(r['videos_new'] for r in all_results)
        total_transcripts = sum(r['transcripts_downloaded'] for r in all_results)
        total_scored = sum(r['transcripts_scored'] for r in all_results)
        total_relevant = sum(r['episodes_relevant'] for r in all_results)
        total_not_relevant = sum(r['episodes_not_relevant'] for r in all_results)
        total_topics = sum(r['topics_extracted'] for r in all_results)
        total_errors = sum(len(r['errors']) for r in all_results)

        logger.info(f"Feeds processed: {len(feeds)}")
        logger.info(f"Videos found: {total_videos}")
        logger.info(f"New videos: {total_new}")
        logger.info(f"Transcripts downloaded: {total_transcripts}")
        logger.info(f"Episodes scored: {total_scored}")
        logger.info(f"Episodes relevant: {total_relevant}")
        logger.info(f"Episodes not relevant: {total_not_relevant}")
        logger.info(f"Topics extracted: {total_topics}")
        logger.info(f"Errors: {total_errors}")

        return 0 if total_errors == 0 else 1

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
