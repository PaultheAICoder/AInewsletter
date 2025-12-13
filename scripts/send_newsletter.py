#!/usr/bin/env python3
"""
Send Newsletter

Sends a generated newsletter to all active subscribers.

Usage:
    python scripts/send_newsletter.py --issue-id N [--dry-run] [--verbose]
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.supabase_client import SupabaseClient
from src.newsletter.email_builder import EmailBuilder
from src.newsletter.sender import EmailSender


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"newsletter_send_{datetime.now().strftime('%Y%m%d')}.log"

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
    parser = argparse.ArgumentParser(description='Send Newsletter')
    parser.add_argument('--issue-id', type=int, required=True, help='Newsletter issue ID to send')
    parser.add_argument('--dry-run', action='store_true', help='Preview without sending')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')

    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    logger.info("=" * 60)
    logger.info(f"Newsletter Send - Issue #{args.issue_id}")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - No emails will be sent")

    try:
        db = SupabaseClient()

        # Get tracking URL from environment or use default
        tracking_url = os.getenv('SURVEY_TRACKING_URL', 'http://localhost:5000/api/survey')
        logger.info(f"Survey tracking URL: {tracking_url}")

        email_builder = EmailBuilder(tracking_base_url=tracking_url)
        sender = EmailSender()

        # Send newsletter
        stats = sender.send_newsletter(
            db_client=db,
            issue_id=args.issue_id,
            email_builder=email_builder,
            dry_run=args.dry_run
        )

        logger.info("=" * 60)
        logger.info("SEND COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total subscribers: {stats['total_subscribers']}")
        logger.info(f"Sent: {stats['sent']}")
        logger.info(f"Failed: {stats['failed']}")

        if stats['errors']:
            logger.warning("Errors:")
            for error in stats['errors']:
                logger.warning(f"  - {error['email']}: {error['error']}")

        print(f"\nNewsletter sent to {stats['sent']}/{stats['total_subscribers']} subscribers")

        return 0 if stats['failed'] == 0 else 1

    except Exception as e:
        logger.error(f"Newsletter send failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
