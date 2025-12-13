"""
Email Builder

Generates HTML email content with embedded survey buttons and personalized tracking links.
"""

import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NewsletterExample:
    """Example data for email template."""
    id: int
    position: int
    title: str
    description: str
    how_to_replicate: str
    source_url: str


class EmailBuilder:
    """Builds HTML email content for newsletters."""

    def __init__(self, tracking_base_url: str):
        """
        Initialize the email builder.

        Args:
            tracking_base_url: Base URL for survey tracking (e.g., https://yourdomain.com/api/survey)
        """
        self.tracking_base_url = tracking_base_url.rstrip('/')

    def _generate_survey_buttons(
        self,
        example_id: int,
        subscriber_hash: str
    ) -> str:
        """Generate HTML for survey buttons with tracking links."""

        yes_url = f"{self.tracking_base_url}?h={subscriber_hash}&e={example_id}&r=yes"
        no_url = f"{self.tracking_base_url}?h={subscriber_hash}&e={example_id}&r=no"

        return f'''
        <div style="margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 5px;">
            <p style="margin: 0 0 10px 0; font-size: 14px; color: #666;">
                Is this example applicable to your daily activities?
            </p>
            <a href="{yes_url}" style="display: inline-block; padding: 8px 20px; margin-right: 10px; background-color: #28a745; color: white; text-decoration: none; border-radius: 4px; font-size: 14px;">👍 Yes</a>
            <a href="{no_url}" style="display: inline-block; padding: 8px 20px; background-color: #6c757d; color: white; text-decoration: none; border-radius: 4px; font-size: 14px;">👎 No</a>
        </div>
        '''

    def _build_example_card(
        self,
        example: NewsletterExample,
        subscriber_hash: str
    ) -> str:
        """Build HTML card for a single example."""

        # Convert how_to_replicate to numbered list if it contains steps
        steps_html = ""
        if example.how_to_replicate:
            steps = example.how_to_replicate.replace("Step ", "\nStep ").strip().split("\n")
            steps = [s.strip() for s in steps if s.strip()]
            if steps:
                steps_html = "<ol style='margin: 10px 0; padding-left: 20px;'>"
                for step in steps:
                    # Remove "Step N:" prefix if present
                    step_text = step
                    if step.lower().startswith("step"):
                        parts = step.split(":", 1)
                        if len(parts) > 1:
                            step_text = parts[1].strip()
                    steps_html += f"<li style='margin: 5px 0;'>{step_text}</li>"
                steps_html += "</ol>"

        source_link = ""
        if example.source_url:
            source_link = f'<a href="{example.source_url}" style="color: #007bff; text-decoration: none;">Watch source →</a>'

        survey_buttons = self._generate_survey_buttons(example.id, subscriber_hash)

        return f'''
        <div style="background-color: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
            <h3 style="margin: 0 0 10px 0; color: #333; font-size: 18px;">
                {example.position}. {example.title}
            </h3>
            <p style="margin: 0 0 15px 0; color: #555; line-height: 1.6;">
                {example.description}
            </p>
            <div style="background-color: #f0f7ff; padding: 15px; border-radius: 5px; margin-bottom: 15px;">
                <strong style="color: #0066cc;">How to try it:</strong>
                {steps_html if steps_html else f"<p style='margin: 10px 0 0 0;'>{example.how_to_replicate}</p>"}
            </div>
            <p style="margin: 0; font-size: 14px; color: #888;">
                {source_link}
            </p>
            {survey_buttons}
        </div>
        '''

    def build_email(
        self,
        issue_id: int,
        subject_line: str,
        big_news: Optional[str],
        examples: List[NewsletterExample],
        subscriber_hash: str,
        subscriber_name: Optional[str] = None
    ) -> str:
        """
        Build complete HTML email.

        Args:
            issue_id: Newsletter issue ID
            subject_line: Email subject
            big_news: Big news summary (if any)
            examples: List of examples
            subscriber_hash: Unique hash for this subscriber (for tracking)
            subscriber_name: Subscriber's name for personalization

        Returns:
            Complete HTML email content
        """

        greeting = f"Hi {subscriber_name}," if subscriber_name else "Hi there,"

        # Big news section
        big_news_section = ""
        if big_news:
            big_news_section = f'''
            <div style="background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 20px; margin-bottom: 25px;">
                <h2 style="margin: 0 0 10px 0; color: #856404; font-size: 20px;">
                    🚀 Big News This Week
                </h2>
                <p style="margin: 0; color: #856404; line-height: 1.6;">
                    {big_news}
                </p>
            </div>
            '''

        # Build example cards
        example_cards = ""
        for example in examples:
            example_cards += self._build_example_card(example, subscriber_hash)

        # Complete email HTML
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject_line}</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">

        <!-- Header -->
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 8px 8px 0 0; text-align: center;">
            <h1 style="margin: 0; color: white; font-size: 28px;">AI Ready PDX</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.9); font-size: 16px;">
                Weekly AI Insights & Practical Examples
            </p>
        </div>

        <!-- Main Content -->
        <div style="background-color: #ffffff; padding: 30px; border-radius: 0 0 8px 8px;">

            <p style="font-size: 16px; color: #333; line-height: 1.6;">
                {greeting}
            </p>
            <p style="font-size: 16px; color: #333; line-height: 1.6;">
                Here are this week's most interesting AI examples and practical tips you can try right now.
            </p>

            {big_news_section}

            <h2 style="color: #333; font-size: 22px; margin: 25px 0 15px 0; border-bottom: 2px solid #667eea; padding-bottom: 10px;">
                💡 This Week's AI Examples
            </h2>

            {example_cards}

            <!-- Footer -->
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; text-align: center;">
                <p style="font-size: 14px; color: #888; margin: 0 0 10px 0;">
                    You're receiving this because you subscribed to AI Ready PDX updates.
                </p>
                <p style="font-size: 12px; color: #aaa; margin: 0;">
                    AI Ready PDX · Portland, Oregon
                </p>
            </div>

        </div>
    </div>
</body>
</html>'''

        return html

    def build_email_from_db(
        self,
        db_client,
        issue_id: int,
        subscriber_hash: str,
        subscriber_name: Optional[str] = None
    ) -> str:
        """
        Build email from database records.

        Args:
            db_client: Database client
            issue_id: Newsletter issue ID
            subscriber_hash: Subscriber's unique hash
            subscriber_name: Subscriber's name

        Returns:
            Complete HTML email
        """
        from psycopg2.extras import RealDictCursor

        with db_client._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get issue
                cur.execute("""
                    SELECT subject_line, big_news_summary
                    FROM newsletter_issues WHERE id = %s
                """, (issue_id,))
                issue = cur.fetchone()

                if not issue:
                    raise ValueError(f"Newsletter issue {issue_id} not found")

                # Get examples
                cur.execute("""
                    SELECT id, position, title, description, how_to_replicate, source_url
                    FROM newsletter_examples
                    WHERE issue_id = %s
                    ORDER BY position
                """, (issue_id,))
                examples_data = cur.fetchall()

                examples = [
                    NewsletterExample(
                        id=ex['id'],
                        position=ex['position'],
                        title=ex['title'],
                        description=ex['description'],
                        how_to_replicate=ex['how_to_replicate'] or '',
                        source_url=ex['source_url'] or ''
                    )
                    for ex in examples_data
                ]

                return self.build_email(
                    issue_id=issue_id,
                    subject_line=issue['subject_line'],
                    big_news=issue['big_news_summary'],
                    examples=examples,
                    subscriber_hash=subscriber_hash,
                    subscriber_name=subscriber_name
                )
