"""
Newsletter Content Generator

Uses GPT-4 to analyze episode transcripts and extract actionable AI examples
for the weekly newsletter.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class AIExample:
    """An actionable AI example extracted from transcripts."""
    title: str
    description: str
    how_to_replicate: str
    why_useful: str
    source_episode_id: int
    source_title: str
    source_url: str


@dataclass
class NewsletterContent:
    """Generated newsletter content."""
    big_news: Optional[str]
    examples: List[AIExample]
    generation_date: datetime
    episodes_analyzed: int


class NewsletterGenerator:
    """Generates newsletter content from episode transcripts using GPT-4."""

    # Minimum AI score to include episode in analysis
    MIN_AI_SCORE = 0.7

    # Maximum examples to include
    MAX_EXAMPLES = 5

    def __init__(self, db_client):
        """
        Initialize the generator.

        Args:
            db_client: SupabaseClient instance for database access
        """
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key, timeout=120.0)
        self.db = db_client

        # Load model from web_settings
        self.model = db_client.get_setting('ai_digest_generation', 'model', 'gpt-4o')

        logger.info(f"NewsletterGenerator initialized with model={self.model}")

    def get_recent_episodes(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get episodes from the past N days with high AI scores.

        Args:
            days: Number of days to look back

        Returns:
            List of episode dictionaries
        """
        query = """
            SELECT
                e.id,
                e.episode_guid,
                e.title,
                e.transcript_content,
                e.transcript_word_count,
                e.scores,
                e.published_date,
                e.audio_url as source_url,
                f.title as feed_title
            FROM episodes e
            JOIN feeds f ON e.feed_id = f.id
            WHERE e.status = 'scored'
              AND e.transcript_content IS NOT NULL
              AND e.scored_at >= NOW() - INTERVAL '%s days'
              AND (e.scores->>'AI and Technology')::float >= %s
            ORDER BY (e.scores->>'AI and Technology')::float DESC
            LIMIT 20
        """

        import psycopg2
        from psycopg2.extras import RealDictCursor

        with self.db._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (days, self.MIN_AI_SCORE))
                episodes = cur.fetchall()
                return [dict(e) for e in episodes]

    def _create_extraction_prompt(self, episodes: List[Dict]) -> str:
        """Create the prompt for extracting AI examples."""

        # Prepare episode summaries (truncate transcripts to fit context)
        episode_texts = []
        for i, ep in enumerate(episodes[:10], 1):  # Limit to top 10 episodes
            transcript = ep.get('transcript_content', '')[:8000]  # Truncate
            episode_texts.append(f"""
--- EPISODE {i} ---
Title: {ep['title']}
Source: {ep['feed_title']}
Episode ID: {ep['id']}
Transcript excerpt:
{transcript}
""")

        all_episodes = "\n".join(episode_texts)

        prompt = f"""You are an expert AI analyst creating a weekly newsletter about practical AI applications.

Analyze these episode transcripts and extract the most interesting, actionable AI examples that readers could replicate or learn from.

{all_episodes}

INSTRUCTIONS:
1. First, check if there are any MAJOR AI announcements (new model releases like GPT-5, Claude 4, Gemini 2, etc., or significant company news). If so, summarize in 2-3 sentences for the "big_news" field. If no major news, set big_news to null.

2. Extract up to 5 unique, actionable AI examples. For each example:
   - title: A catchy, specific title (e.g., "Use Claude to Generate Video Scripts in Minutes")
   - description: What this AI application does and why it's interesting (2-3 sentences)
   - how_to_replicate: Step-by-step instructions for readers to try it themselves (3-5 steps)
   - why_useful: Who would benefit and what problem it solves (1-2 sentences)
   - source_episode_id: The episode ID number where this was discussed

3. Prioritize examples that are:
   - Practical and immediately actionable
   - Unique or novel (not obvious uses like "use ChatGPT to write emails")
   - Specific enough to replicate
   - Relevant to business professionals, developers, or creators

Return your response as a JSON object with this exact structure:
{{
    "big_news": "Major announcement summary or null",
    "examples": [
        {{
            "title": "Example title",
            "description": "What it does and why it's interesting",
            "how_to_replicate": "Step 1: ... Step 2: ... Step 3: ...",
            "why_useful": "Who benefits and what problem it solves",
            "source_episode_id": 123
        }}
    ]
}}
"""
        return prompt

    def generate_content(self, days: int = 7) -> Optional[NewsletterContent]:
        """
        Generate newsletter content from recent episodes.

        Args:
            days: Number of days to look back for episodes

        Returns:
            NewsletterContent object or None if no suitable episodes
        """
        logger.info(f"Generating newsletter content for past {days} days")

        # Get recent high-scoring episodes
        episodes = self.get_recent_episodes(days)

        if not episodes:
            logger.warning("No recent episodes with high AI scores found")
            return None

        logger.info(f"Found {len(episodes)} episodes to analyze")

        # Create episode lookup for source info
        episode_lookup = {ep['id']: ep for ep in episodes}

        try:
            prompt = self._create_extraction_prompt(episodes)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=4000,
                temperature=0.7
            )

            result = json.loads(response.choices[0].message.content)

            # Parse examples
            examples = []
            for ex in result.get('examples', [])[:self.MAX_EXAMPLES]:
                source_id = ex.get('source_episode_id')
                source_ep = episode_lookup.get(source_id, {})

                examples.append(AIExample(
                    title=ex.get('title', 'Untitled'),
                    description=ex.get('description', ''),
                    how_to_replicate=ex.get('how_to_replicate', ''),
                    why_useful=ex.get('why_useful', ''),
                    source_episode_id=source_id,
                    source_title=source_ep.get('title', 'Unknown'),
                    source_url=source_ep.get('source_url', '')
                ))

            content = NewsletterContent(
                big_news=result.get('big_news'),
                examples=examples,
                generation_date=datetime.now(timezone.utc),
                episodes_analyzed=len(episodes)
            )

            logger.info(f"Generated {len(examples)} examples, big_news: {bool(content.big_news)}")

            return content

        except Exception as e:
            logger.error(f"Failed to generate newsletter content: {e}")
            raise

    def save_newsletter(self, content: NewsletterContent) -> int:
        """
        Save generated newsletter to database.

        Args:
            content: NewsletterContent to save

        Returns:
            Newsletter issue ID
        """
        import psycopg2

        # Create subject line
        if content.big_news:
            subject = f"🚀 AI Weekly: Big News + {len(content.examples)} Practical AI Tips"
        else:
            subject = f"💡 AI Weekly: {len(content.examples)} Actionable AI Examples This Week"

        with self.db._get_connection() as conn:
            with conn.cursor() as cur:
                # Insert issue
                cur.execute("""
                    INSERT INTO newsletter_issues
                    (issue_date, subject_line, big_news_summary, generated_at)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (
                    content.generation_date.date(),
                    subject,
                    content.big_news,
                    content.generation_date
                ))
                issue_id = cur.fetchone()[0]

                # Insert examples
                for i, ex in enumerate(content.examples, 1):
                    cur.execute("""
                        INSERT INTO newsletter_examples
                        (issue_id, position, title, description, how_to_replicate,
                         source_episode_id, source_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        issue_id,
                        i,
                        ex.title,
                        ex.description,
                        ex.how_to_replicate,
                        ex.source_episode_id,
                        ex.source_url
                    ))

                conn.commit()
                logger.info(f"Saved newsletter issue {issue_id} with {len(content.examples)} examples")

                return issue_id

    def cleanup_old_newsletters(self, keep_count: int = 20) -> int:
        """
        Delete old newsletters, keeping only the most recent N issues.

        Args:
            keep_count: Number of recent newsletters to keep (default 20)

        Returns:
            Number of issues deleted
        """
        import psycopg2

        with self.db._get_connection() as conn:
            with conn.cursor() as cur:
                # Find IDs to delete (older than the most recent N)
                cur.execute("""
                    SELECT id FROM newsletter_issues
                    ORDER BY issue_date DESC, id DESC
                    OFFSET %s
                """, (keep_count,))
                old_ids = [row[0] for row in cur.fetchall()]

                if not old_ids:
                    logger.info(f"No newsletters to delete (have {keep_count} or fewer)")
                    return 0

                # Delete examples first (foreign key constraint)
                cur.execute("""
                    DELETE FROM newsletter_examples
                    WHERE issue_id = ANY(%s)
                """, (old_ids,))

                # Delete survey responses for those examples
                cur.execute("""
                    DELETE FROM survey_responses
                    WHERE example_id NOT IN (SELECT id FROM newsletter_examples)
                """)

                # Delete issues
                cur.execute("""
                    DELETE FROM newsletter_issues
                    WHERE id = ANY(%s)
                """, (old_ids,))

                conn.commit()
                logger.info(f"Deleted {len(old_ids)} old newsletter issues (keeping {keep_count})")

                return len(old_ids)
