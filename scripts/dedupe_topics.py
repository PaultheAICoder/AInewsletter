#!/usr/bin/env python3
"""
Topic Deduplication and Story Arc Consolidation Script

Consolidates topics that belong to the same story arc, merging their key points
into a single evolving narrative. Also handles semantic duplicates.

Usage:
    python scripts/dedupe_topics.py [--dry-run] [--digest-topic NAME] [--verbose]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.supabase_client import SupabaseClient
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

# Import story arc patterns from topic extractor
from src.topic_tracking.topic_extractor import STORY_ARC_PATTERNS


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

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


def identify_story_arc(topic_name: str, key_points: List[str] = None) -> Optional[str]:
    """
    Identify which story arc a topic belongs to based on keywords.

    Args:
        topic_name: The topic name to check
        key_points: Optional key points for additional context

    Returns:
        Story arc identifier or None if no match
    """
    text_to_check = topic_name.lower()
    if key_points:
        text_to_check += " " + " ".join(key_points).lower()

    for arc_id, patterns in STORY_ARC_PATTERNS.items():
        for pattern in patterns:
            if pattern in text_to_check:
                return arc_id

    return None


def group_topics_by_story_arc(topics: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group topics by their story arc.

    Args:
        topics: List of topic dictionaries

    Returns:
        Dictionary mapping story arc IDs to lists of topics
    """
    arcs = {}
    for topic in topics:
        arc_id = identify_story_arc(
            topic.get('topic_name', ''),
            topic.get('key_points', [])
        )
        if arc_id:
            if arc_id not in arcs:
                arcs[arc_id] = []
            arcs[arc_id].append(topic)

    return arcs


