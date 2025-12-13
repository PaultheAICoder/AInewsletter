"""
Newsletter Content Generator

Generates weekly AI newsletter using:
1. Story arcs from topic tracking (evolving narratives)
2. Actionable examples from episode transcripts
3. Tone guidance from newsletter-vibes.md

Structure:
- Big Stories: Evolving narratives from our story arc tracking
- Practical Applications: Categorized by functional area
  - Code/Development
  - Marketing & Sales
  - Operations (IT/Finance/HR/Legal)
  - Productivity
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Import story arc patterns
from src.topic_tracking.topic_extractor import STORY_ARC_PATTERNS


# Functional area categories for organizing practical tips
FUNCTIONAL_AREAS = {
    "code_development": {
        "name": "Code & Development",
        "emoji": "💻",
        "keywords": ["code", "coding", "developer", "engineering", "api", "programming", "software", "github", "debug"]
    },
    "marketing_sales": {
        "name": "Marketing & Sales",
        "emoji": "📣",
        "keywords": ["marketing", "sales", "content", "social media", "campaign", "outreach", "customer", "brand"]
    },
    "operations": {
        "name": "Operations",
        "emoji": "⚙️",
        "keywords": ["finance", "hr", "legal", "compliance", "process", "workflow", "automat", "spreadsheet", "report"]
    },
    "productivity": {
        "name": "Productivity",
        "emoji": "🚀",
        "keywords": ["productivity", "assistant", "personal", "organize", "calendar", "task", "note", "research"]
    }
}


@dataclass
class StoryArc:
    """An evolving story we're tracking."""
    arc_id: str
    title: str
    summary: str
    key_developments: List[str]
    why_it_matters: str


@dataclass
class PracticalTip:
    """An actionable AI application."""
    title: str
    description: str
    how_to_replicate: str
    why_useful: str
    functional_area: str
    source_episode_id: int
    source_title: str


@dataclass
class NewsletterContent:
    """Generated newsletter content."""
    story_arcs: List[StoryArc]
    practical_tips: List[PracticalTip]
    generation_date: datetime
    episodes_analyzed: int
    intro_hook: str = ""


