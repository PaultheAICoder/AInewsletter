#!/usr/bin/env python3
"""
Generate Newsletter

Analyzes recent episode transcripts and generates a new newsletter issue.

Usage:
    python scripts/generate_newsletter.py [--days N] [--dry-run] [--verbose]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.supabase_client import SupabaseClient
from src.newsletter.generator import NewsletterGenerator


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"newsletter_generate_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Generate Newsletter')
    parser.add_argument('--days', type=int, default=7, help='Days to look back for episodes')
    parser.add_argument('--dry-run', action='store_true', help='Generate but do not save')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info("Newsletter Generation Starting")
    logger.info("=" * 60)

    try:
        db = SupabaseClient()
        generator = NewsletterGenerator(db)

        # Generate content
        content = generator.generate_content(days=args.days)

        if not content:
            logger.warning("No content generated - no suitable episodes found")
            return 1

        logger.info(f"Generated content:")
        logger.info(f"  - Big news: {bool(content.big_news)}")
        logger.info(f"  - Examples: {len(content.examples)}")
        logger.info(f"  - Episodes analyzed: {content.episodes_analyzed}")

        for i, ex in enumerate(content.examples, 1):
            logger.info(f"  Example {i}: {ex.title}")

        if args.dry_run:
            logger.info("[DRY RUN] Newsletter not saved")
            return 0

        # Save to database
        issue_id = generator.save_newsletter(content)
        logger.info(f"Newsletter saved as issue #{issue_id}")

        # Cleanup old newsletters (keep only 20)
        deleted = generator.cleanup_old_newsletters(keep_count=20)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old newsletter(s)")

        print(f"\nNewsletter issue #{issue_id} created successfully!")
        print(f"Run 'python scripts/send_newsletter.py --issue-id {issue_id}' to send")

        return 0

    except Exception as e:
        logger.error(f"Newsletter generation failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