def consolidate_story_arc(
    arc_id: str,
    topics: List[Dict],
    db: SupabaseClient,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Consolidate all topics in a story arc into one canonical topic.

    Args:
        arc_id: Story arc identifier
        topics: List of topics in this arc
        db: Database client
        dry_run: If True, don't make changes
        logger: Logger instance

    Returns:
        Dict with consolidation statistics
    """
    stats = {
        'arc_id': arc_id,
        'topics_consolidated': 0,
        'key_points_merged': 0,
        'errors': []
    }

    if len(topics) <= 1:
        return stats

    # Sort by first_mentioned_at to find canonical (oldest)
    topics.sort(key=lambda t: t.get('first_mentioned_at') or t.get('created_at') or datetime.min)
    canonical = topics[0]
    duplicates = topics[1:]

    if logger:
        logger.info(f"Story Arc '{arc_id}': Consolidating {len(duplicates)} topics into '{canonical['topic_name']}'")

    # Collect all unique key points
    all_key_points = list(canonical.get('key_points', []) or [])
    seen_points_lower = {p.lower() for p in all_key_points}

    for dup in duplicates:
        dup_points = dup.get('key_points', []) or []
        for point in dup_points:
            if point.lower() not in seen_points_lower:
                all_key_points.append(point)
                seen_points_lower.add(point.lower())
                stats['key_points_merged'] += 1

        if logger:
            logger.info(f"  - Merging '{dup['topic_name']}' (id={dup['id']})")

    # Limit to 6 key points (story arcs can have more than regular topics)
    final_key_points = all_key_points[:6]

    if not dry_run:
        try:
            # Update canonical with merged key points
            db.update_episode_topic_key_points(canonical['id'], final_key_points)

            # Delete duplicates
            for dup in duplicates:
                db.delete_episode_topic(dup['id'])
                stats['topics_consolidated'] += 1

        except Exception as e:
            error_msg = f"Failed to consolidate story arc {arc_id}: {e}"
            if logger:
                logger.error(error_msg)
            stats['errors'].append(error_msg)
    else:
        stats['topics_consolidated'] = len(duplicates)

    if logger:
        logger.info(
            f"  Result: '{canonical['topic_name']}' now has {len(final_key_points)} key points"
        )

    return stats


def merge_duplicate_topics(
    canonical: Dict,
    duplicates: List[Dict],
    db: SupabaseClient,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Merge semantic duplicate topics into the canonical topic.

    Args:
        canonical: The topic to keep (oldest/most mentioned)
        duplicates: List of duplicate topics to merge
        db: Database client
        dry_run: If True, don't make changes
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
                db.delete_episode_topic(dup['id'])
                stats['duplicates_merged'] += 1
            except Exception as e:
                error_msg = f"Failed to delete duplicate topic {dup['id']}: {e}"
                if logger:
                    logger.error(error_msg)
                stats['errors'].append(error_msg)

    if new_key_points:
        combined_points = list(canonical.get('key_points', []) or []) + new_key_points
        combined_points = combined_points[:6]

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
    similarity_threshold: float = 0.80,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Deduplicate and consolidate topics for a specific digest topic.

    Args:
        digest_topic: The digest topic to process
        db: Database client
        matcher: Semantic matcher instance
        days_back: How many days of topics to consider
        similarity_threshold: Minimum similarity for semantic duplicates
        dry_run: If True, don't make changes
        logger: Logger instance

    Returns:
        Dict with deduplication statistics
    """
    stats = {
        'digest_topic': digest_topic,
        'topics_checked': 0,
        'story_arcs_found': 0,
        'story_arc_topics_consolidated': 0,
        'semantic_duplicate_groups': 0,
        'semantic_duplicates_merged': 0,
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

    # PHASE 1: Consolidate story arcs
    if logger:
        logger.info("=" * 40)
        logger.info("PHASE 1: Story Arc Consolidation")
        logger.info("=" * 40)

    story_arcs = group_topics_by_story_arc(topics)
    stats['story_arcs_found'] = len([a for a in story_arcs.values() if len(a) > 1])

    for arc_id, arc_topics in story_arcs.items():
        if len(arc_topics) > 1:
            arc_stats = consolidate_story_arc(
                arc_id=arc_id,
                topics=arc_topics,
                db=db,
                dry_run=dry_run,
                logger=logger
            )
            stats['story_arc_topics_consolidated'] += arc_stats['topics_consolidated']
            stats['key_points_consolidated'] += arc_stats['key_points_merged']
            stats['errors'].extend(arc_stats['errors'])

    # Refresh topics list after story arc consolidation
    if not dry_run and stats['story_arc_topics_consolidated'] > 0:
        topics = db.get_recent_episode_topics(digest_topic=digest_topic, days=days_back)

    # PHASE 2: Semantic duplicate detection (for topics not in story arcs)
    if logger:
        logger.info("=" * 40)
        logger.info("PHASE 2: Semantic Duplicate Detection")
        logger.info("=" * 40)

    # Filter out topics that are in story arcs (already handled)
    non_arc_topics = [
        t for t in topics
        if identify_story_arc(t.get('topic_name', ''), t.get('key_points', [])) is None
    ]

    if len(non_arc_topics) >= 2:
        duplicate_groups = matcher.find_duplicate_groups(
            non_arc_topics, similarity_threshold=similarity_threshold
        )
        stats['semantic_duplicate_groups'] = len(duplicate_groups)

        if logger:
            logger.info(f"Found {len(duplicate_groups)} groups of semantic duplicates")

        for i, group in enumerate(duplicate_groups, 1):
            canonical = group[0]
            duplicates = group[1:]

            if logger:
                logger.info(
                    f"Group {i}: '{canonical['topic_name']}' has {len(duplicates)} duplicates"
                )

            merge_stats = merge_duplicate_topics(
                canonical=canonical,
                duplicates=duplicates,
                db=db,
                dry_run=dry_run,
                logger=logger
            )

            stats['semantic_duplicates_merged'] += merge_stats['duplicates_merged']
            stats['key_points_consolidated'] += merge_stats['key_points_added']
            stats['errors'].extend(merge_stats['errors'])

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Topic Deduplication and Story Arc Consolidation')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--digest-topic', type=str, help='Process only specific digest topic')
    parser.add_argument('--days-back', type=int, default=30, help='Days of topics to consider (default: 30)')
    parser.add_argument('--similarity-threshold', type=float, default=0.80,
                        help='Similarity threshold for semantic duplicates (default: 0.80)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info("Topic Deduplication & Story Arc Consolidation")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    try:
        db = SupabaseClient()
        matcher = SemanticTopicMatcher(similarity_threshold=args.similarity_threshold, db_client=db)

        if args.digest_topic:
            digest_topics = [args.digest_topic]
        else:
            topics_config = db.get_topics_with_tracking_enabled()
            digest_topics = [t['name'] for t in topics_config]

        logger.info(f"Processing {len(digest_topics)} digest topics")

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
        logger.info("CONSOLIDATION COMPLETE - SUMMARY")
        logger.info("=" * 60)

        total_checked = sum(s['topics_checked'] for s in all_stats)
        total_arcs = sum(s['story_arcs_found'] for s in all_stats)
        total_arc_consolidated = sum(s['story_arc_topics_consolidated'] for s in all_stats)
        total_semantic_groups = sum(s['semantic_duplicate_groups'] for s in all_stats)
        total_semantic_merged = sum(s['semantic_duplicates_merged'] for s in all_stats)
        total_points = sum(s['key_points_consolidated'] for s in all_stats)
        total_errors = sum(len(s['errors']) for s in all_stats)

        logger.info(f"Digest topics processed: {len(digest_topics)}")
        logger.info(f"Topics checked: {total_checked}")
        logger.info(f"Story arcs found: {total_arcs}")
        logger.info(f"Story arc topics consolidated: {total_arc_consolidated}")
        logger.info(f"Semantic duplicate groups: {total_semantic_groups}")
        logger.info(f"Semantic duplicates merged: {total_semantic_merged}")
        logger.info(f"Key points consolidated: {total_points}")
        logger.info(f"Errors: {total_errors}")

        return 0 if total_errors == 0 else 1

    except Exception as e:
        logger.error(f"Deduplication failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
