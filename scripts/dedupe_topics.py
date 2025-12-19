#!/usr/bin/env python3
"""
Story Arc Consolidation Script

Consolidates story arcs that are semantically similar (the LLM might create
"GPT-5.2 Release" and "GPT 5.2 Launch" as separate arcs when they should be one).

Also cleans up old arcs beyond the retention window.

Usage:
    python scripts/dedupe_topics.py [--dry-run] [--digest-topic NAME] [--verbose]
"""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.supabase_client import SupabaseClient
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"story_arc_consolidation_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)


def get_story_arcs_for_consolidation(
    db: SupabaseClient,
    digest_topic: str,
    days: int = 14
) -> List[Dict]:
    """
    Get story arcs for consolidation.

    Args:
        db: Database client
        digest_topic: Parent topic name
        days: Days of history to consider

    Returns:
        List of story arc dictionaries
    """
    arcs = db.get_active_story_arcs(digest_topic=digest_topic, days=days)
    return arcs


def find_similar_arc_groups(
    arcs: List[Dict],
    matcher: SemanticTopicMatcher,
    similarity_threshold: float = 0.80,
    logger: logging.Logger = None
) -> List[List[Dict]]:
    """
    Find groups of semantically similar story arcs.

    Args:
        arcs: List of story arc dictionaries
        matcher: Semantic matcher instance
        similarity_threshold: Minimum similarity to consider as same arc
        logger: Logger instance

    Returns:
        List of groups, each group is a list of similar arcs
    """
    if len(arcs) < 2:
        return []

    # Convert arcs to format expected by semantic matcher
    # We use arc_name + first event summary for comparison
    topics_for_matching = []
    for arc in arcs:
        events = arc.get('events', [])
        first_event_summary = events[0]['event_summary'] if events else ""

        topics_for_matching.append({
            'id': arc['id'],
            'topic_name': arc['arc_name'],
            'topic_slug': arc['arc_slug'],
            'key_points': [first_event_summary] if first_event_summary else [],
            'first_mentioned_at': arc['started_at'],
            'created_at': arc['created_at']
        })

    # Find duplicate groups
    groups = matcher.find_duplicate_groups(
        topics_for_matching,
        similarity_threshold=similarity_threshold
    )

    # Convert back to arc format
    arc_by_id = {arc['id']: arc for arc in arcs}
    result = []

    for group in groups:
        arc_group = [arc_by_id[t['id']] for t in group if t['id'] in arc_by_id]
        if len(arc_group) > 1:
            result.append(arc_group)

    return result


