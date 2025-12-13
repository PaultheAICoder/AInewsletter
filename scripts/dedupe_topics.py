#!/usr/bin/env python3
"""
Topic Deduplication Script

Finds and consolidates semantically similar topics that slipped through during extraction.
Designed to run daily as a cleanup process.

Usage:
    python scripts/dedupe_topics.py [--dry-run] [--digest-topic NAME] [--verbose]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.supabase_client import SupabaseClient
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    # Create logs directory if needed
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    # Log file with date
    log_file = log_dir / f"dedupe_topics_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)


def merge_duplicate_topics(
    canonical: Dict,
    duplicates: List[Dict],
    db: SupabaseClient,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Merge duplicate topics into the canonical topic.

    Args:
        canonical: The topic to keep (oldest/most mentioned)
        duplicates: List of duplicate topics to merge into canonical
        db: Database client
        dry_run: If True, don't actually make changes
        logger: Logger instance

    Returns:
        Dict with merge statistics
    """
    stats = {
        'canonical_id': canonical['id'],
        'canonical_name': canonical['topic_name'],
        'duplicates_merged': 0,
        'key_points_added': 0,
        'errors': []
    }

    canonical_points = set(canonical.get('key_points', []) or [])
    new_key_points = []

    for dup in duplicates:
        dup_points = dup.get('key_points', []) or []

        # Find truly new key points (case-insensitive)
        canonical_points_lower = {p.lower() for p in canonical_points}
        for point in dup_points:
            if point.lower() not in canonical_points_lower:
                new_key_points.append(point)
                canonical_points_lower.add(point.lower())

        if logger:
            logger.info(
                f"  Merging '{dup['topic_name']}' (id={dup['id']}) into "
                f"'{canonical['topic_name']}' (id={canonical['id']})"
            )

        if not dry_run:
            try:
                # Delete the duplicate
                db.delete_episode_topic(dup['id'])
                stats['duplicates_merged'] += 1
            except Exception as e:
                error_msg = f"Failed to delete duplicate topic {dup['id']}: {e}"
                if logger:
                    logger.error(error_msg)
                stats['errors'].append(error_msg)

    # Update canonical with new key points (limit to 6 total)
    if new_key_points:
        combined_points = list(canonical.get('key_points', []) or []) + new_key_points
        combined_points = combined_points[:6]  # Keep top 6

        if logger:
            logger.info(
                f"  Adding {len(new_key_points)} new key points to canonical topic "
                f"(total: {len(combined_points)})"
            )

        if not dry_run:
            try:
                db.update_episode_topic_key_points(canonical['id'], combined_points)
                stats['key_points_added'] = len(new_key_points)
            except Exception as e:
                error_msg = f"Failed to update canonical topic {canonical['id']}: {e}"
                if logger:
                    logger.error(error_msg)
                stats['errors'].append(error_msg)

    return stats


def dedupe_digest_topic(
    digest_topic: str,
    db: SupabaseClient,
    matcher: SemanticTopicMatcher,
    days_back: int = 30,
    similarity_threshold: float = 0.85,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Deduplicate topics for a specific digest topic.

    Args:
        digest_topic: The digest topic to process
        db: Database client
        matcher: Semantic matcher instance
        days_back: How many days of topics to consider
        similarity_threshold: Minimum similarity to consider duplicates
        dry_run: If True, don't make changes
        logger: Logger instance

    Returns:
        Dict with deduplication statistics
    """
    stats = {
        'digest_topic': digest_topic,
        'topics_checked': 0,
        'duplicate_groups_found': 0,
        'topics_merged': 0,
        'key_points_consolidated': 0,
        'errors': []
    }

    if logger:
        logger.info(f"Processing digest topic: {digest_topic}")

    # Get recent topics
    topics = db.get_recent_episode_topics(digest_topic=digest_topic, days=days_back)
    stats['topics_checked'] = len(topics)

    if logger:
        logger.info(f"Found {len(topics)} topics in last {days_back} days")

    if len(topics) < 2:
        if logger:
            logger.info("Not enough topics to check for duplicates")
        return stats

    # Find duplicate groups
    duplicate_groups = matcher.find_duplicate_groups(
        topics, similarity_threshold=similarity_threshold
    )
    stats['duplicate_groups_found'] = len(duplicate_groups)

    if logger:
        logger.info(f"Found {len(duplicate_groups)} groups of duplicate topics")

    # Process each group
    for i, group in enumerate(duplicate_groups, 1):
        canonical = group[0]  # First is oldest/most mentioned
        duplicates = group[1:]

        if logger:
            logger.info(
                f"Group {i}: '{canonical['topic_name']}' has {len(duplicates)} duplicates"
            )
            for dup in duplicates:
                logger.info(f"  - '{dup['topic_name']}' (id={dup['id']})")

        merge_stats = merge_duplicate_topics(
            canonical=canonical,
            duplicates=duplicates,
            db=db,
            dry_run=dry_run,
            logger=logger
        )

        stats['topics_merged'] += merge_stats['duplicates_merged']
        stats['key_points_consolidated'] += merge_stats['key_points_added']
        stats['errors'].extend(merge_stats['errors'])

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Topic Deduplication Script')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--digest-topic', type=str, help='Process only specific digest topic')
    parser.add_argument('--days-back', type=int, default=30, help='Days of topics to consider (default: 30)')
    parser.add_argument('--similarity-threshold', type=float, default=0.85,
                        help='Similarity threshold for duplicates (default: 0.85)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info("Topic Deduplication Script Starting")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    try:
        # Initialize components
        db = SupabaseClient()
        matcher = SemanticTopicMatcher(similarity_threshold=args.similarity_threshold)

        # Get digest topics to process
        if args.digest_topic:
            digest_topics = [args.digest_topic]
        else:
            # Get all topics with tracking enabled
            topics_config = db.get_topics_with_tracking_enabled()
            digest_topics = [t['name'] for t in topics_config]

        logger.info(f"Processing {len(digest_topics)} digest topics")

        # Process each digest topic
        all_stats = []
        for digest_topic in digest_topics:
            stats = dedupe_digest_topic(
                digest_topic=digest_topic,
                db=db,
                matcher=matcher,
                days_back=args.days_back,
                similarity_threshold=args.similarity_threshold,
                dry_run=args.dry_run,
                logger=logger
            )
            all_stats.append(stats)

        # Summary
        logger.info("=" * 60)
        logger.info("DEDUPLICATION COMPLETE - SUMMARY")
        logger.info("=" * 60)

        total_checked = sum(s['topics_checked'] for s in all_stats)
        total_groups = sum(s['duplicate_groups_found'] for s in all_stats)
        total_merged = sum(s['topics_merged'] for s in all_stats)
        total_points = sum(s['key_points_consolidated'] for s in all_stats)
        total_errors = sum(len(s['errors']) for s in all_stats)

        logger.info(f"Digest topics processed: {len(digest_topics)}")
        logger.info(f"Topics checked: {total_checked}")
        logger.info(f"Duplicate groups found: {total_groups}")
        logger.info(f"Topics merged: {total_merged}")
        logger.info(f"Key points consolidated: {total_points}")
        logger.info(f"Errors: {total_errors}")

        return 0 if total_errors == 0 else 1

    except Exception as e:
        logger.error(f"Deduplication failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
