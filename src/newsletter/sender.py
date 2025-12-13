"""
Email Sender

Sends newsletters via SMTP through Microsoft 365.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Result of sending an email."""
    subscriber_email: str
    success: bool
    error_message: Optional[str] = None


class EmailSender:
    """Sends emails via SMTP."""

    def __init__(
        self,
        smtp_host: str = None,
        smtp_port: int = None,
        smtp_user: str = None,
        smtp_password: str = None,
        from_email: str = None,
        from_name: str = None
    ):
        """
        Initialize the email sender.

        Args:
            smtp_host: SMTP server hostname
            smtp_port: SMTP server port
            smtp_user: SMTP username
            smtp_password: SMTP password
            from_email: From email address
            from_name: From display name
        """
        self.smtp_host = smtp_host or os.getenv('SMTP_HOST', 'smtp.office365.com')
        self.smtp_port = smtp_port or int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = smtp_user or os.getenv('SMTP_USER')
        self.smtp_password = smtp_password or os.getenv('SMTP_PASSWORD')
        self.from_email = from_email or os.getenv('SMTP_FROM_EMAIL', self.smtp_user)
        self.from_name = from_name or os.getenv('SMTP_FROM_NAME', 'AI Ready PDX')

        if not self.smtp_user or not self.smtp_password:
            raise ValueError("SMTP_USER and SMTP_PASSWORD must be set")

        logger.info(f"EmailSender initialized: {self.smtp_host}:{self.smtp_port}")

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        to_name: Optional[str] = None
    ) -> SendResult:
        """
        Send a single email.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML email body
            to_name: Recipient name for display

        Returns:
            SendResult with success status
        """
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = f"{to_name} <{to_email}>" if to_name else to_email

            # Attach HTML content
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)

            # Send via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to_email, msg.as_string())

            logger.info(f"Email sent successfully to {to_email}")
            return SendResult(subscriber_email=to_email, success=True)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send email to {to_email}: {error_msg}")
            return SendResult(
                subscriber_email=to_email,
                success=False,
                error_message=error_msg
            )

    def send_newsletter(
        self,
        db_client,
        issue_id: int,
        email_builder,
        dry_run: bool = False
    ) -> Dict:
        """
        Send newsletter to all active subscribers.

        Args:
            db_client: Database client
            issue_id: Newsletter issue ID
            email_builder: EmailBuilder instance
            dry_run: If True, don't actually send emails

        Returns:
            Dictionary with send statistics
        """
        from psycopg2.extras import RealDictCursor

        stats = {
            'total_subscribers': 0,
            'sent': 0,
            'failed': 0,
            'errors': []
        }

        with db_client._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get newsletter issue
                cur.execute("""
                    SELECT id, subject_line FROM newsletter_issues WHERE id = %s
                """, (issue_id,))
                issue = cur.fetchone()

                if not issue:
                    raise ValueError(f"Newsletter issue {issue_id} not found")

                # Get active subscribers
                cur.execute("""
                    SELECT id, email, name, subscriber_hash
                    FROM subscribers
                    WHERE is_active = true
                """)
                subscribers = cur.fetchall()

                stats['total_subscribers'] = len(subscribers)
                logger.info(f"Sending newsletter {issue_id} to {len(subscribers)} subscribers")

                for subscriber in subscribers:
                    try:
                        # Build personalized email
                        html_content = email_builder.build_email_from_db(
                            db_client=db_client,
                            issue_id=issue_id,
                            subscriber_hash=subscriber['subscriber_hash'],
                            subscriber_name=subscriber['name']
                        )

                        if dry_run:
                            logger.info(f"[DRY RUN] Would send to {subscriber['email']}")
                            stats['sent'] += 1
                        else:
                            result = self.send_email(
                                to_email=subscriber['email'],
                                subject=issue['subject_line'],
                                html_content=html_content,
                                to_name=subscriber['name']
                            )

                            if result.success:
                                stats['sent'] += 1
                            else:
                                stats['failed'] += 1
                                stats['errors'].append({
                                    'email': subscriber['email'],
                                    'error': result.error_message
                                })

                    except Exception as e:
                        stats['failed'] += 1
                        stats['errors'].append({
                            'email': subscriber['email'],
                            'error': str(e)
                        })
                        logger.error(f"Error processing subscriber {subscriber['email']}: {e}")

                # Update sent_at timestamp
                if not dry_run and stats['sent'] > 0:
                    cur.execute("""
                        UPDATE newsletter_issues
                        SET sent_at = %s
                        WHERE id = %s
                    """, (datetime.now(timezone.utc), issue_id))
                    conn.commit()

        logger.info(f"Newsletter send complete: {stats['sent']}/{stats['total_subscribers']} sent")
        return stats