class NewsletterGenerator:
    """
    Generates newsletter content combining story arcs and practical tips.

    Tone guidance (from newsletter-vibes.md):
    - Pragmatic optimism with urgency
    - Ground technical concepts in relatable scenarios
    - Oscillate between accessible and sophisticated
    - Emphasize AI for complex workflows, not just automation
    """

    # Minimum AI score to include episode in analysis
    MIN_AI_SCORE = 0.7

    # Maximum items per section
    MAX_STORY_ARCS = 3
    MAX_TIPS_PER_CATEGORY = 2

    def __init__(self, db_client):
        """Initialize the generator."""
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key, timeout=120.0)
        self.db = db_client

        # Load model from web_settings
        self.model = db_client.get_setting('ai_digest_generation', 'model', 'gpt-4o')

        logger.info(f"NewsletterGenerator initialized with model={self.model}")

    def get_active_story_arcs(self, days: int = 7) -> List[Dict]:
        """
        Get story arcs that have activity in the past N days.

        Returns topics grouped by story arc with their key points.
        """
        topics = self.db.get_recent_episode_topics(
            digest_topic='AI and Technology',
            days=days
        )

        # Group topics by story arc
        arc_topics = {}
        for topic in topics:
            text = (topic.get('topic_name', '') + ' ' +
                   ' '.join(topic.get('key_points', []) or [])).lower()

            for arc_id, patterns in STORY_ARC_PATTERNS.items():
                for pattern in patterns:
                    if pattern in text:
                        if arc_id not in arc_topics:
                            arc_topics[arc_id] = []
                        arc_topics[arc_id].append(topic)
                        break

        return arc_topics

    def get_recent_episodes(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get episodes from the past N days with high AI scores."""
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

    def _create_story_arc_prompt(self, arc_topics: Dict[str, List[Dict]]) -> str:
        """Create prompt to summarize story arcs."""

        arc_descriptions = []
        for arc_id, topics in arc_topics.items():
            if not topics:
                continue

            # Collect all key points for this arc
            all_points = []
            topic_names = []
            for t in topics:
                topic_names.append(t.get('topic_name', ''))
                all_points.extend(t.get('key_points', []) or [])

            arc_descriptions.append(f"""
Story Arc: {arc_id.replace('-', ' ').title()}
Related Topics: {', '.join(set(topic_names))}
Key Developments:
{chr(10).join(f'- {p}' for p in all_points[:8])}
""")

        prompt = f"""You are writing the "Big Stories" section of a weekly AI newsletter.

TONE GUIDANCE:
- Pragmatic optimism with urgency - acknowledge opportunities most are missing
- Ground technical concepts in relatable business scenarios
- Shift the narrative from "AI automates boring tasks" to "AI scales human judgment"
- Strategic mentorship, not marketing fluff

Here are the story arcs we've been tracking this week:

{''.join(arc_descriptions)}

For each major story (pick the 2-3 most significant), create:
1. A compelling title that captures the narrative
2. A 2-3 sentence summary of what happened and why it matters
3. 3-4 key developments as bullet points (use the key points provided)
4. One sentence on why this matters for business professionals

Return as JSON:
{{
    "story_arcs": [
        {{
            "arc_id": "gpt-5-release",
            "title": "GPT-5.2 Arrives: OpenAI's Bet on Business Value Over Benchmarks",
            "summary": "Summary here...",
            "key_developments": ["point 1", "point 2", "point 3"],
            "why_it_matters": "Why this matters..."
        }}
    ]
}}
"""
        return prompt

    def _create_practical_tips_prompt(self, episodes: List[Dict]) -> str:
        """Create prompt for extracting practical tips categorized by function."""

        episode_texts = []
        for i, ep in enumerate(episodes[:8], 1):
            transcript = ep.get('transcript_content', '')[:6000]
            episode_texts.append(f"""
--- EPISODE {i} ---
Title: {ep['title']}
Source: {ep['feed_title']}
Episode ID: {ep['id']}
Transcript excerpt:
{transcript}
""")

        prompt = f"""You are extracting practical AI applications for a newsletter.

TONE GUIDANCE:
- Focus on transformational use cases that codify expertise, not just automation
- Ground examples in real business scenarios
- Emphasize human + AI collaboration
- Be specific enough to replicate

AUDIENCE: Mixed professionals (engineering/consumer electronics testing, marketing/social, operations)

FUNCTIONAL CATEGORIES:
1. Code & Development - Technical tools, coding assistants, developer workflows
2. Marketing & Sales - Content, outreach, customer engagement
3. Operations - Process automation, compliance, finance/HR/legal
4. Productivity - Cross-functional tools, personal AI assistants

Episodes to analyze:
{''.join(episode_texts)}

Extract 6-8 practical AI applications. For each:
- Categorize into one of the functional areas
- Make it specific and actionable
- Focus on complex workflows, not basic automation

Return as JSON:
{{
    "practical_tips": [
        {{
            "title": "Catchy, specific title",
            "description": "What it does and why interesting (2-3 sentences)",
            "how_to_replicate": "Step 1: ... Step 2: ... Step 3: ...",
            "why_useful": "Who benefits and what problem it solves",
            "functional_area": "code_development|marketing_sales|operations|productivity",
            "source_episode_id": 123
        }}
    ],
    "intro_hook": "One compelling sentence to open the newsletter that captures this week's theme"
}}
"""
        return prompt

    def generate_content(self, days: int = 7) -> Optional[NewsletterContent]:
        """Generate newsletter content from story arcs and episodes."""
        logger.info(f"Generating newsletter content for past {days} days")

        # Get story arcs
        arc_topics = self.get_active_story_arcs(days)
        logger.info(f"Found {len(arc_topics)} active story arcs")

        # Get recent episodes for practical tips
        episodes = self.get_recent_episodes(days)
        logger.info(f"Found {len(episodes)} episodes to analyze")

        if not arc_topics and not episodes:
            logger.warning("No story arcs or episodes found")
            return None

        episode_lookup = {ep['id']: ep for ep in episodes}

        story_arcs = []
        practical_tips = []
        intro_hook = ""

        # Generate story arc summaries
        if arc_topics:
            try:
                prompt = self._create_story_arc_prompt(arc_topics)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_completion_tokens=2000,
                    temperature=0.7
                )
                result = json.loads(response.choices[0].message.content)

                for arc in result.get('story_arcs', [])[:self.MAX_STORY_ARCS]:
                    story_arcs.append(StoryArc(
                        arc_id=arc.get('arc_id', ''),
                        title=arc.get('title', ''),
                        summary=arc.get('summary', ''),
                        key_developments=arc.get('key_developments', []),
                        why_it_matters=arc.get('why_it_matters', '')
                    ))

                logger.info(f"Generated {len(story_arcs)} story arc summaries")
            except Exception as e:
                logger.error(f"Failed to generate story arcs: {e}")

        # Generate practical tips
        if episodes:
            try:
                prompt = self._create_practical_tips_prompt(episodes)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_completion_tokens=3000,
                    temperature=0.7
                )
                result = json.loads(response.choices[0].message.content)

                intro_hook = result.get('intro_hook', '')

                for tip in result.get('practical_tips', []):
                    source_id = tip.get('source_episode_id')
                    source_ep = episode_lookup.get(source_id, {})

                    practical_tips.append(PracticalTip(
                        title=tip.get('title', ''),
                        description=tip.get('description', ''),
                        how_to_replicate=tip.get('how_to_replicate', ''),
                        why_useful=tip.get('why_useful', ''),
                        functional_area=tip.get('functional_area', 'productivity'),
                        source_episode_id=source_id,
                        source_title=source_ep.get('title', 'Unknown')
                    ))

                logger.info(f"Generated {len(practical_tips)} practical tips")
            except Exception as e:
                logger.error(f"Failed to generate practical tips: {e}")

        content = NewsletterContent(
            story_arcs=story_arcs,
            practical_tips=practical_tips,
            generation_date=datetime.now(timezone.utc),
            episodes_analyzed=len(episodes),
            intro_hook=intro_hook
        )

        return content

    def render_html(self, content: NewsletterContent) -> str:
        """Render newsletter content as HTML."""

        # Group tips by functional area
        tips_by_area = {}
        for tip in content.practical_tips:
            area = tip.functional_area
            if area not in tips_by_area:
                tips_by_area[area] = []
            tips_by_area[area].append(tip)

        # Build story arcs HTML
        story_arcs_html = ""
        for arc in content.story_arcs:
            developments = "\n".join(f"<li>{d}</li>" for d in arc.key_developments)
            story_arcs_html += f"""
            <div style="margin-bottom: 24px; padding: 16px; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #0066cc;">
                <h3 style="margin: 0 0 8px 0; color: #0066cc;">{arc.title}</h3>
                <p style="margin: 0 0 12px 0; color: #333;">{arc.summary}</p>
                <ul style="margin: 0 0 12px 0; padding-left: 20px; color: #555;">
                    {developments}
                </ul>
                <p style="margin: 0; font-style: italic; color: #666;"><strong>Why it matters:</strong> {arc.why_it_matters}</p>
            </div>
            """

        # Build practical tips HTML by category
        tips_html = ""
        for area_id, area_info in FUNCTIONAL_AREAS.items():
            area_tips = tips_by_area.get(area_id, [])
            if not area_tips:
                continue

            tips_content = ""
            for tip in area_tips[:self.MAX_TIPS_PER_CATEGORY]:
                tips_content += f"""
                <div style="margin-bottom: 20px; padding: 12px; background: white; border: 1px solid #e0e0e0; border-radius: 6px;">
                    <h4 style="margin: 0 0 8px 0; color: #333;">{tip.title}</h4>
                    <p style="margin: 0 0 8px 0; color: #555;">{tip.description}</p>
                    <p style="margin: 0 0 8px 0; color: #666; font-size: 14px;"><strong>How to try it:</strong> {tip.how_to_replicate}</p>
                    <p style="margin: 0; color: #888; font-size: 13px;"><em>Source: {tip.source_title}</em></p>
                </div>
                """

            tips_html += f"""
            <div style="margin-bottom: 24px;">
                <h3 style="margin: 0 0 12px 0; color: #333;">{area_info['emoji']} {area_info['name']}</h3>
                {tips_content}
            </div>
            """

        # Full HTML
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>AI Weekly Newsletter</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 680px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333;">

    <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="margin: 0; color: #0066cc;">🤖 AI Weekly</h1>
        <p style="margin: 8px 0 0 0; color: #666;">{content.generation_date.strftime('%B %d, %Y')}</p>
    </div>

    {f'<p style="font-size: 18px; color: #333; margin-bottom: 24px; text-align: center; font-style: italic;">{content.intro_hook}</p>' if content.intro_hook else ''}

    <div style="margin-bottom: 32px;">
        <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 8px;">🔥 Big Stories This Week</h2>
        {story_arcs_html if story_arcs_html else '<p style="color: #666;">No major story developments this week.</p>'}
    </div>

    <div style="margin-bottom: 32px;">
        <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 8px;">💡 Practical Applications</h2>
        <p style="color: #666; margin-bottom: 16px;">AI isn't just for automating boring tasks—it's for codifying complex workflows and scaling human judgment. Here's how people are putting it to work:</p>
        {tips_html if tips_html else '<p style="color: #666;">No practical tips this week.</p>'}
    </div>

    <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e0e0e0; text-align: center; color: #888; font-size: 13px;">
        <p>Generated from {content.episodes_analyzed} episodes analyzed this week.</p>
        <p>Questions? Reply to this email or reach out to the AI team.</p>
    </div>

</body>
</html>
"""
        return html

    def save_newsletter(self, content: NewsletterContent) -> int:
        """Save generated newsletter to database and HTML file."""
        import psycopg2

        # Create subject line
        if content.story_arcs:
            top_story = content.story_arcs[0].title[:50]
            subject = f"🤖 AI Weekly: {top_story}..."
        else:
            subject = f"💡 AI Weekly: {len(content.practical_tips)} Practical AI Applications"

        # Generate big news summary from story arcs
        big_news = None
        if content.story_arcs:
            big_news = " | ".join(arc.title for arc in content.story_arcs)

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
                    big_news,
                    content.generation_date
                ))
                issue_id = cur.fetchone()[0]

                # Insert practical tips as examples
                position = 1
                for tip in content.practical_tips:
                    cur.execute("""
                        INSERT INTO newsletter_examples
                        (issue_id, position, title, description, how_to_replicate,
                         source_episode_id, source_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        issue_id,
                        position,
                        f"[{FUNCTIONAL_AREAS.get(tip.functional_area, {}).get('name', 'General')}] {tip.title}",
                        tip.description,
                        tip.how_to_replicate,
                        tip.source_episode_id,
                        ""  # source_url not available
                    ))
                    position += 1

                conn.commit()
                logger.info(f"Saved newsletter issue {issue_id}")

        # Save HTML copy
        html_content = self.render_html(content)
        html_dir = Path(__file__).parent.parent.parent / 'logs' / 'newsletters'
        html_dir.mkdir(parents=True, exist_ok=True)

        html_file = html_dir / f"newsletter_{issue_id}_{content.generation_date.strftime('%Y%m%d')}.html"
        html_file.write_text(html_content)
        logger.info(f"Saved HTML to {html_file}")

        return issue_id

    def cleanup_old_newsletters(self, keep_count: int = 20) -> int:
        """Delete old newsletters, keeping only the most recent N issues."""
        import psycopg2

        with self.db._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id FROM newsletter_issues
                    ORDER BY issue_date DESC, id DESC
                    OFFSET %s
                """, (keep_count,))
                old_ids = [row[0] for row in cur.fetchall()]

                if not old_ids:
                    logger.info(f"No newsletters to delete (have {keep_count} or fewer)")
                    return 0

                cur.execute("""
                    DELETE FROM newsletter_examples
                    WHERE issue_id = ANY(%s)
                """, (old_ids,))

                cur.execute("""
                    DELETE FROM newsletter_issues
                    WHERE id = ANY(%s)
                """, (old_ids,))

                conn.commit()
                logger.info(f"Deleted {len(old_ids)} old newsletter issues")

                return len(old_ids)