def merge_story_arcs(
    canonical: Dict,
    duplicates: List[Dict],
    db: SupabaseClient,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Merge duplicate story arcs into the canonical arc.

    Args:
        canonical: The arc to keep (oldest/most events)
        duplicates: List of duplicate arcs to merge
        db: Database client
        dry_run: If True, don't make changes
        logger: Logger instance

    Returns:
        Dict with merge statistics
    """
    stats = {
        'canonical_id': canonical['id'],
        'canonical_name': canonical['arc_name'],
        'arcs_merged': 0,
        'events_moved': 0,
        'errors': []
    }

    for dup in duplicates:
        if logger:
            logger.info(
                f"  Merging arc '{dup['arc_name']}' (id={dup['id']}, "
                f"events={dup['event_count']}) into '{canonical['arc_name']}'"
            )

        if not dry_run:
            try:
                # Move events from duplicate to canonical
                # This is done via SQL since we don't have a direct method
                with db._get_connection() as conn:
                    with conn.cursor() as cur:
                        # Update events to point to canonical arc
                        cur.execute("""
                            UPDATE story_arc_events
                            SET story_arc_id = %s
                            WHERE story_arc_id = %s
                        """, (canonical['id'], dup['id']))
                        events_moved = cur.rowcount
                        stats['events_moved'] += events_moved

                        # Delete the duplicate arc
                        cur.execute("""
                            DELETE FROM story_arcs WHERE id = %s
                        """, (dup['id'],))

                        # Update canonical arc's event_count and source_count
                        cur.execute("""
                            UPDATE story_arcs
                            SET event_count = (
                                SELECT COUNT(*) FROM story_arc_events
                                WHERE story_arc_id = %s
                            ),
                            source_count = (
                                SELECT COUNT(DISTINCT source_feed_id)
                                FROM story_arc_events
                                WHERE story_arc_id = %s AND source_feed_id IS NOT NULL
                            ),
                            updated_at = %s
                            WHERE id = %s
                        """, (canonical['id'], canonical['id'],
                              datetime.now(timezone.utc), canonical['id']))

                        conn.commit()

                stats['arcs_merged'] += 1

            except Exception as e:
                error_msg = f"Failed to merge arc {dup['id']}: {e}"
                if logger:
                    logger.error(error_msg)
                stats['errors'].append(error_msg)
        else:
            stats['arcs_merged'] += 1
            stats['events_moved'] += dup['event_count']

    return stats


def consolidate_story_arcs(
    digest_topic: str,
    db: SupabaseClient,
    matcher: SemanticTopicMatcher,
    days_back: int = 14,
    similarity_threshold: float = 0.80,
    dry_run: bool = False,
    logger: logging.Logger = None
) -> Dict:
    """
    Consolidate story arcs for a digest topic.

    Args:
        digest_topic: The digest topic to process
        db: Database client
        matcher: Semantic matcher instance
        days_back: Days of history to consider
        similarity_threshold: Minimum similarity for merging
        dry_run: If True, don't make changes
        logger: Logger instance

    Returns:
        Dict with consolidation statistics
    """
    stats = {
        'digest_topic': digest_topic,
        'arcs_checked': 0,
        'similar_groups_found': 0,
        'arcs_merged': 0,
        'events_moved': 0,
        'arcs_cleaned_up': 0,
        'errors': []
    }

    if logger:
        logger.info(f"Processing digest topic: {digest_topic}")

    # Get active story arcs
    arcs = get_story_arcs_for_consolidation(db, digest_topic, days_back)
    stats['arcs_checked'] = len(arcs)

    if logger:
        logger.info(f"Found {len(arcs)} active story arcs")

    if len(arcs) < 2:
        if logger:
            logger.info("Not enough arcs to check for duplicates")
        return stats

    # Find similar arc groups
    if logger:
        logger.info("=" * 40)
        logger.info("PHASE 1: Semantic Arc Consolidation")
        logger.info("=" * 40)

    similar_groups = find_similar_arc_groups(
        arcs, matcher, similarity_threshold, logger
    )
    stats['similar_groups_found'] = len(similar_groups)

    if logger:
        logger.info(f"Found {len(similar_groups)} groups of similar arcs")

    # Merge each group
    for i, group in enumerate(similar_groups, 1):
        # Sort by event_count descending, then by started_at ascending
        group.sort(key=lambda a: (-a['event_count'], a['started_at'] or datetime.min))
        canonical = group[0]
        duplicates = group[1:]

        if logger:
            logger.info(
                f"Group {i}: '{canonical['arc_name']}' has {len(duplicates)} duplicates"
            )

        merge_stats = merge_story_arcs(
            canonical=canonical,
            duplicates=duplicates,
            db=db,
            dry_run=dry_run,
            logger=logger
        )

        stats['arcs_merged'] += merge_stats['arcs_merged']
        stats['events_moved'] += merge_stats['events_moved']
        stats['errors'].extend(merge_stats['errors'])

    # PHASE 2: Cleanup old arcs
    if logger:
        logger.info("=" * 40)
        logger.info("PHASE 2: Cleanup Old Arcs")
        logger.info("=" * 40)

    if not dry_run:
        cleaned = db.cleanup_old_story_arcs(days=days_back)
        stats['arcs_cleaned_up'] = cleaned
        if logger:
            logger.info(f"Cleaned up {cleaned} old story arcs")
    else:
        if logger:
            logger.info("[DRY RUN] Would clean up old story arcs")

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Story Arc Consolidation')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--digest-topic', type=str, help='Process only specific digest topic')
    parser.add_argument('--days-back', type=int, default=14, help='Days of history to consider (default: 14)')
    parser.add_argument('--similarity-threshold', type=float, default=0.80,
                        help='Similarity threshold for merging (default: 0.80)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info("Story Arc Consolidation")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    # Generate unique run ID and track start time
    run_id = f"arc-consolidate-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc)
    db = None

    try:
        db = SupabaseClient()

        # Get retention days from settings
        retention_days = db.get_setting('story_arcs', 'retention_days', 14)
        days_back = args.days_back if args.days_back else retention_days
        logger.info(f"Using retention window: {days_back} days")

        # Log run start (only if not dry run)
        if not args.dry_run:
            db.log_pipeline_run(
                run_id=run_id,
                workflow_name='story_arc_consolidation',
                status='running',
                started_at=started_at,
                trigger='manual' if args.digest_topic else 'cron'
            )

        matcher = SemanticTopicMatcher(
            similarity_threshold=args.similarity_threshold,
            db_client=db
        )

        if args.digest_topic:
            digest_topics = [args.digest_topic]
        else:
            topics_config = db.get_topics_with_tracking_enabled()
            digest_topics = [t['name'] for t in topics_config]

        logger.info(f"Processing {len(digest_topics)} digest topics")

        all_stats = []
        for digest_topic in digest_topics:
            stats = consolidate_story_arcs(
                digest_topic=digest_topic,
                db=db,
                matcher=matcher,
                days_back=days_back,
                similarity_threshold=args.similarity_threshold,
                dry_run=args.dry_run,
                logger=logger
            )
            all_stats.append(stats)

        # Summary
        logger.info("=" * 60)
        logger.info("CONSOLIDATION COMPLETE - SUMMARY")
        logger.info("=" * 60)

        total_checked = sum(s['arcs_checked'] for s in all_stats)
        total_groups = sum(s['similar_groups_found'] for s in all_stats)
        total_merged = sum(s['arcs_merged'] for s in all_stats)
        total_events_moved = sum(s['events_moved'] for s in all_stats)
        total_cleaned = sum(s['arcs_cleaned_up'] for s in all_stats)
        total_errors = sum(len(s['errors']) for s in all_stats)

        logger.info(f"Digest topics processed: {len(digest_topics)}")
        logger.info(f"Story arcs checked: {total_checked}")
        logger.info(f"Similar groups found: {total_groups}")
        logger.info(f"Arcs merged: {total_merged}")
        logger.info(f"Events moved: {total_events_moved}")
        logger.info(f"Arcs cleaned up: {total_cleaned}")
        logger.info(f"Errors: {total_errors}")

        # Log successful completion to database
        if not args.dry_run:
            finished_at = datetime.now(timezone.utc)
            db.log_pipeline_run(
                run_id=run_id,
                workflow_name='story_arc_consolidation',
                status='completed',
                conclusion='success' if total_errors == 0 else 'failure',
                started_at=started_at,
                finished_at=finished_at,
                phase={
                    'digest_topics_processed': len(digest_topics),
                    'arcs_checked': total_checked,
                    'similar_groups_found': total_groups,
                    'arcs_merged': total_merged,
                    'events_moved': total_events_moved,
                    'arcs_cleaned_up': total_cleaned,
                    'errors': total_errors,
                    'duration_seconds': (finished_at - started_at).total_seconds()
                },
                notes=f"Processed {len(digest_topics)} digest topics, merged {total_merged} arcs"
            )

        return 0 if total_errors == 0 else 1

    except Exception as e:
        logger.error(f"Consolidation failed: {e}", exc_info=True)

        # Log failure to database
        if db and not args.dry_run:
            try:
                db.log_pipeline_run(
                    run_id=run_id,
                    workflow_name='story_arc_consolidation',
                    status='completed',
                    conclusion='failure',
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    notes=f"Error: {str(e)}"
                )
            except Exception:
                pass  # Don't fail on logging errors

        return 1


if __name__ == '__main__':
    sys.exit(main())
