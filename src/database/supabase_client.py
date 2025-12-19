"""
Supabase Database Client

Handles all database operations for the YouTube transcript pipeline.
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class SupabaseClient:
    """Client for Supabase PostgreSQL database operations."""

    def __init__(self):
        """Initialize database connection."""
        self.database_url = os.getenv('DATABASE_URL')
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable not set")

    def _get_connection(self):
        """Get a database connection."""
        return psycopg2.connect(self.database_url)

    def get_youtube_feeds(self) -> List[Dict[str, Any]]:
        """
        Get all YouTube feeds from the database.

        Returns:
            List of feed dictionaries with id, title, feed_url
        """
        query = """
            SELECT id, title, feed_url
            FROM feeds
            WHERE feed_url LIKE '%youtube.com/feeds/videos.xml%'
            ORDER BY id
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                feeds = cur.fetchall()
                return [dict(f) for f in feeds]

    def get_setting(self, category: str, key: str, default: Any = None) -> Any:
        """
        Get a setting from web_settings table.

        Args:
            category: Setting category
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        query = """
            SELECT setting_value, value_type
            FROM web_settings
            WHERE category = %s AND setting_key = %s
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (category, key))
                row = cur.fetchone()

                if not row:
                    return default

                value = row['setting_value']
                value_type = row['value_type']

                # Convert based on type
                if value_type == 'int':
                    return int(value)
                elif value_type == 'float':
                    return float(value)
                elif value_type == 'bool':
                    return value.lower() in ('true', '1', 'yes')
                else:
                    return value

    def get_existing_episode_guids(self, feed_id: int) -> set:
        """
        Get all existing episode GUIDs for a feed.

        Args:
            feed_id: Feed ID

        Returns:
            Set of episode GUIDs
        """
        query = """
            SELECT episode_guid
            FROM episodes
            WHERE feed_id = %s
        """

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (feed_id,))
                return {row[0] for row in cur.fetchall()}

    def get_active_topics(self) -> List[Dict[str, Any]]:
        """
        Get all active topics for scoring.

        Returns:
            List of topic dictionaries
        """
        query = """
            SELECT id, slug, name, description
            FROM topics
            WHERE is_active = true
            ORDER BY sort_order, id
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                topics = cur.fetchall()
                return [dict(t) for t in topics]

    def get_topics_with_tracking_enabled(self) -> List[Dict[str, Any]]:
        """
        Get topics that have topic tracking enabled (like podscrape2).

        Returns:
            List of topic dictionaries with tracking enabled
        """
        query = """
            SELECT id, slug, name, description, enable_topic_tracking
            FROM topics
            WHERE is_active = true AND enable_topic_tracking = true
            ORDER BY sort_order, id
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                topics = cur.fetchall()
                return [dict(t) for t in topics]

    def create_episode(
        self,
        episode_guid: str,
        feed_id: int,
        title: str,
        published_date: datetime,
        video_url: str,
        duration_seconds: Optional[int],
        description: Optional[str],
        transcript_content: str,
        transcript_word_count: int,
        status: str = 'transcribed'
    ) -> int:
        """
        Create a new episode record.

        Args:
            episode_guid: Unique identifier (video ID for YouTube)
            feed_id: Associated feed ID
            title: Video title
            published_date: When video was published
            video_url: YouTube video URL
            duration_seconds: Video duration
            description: Video description
            transcript_content: Full transcript text
            transcript_word_count: Word count
            status: Episode status

        Returns:
            Created episode ID
        """
        query = """
            INSERT INTO episodes (
                episode_guid, feed_id, title, published_date, audio_url,
                duration_seconds, description, transcript_content,
                transcript_word_count, transcript_generated_at, status,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
        """

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (
                    episode_guid,
                    feed_id,
                    title,
                    published_date,
                    video_url,  # Using audio_url field for video URL
                    duration_seconds,
                    description,
                    transcript_content,
                    transcript_word_count,
                    now,  # transcript_generated_at
                    status,
                    now,  # created_at
                    now   # updated_at
                ))
                episode_id = cur.fetchone()[0]
                conn.commit()
                return episode_id

    def update_episode_scores(
        self,
        episode_guid: str,
        scores: Dict[str, float],
        status: str
    ) -> None:
        """
        Update episode with scores.

        Args:
            episode_guid: Episode GUID
            scores: Dictionary of topic scores
            status: New status ('scored' or 'not_relevant')
        """
        import json

        query = """
            UPDATE episodes
            SET scores = %s, scored_at = %s, status = %s, updated_at = %s
            WHERE episode_guid = %s
        """

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (
                    json.dumps(scores),
                    now,
                    status,
                    now,
                    episode_guid
                ))
                conn.commit()

    def update_episode_failed(
        self,
        episode_guid: str,
        error_message: str
    ) -> None:
        """
        Mark an episode as failed.

        Args:
            episode_guid: Episode GUID
            error_message: Failure reason
        """
        query = """
            UPDATE episodes
            SET status = 'failed', failure_reason = %s,
                failure_count = COALESCE(failure_count, 0) + 1,
                last_failure_at = %s, updated_at = %s
            WHERE episode_guid = %s
        """

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (error_message, now, now, episode_guid))
                conn.commit()

    def episode_exists(self, episode_guid: str) -> bool:
        """Check if an episode already exists."""
        query = "SELECT 1 FROM episodes WHERE episode_guid = %s"

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (episode_guid,))
                return cur.fetchone() is not None

    def get_episode_by_guid(self, episode_guid: str) -> Optional[Dict[str, Any]]:
        """
        Get episode by GUID.

        Args:
            episode_guid: Episode GUID

        Returns:
            Episode dictionary or None
        """
        query = """
            SELECT id, episode_guid, feed_id, title, published_date,
                   audio_url, duration_seconds, description,
                   transcript_content, transcript_word_count,
                   scores, scored_at, status
            FROM episodes
            WHERE episode_guid = %s
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (episode_guid,))
                row = cur.fetchone()
                return dict(row) if row else None

    # ==================== Episode Topics Methods ====================

    def store_episode_topic(
        self,
        episode_id: int,
        topic_name: str,
        topic_slug: str,
        key_points: List[str],
        digest_topic: str,
        relevance_score: float,
        topic_type: str = 'other',
        novelty_score: float = 1.0,
        is_update: bool = False,
        parent_topic_id: Optional[int] = None,
        evolution_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Store or update topic for episode.
        Uses UPSERT pattern to handle re-scoring scenarios.

        Args:
            episode_id: Episode database ID
            topic_name: Human-readable topic name
            topic_slug: Normalized slug for deduplication
            key_points: List of key insights (2-4 items)
            digest_topic: Parent topic (e.g., "AI and Technology")
            relevance_score: Episode's relevance score for digest_topic
            topic_type: Classification (model_release, use_case, etc.)
            novelty_score: 0.0-1.0 novelty score
            is_update: Whether this is an update to existing topic
            parent_topic_id: ID of parent topic if this is an update
            evolution_summary: What changed since parent

        Returns:
            The created or updated topic record as dictionary
        """
        now = datetime.now(timezone.utc)

        # Check if topic already exists
        check_query = """
            SELECT id, mention_count FROM episode_topics
            WHERE episode_id = %s AND topic_slug = %s
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(check_query, (episode_id, topic_slug))
                existing = cur.fetchone()

                if existing:
                    # Update existing record
                    update_query = """
                        UPDATE episode_topics
                        SET topic_name = %s, key_points = %s, last_mentioned_at = %s,
                            updated_at = %s, mention_count = %s, topic_type = %s,
                            novelty_score = %s, is_update = %s, parent_topic_id = %s,
                            evolution_summary = %s
                        WHERE id = %s
                        RETURNING id, episode_id, topic_name, topic_slug, key_points,
                                  first_mentioned_at, last_mentioned_at, mention_count,
                                  digest_topic, relevance_score, topic_type, novelty_score,
                                  is_update, parent_topic_id, evolution_summary
                    """
                    cur.execute(update_query, (
                        topic_name, key_points, now, now,
                        existing['mention_count'] + 1,
                        topic_type, novelty_score, is_update, parent_topic_id,
                        evolution_summary, existing['id']
                    ))
                else:
                    # Create new record
                    insert_query = """
                        INSERT INTO episode_topics (
                            episode_id, topic_name, topic_slug, key_points,
                            first_mentioned_at, last_mentioned_at, mention_count,
                            digest_topic, relevance_score, topic_type, novelty_score,
                            is_update, parent_topic_id, evolution_summary, first_seen_at,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING id, episode_id, topic_name, topic_slug, key_points,
                                  first_mentioned_at, last_mentioned_at, mention_count,
                                  digest_topic, relevance_score, topic_type, novelty_score,
                                  is_update, parent_topic_id, evolution_summary
                    """
                    cur.execute(insert_query, (
                        episode_id, topic_name, topic_slug, key_points,
                        now, now, digest_topic, relevance_score,
                        topic_type, novelty_score, is_update, parent_topic_id,
                        evolution_summary, now, now, now
                    ))

                result = cur.fetchone()
                conn.commit()
                return dict(result)

    def get_recent_episode_topics(
        self,
        digest_topic: str,
        days: int = 14
    ) -> List[Dict[str, Any]]:
        """
        Get recent topics for a digest topic.
        Used for novelty detection comparison.

        Args:
            digest_topic: Parent topic name (e.g., "AI and Technology")
            days: How many days of history to retrieve

        Returns:
            List of topic dictionaries
        """
        query = """
            SELECT id, episode_id, topic_name, topic_slug, key_points,
                   first_mentioned_at, last_mentioned_at, mention_count,
                   digest_topic, relevance_score, topic_type, novelty_score,
                   is_update, parent_topic_id, evolution_summary
            FROM episode_topics
            WHERE digest_topic = %s
              AND last_mentioned_at >= NOW() - INTERVAL '%s days'
            ORDER BY last_mentioned_at DESC
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (digest_topic, days))
                topics = cur.fetchall()
                return [dict(t) for t in topics]

    def get_topics_for_episode(
        self,
        episode_id: int,
        digest_topic: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all topics extracted from a specific episode.

        Args:
            episode_id: Episode database ID
            digest_topic: Optional filter by digest topic

        Returns:
            List of topic dictionaries
        """
        if digest_topic:
            query = """
                SELECT id, episode_id, topic_name, topic_slug, key_points,
                       first_mentioned_at, last_mentioned_at, mention_count,
                       digest_topic, relevance_score, topic_type, novelty_score,
                       is_update, parent_topic_id, evolution_summary
                FROM episode_topics
                WHERE episode_id = %s AND digest_topic = %s
            """
            params = (episode_id, digest_topic)
        else:
            query = """
                SELECT id, episode_id, topic_name, topic_slug, key_points,
                       first_mentioned_at, last_mentioned_at, mention_count,
                       digest_topic, relevance_score, topic_type, novelty_score,
                       is_update, parent_topic_id, evolution_summary
                FROM episode_topics
                WHERE episode_id = %s
            """
            params = (episode_id,)

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                topics = cur.fetchall()
                return [dict(t) for t in topics]

    def get_topic_stats(
        self,
        digest_topic: str,
        days_back: int = 14
    ) -> Dict[str, Any]:
        """
        Get statistics about topics for a digest topic.

        Args:
            digest_topic: Parent topic name
            days_back: Time window for statistics

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Total topics
                cur.execute("""
                    SELECT COUNT(*) as count FROM episode_topics
                    WHERE digest_topic = %s
                      AND last_mentioned_at >= NOW() - INTERVAL '%s days'
                """, (digest_topic, days_back))
                total_topics = cur.fetchone()['count']

                # Topics included in digests
                cur.execute("""
                    SELECT COUNT(*) as count FROM episode_topics
                    WHERE digest_topic = %s
                      AND last_mentioned_at >= NOW() - INTERVAL '%s days'
                      AND included_in_digest_id IS NOT NULL
                """, (digest_topic, days_back))
                included_topics = cur.fetchone()['count']

                # Most mentioned topics
                cur.execute("""
                    SELECT topic_slug, topic_name, SUM(mention_count) as mentions
                    FROM episode_topics
                    WHERE digest_topic = %s
                      AND last_mentioned_at >= NOW() - INTERVAL '%s days'
                    GROUP BY topic_slug, topic_name
                    ORDER BY mentions DESC
                    LIMIT 10
                """, (digest_topic, days_back))
                top_topics = cur.fetchall()

                return {
                    "total_topics": total_topics,
                    "included_topics": included_topics,
                    "pending_topics": total_topics - included_topics,
                    "top_topics": [
                        {
                            "slug": t['topic_slug'],
                            "name": t['topic_name'],
                            "mentions": t['mentions'],
                        }
                        for t in top_topics
                    ],
                }

    def delete_episode_topic(self, topic_id: int) -> bool:
        """
        Delete an episode topic by ID.

        Args:
            topic_id: Topic database ID

        Returns:
            True if deleted, False otherwise
        """
        query = "DELETE FROM episode_topics WHERE id = %s"

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (topic_id,))
                deleted = cur.rowcount > 0
                conn.commit()
                return deleted

    def update_episode_topic_key_points(
        self,
        topic_id: int,
        key_points: List[str]
    ) -> None:
        """
        Update key points for an episode topic.

        Args:
            topic_id: Topic database ID
            key_points: New list of key points
        """
        from datetime import datetime, timezone

        query = """
            UPDATE episode_topics
            SET key_points = %s, updated_at = %s
            WHERE id = %s
        """

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (key_points, now, topic_id))
                conn.commit()

    # ==================== Pipeline Run Logging ====================

    def log_pipeline_run(
        self,
        run_id: str,
        workflow_name: str,
        status: str,
        conclusion: str = None,
        started_at: datetime = None,
        finished_at: datetime = None,
        phase: Dict = None,
        notes: str = None,
        trigger: str = 'cron'
    ) -> None:
        """
        Log a pipeline run to the database.

        Args:
            run_id: Unique identifier for the run
            workflow_name: Name of the workflow (e.g., 'topic_deduplication')
            status: Current status ('running', 'completed', 'failed')
            conclusion: Final conclusion ('success', 'failure', 'cancelled')
            started_at: When the run started
            finished_at: When the run finished
            phase: JSON data with phase-specific details
            notes: Any additional notes or error messages
            trigger: What triggered the run ('cron', 'manual', etc.)
        """
        import json

        query = """
            INSERT INTO pipeline_runs (
                id, workflow_name, trigger, status, conclusion,
                started_at, finished_at, phase, notes, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                conclusion = EXCLUDED.conclusion,
                finished_at = EXCLUDED.finished_at,
                phase = EXCLUDED.phase,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
        """

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (
                    run_id,
                    workflow_name,
                    trigger,
                    status,
                    conclusion,
                    started_at or now,
                    finished_at,
                    json.dumps(phase) if phase else None,
                    notes,
                    now,
                    now
                ))
                conn.commit()

    def get_recent_pipeline_runs(
        self,
        workflow_name: str = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get recent pipeline runs.

        Args:
            workflow_name: Filter by workflow name (optional)
            limit: Maximum number of runs to return

        Returns:
            List of pipeline run dictionaries
        """
        if workflow_name:
            query = """
                SELECT id, workflow_name, trigger, status, conclusion,
                       started_at, finished_at, phase, notes
                FROM pipeline_runs
                WHERE workflow_name = %s
                ORDER BY started_at DESC
                LIMIT %s
            """
            params = (workflow_name, limit)
        else:
            query = """
                SELECT id, workflow_name, trigger, status, conclusion,
                       started_at, finished_at, phase, notes
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT %s
            """
            params = (limit,)

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                runs = cur.fetchall()
                return [dict(r) for r in runs]

    # ==================== Story Arc Methods ====================

    def _normalize_arc_slug(self, arc_name: str) -> str:
        """
        Normalize story arc name to a slug for matching.

        Args:
            arc_name: The story arc name

        Returns:
            Normalized slug
        """
        import re
        # Lowercase, replace spaces/special chars with hyphens, remove duplicates
        slug = arc_name.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        slug = slug.strip('-')
        return slug[:255]  # Limit length

    def get_active_story_arcs(
        self,
        digest_topic: str,
        days: int = None
    ) -> List[Dict[str, Any]]:
        """
        Get active story arcs for a digest topic within retention window.

        Args:
            digest_topic: Parent topic name (e.g., "AI and Technology")
            days: Override retention days (defaults to web_setting)

        Returns:
            List of story arc dictionaries with their events
        """
        if days is None:
            days = self.get_setting('story_arcs', 'retention_days', 14)

        query = """
            SELECT sa.id, sa.arc_name, sa.arc_slug, sa.functional_category,
                   sa.digest_topic, sa.summary, sa.started_at, sa.last_updated_at,
                   sa.event_count, sa.source_count, sa.included_in_digest_id,
                   sa.included_at, sa.created_at, sa.updated_at
            FROM story_arcs sa
            WHERE sa.digest_topic = %s
              AND sa.last_updated_at >= NOW() - INTERVAL '%s days'
            ORDER BY sa.last_updated_at DESC
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (digest_topic, days))
                arcs = [dict(a) for a in cur.fetchall()]

                # Get events for each arc
                for arc in arcs:
                    events_query = """
                        SELECT id, story_arc_id, event_date, event_summary,
                               key_points, source_feed_id, source_episode_id,
                               source_episode_guid, source_name, perspective,
                               relevance_score, extracted_at
                        FROM story_arc_events
                        WHERE story_arc_id = %s
                        ORDER BY event_date ASC
                    """
                    cur.execute(events_query, (arc['id'],))
                    arc['events'] = [dict(e) for e in cur.fetchall()]

                return arcs

    def find_story_arc_by_slug(
        self,
        arc_slug: str,
        digest_topic: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find a story arc by its slug and digest topic.

        Args:
            arc_slug: Normalized arc slug
            digest_topic: Parent topic name

        Returns:
            Story arc dictionary or None
        """
        query = """
            SELECT id, arc_name, arc_slug, functional_category,
                   digest_topic, summary, started_at, last_updated_at,
                   event_count, source_count
            FROM story_arcs
            WHERE arc_slug = %s AND digest_topic = %s
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (arc_slug, digest_topic))
                row = cur.fetchone()
                return dict(row) if row else None

    def create_story_arc(
        self,
        arc_name: str,
        digest_topic: str,
        functional_category: str = 'other',
        initial_event: Dict = None
    ) -> Dict[str, Any]:
        """
        Create a new story arc, optionally with an initial event.

        Args:
            arc_name: Human-readable arc name
            digest_topic: Parent topic (e.g., "AI and Technology")
            functional_category: Classification (model_release, company_strategy, etc.)
            initial_event: Optional dict with event_date, event_summary, key_points, etc.

        Returns:
            Created story arc dictionary
        """
        arc_slug = self._normalize_arc_slug(arc_name)
        now = datetime.now(timezone.utc)

        # Check if arc already exists
        existing = self.find_story_arc_by_slug(arc_slug, digest_topic)
        if existing:
            logger.info(f"Story arc already exists: {arc_name} (id={existing['id']})")
            return existing

        insert_query = """
            INSERT INTO story_arcs (
                arc_name, arc_slug, functional_category, digest_topic,
                started_at, last_updated_at, event_count, source_count,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 0, 0, %s, %s
            )
            RETURNING id, arc_name, arc_slug, functional_category, digest_topic,
                      summary, started_at, last_updated_at, event_count, source_count
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(insert_query, (
                    arc_name, arc_slug, functional_category, digest_topic,
                    now, now, now, now
                ))
                arc = dict(cur.fetchone())
                conn.commit()

                # Add initial event if provided
                if initial_event:
                    self.add_story_arc_event(
                        story_arc_id=arc['id'],
                        event_date=initial_event.get('event_date', now),
                        event_summary=initial_event['event_summary'],
                        key_points=initial_event.get('key_points', []),
                        source_feed_id=initial_event.get('source_feed_id'),
                        source_episode_id=initial_event.get('source_episode_id'),
                        source_episode_guid=initial_event.get('source_episode_guid'),
                        source_name=initial_event.get('source_name'),
                        perspective=initial_event.get('perspective'),
                        relevance_score=initial_event.get('relevance_score')
                    )

                logger.info(f"Created story arc: {arc_name} (id={arc['id']})")
                return arc

    def add_story_arc_event(
        self,
        story_arc_id: int,
        event_date: datetime,
        event_summary: str,
        key_points: List[str] = None,
        source_feed_id: int = None,
        source_episode_id: int = None,
        source_episode_guid: str = None,
        source_name: str = None,
        perspective: str = None,
        relevance_score: float = None
    ) -> Dict[str, Any]:
        """
        Add an event to a story arc timeline.

        Args:
            story_arc_id: Story arc database ID
            event_date: When the event occurred
            event_summary: 1-2 sentence description
            key_points: Supporting details
            source_feed_id: Source feed ID
            source_episode_id: Source episode ID
            source_episode_guid: Source episode GUID
            source_name: Feed/episode title for display
            perspective: positive, negative, neutral, analytical
            relevance_score: Episode's relevance score

        Returns:
            Created event dictionary
        """
        now = datetime.now(timezone.utc)
        max_events = self.get_setting('story_arcs', 'max_events_per_arc', 20)

        insert_query = """
            INSERT INTO story_arc_events (
                story_arc_id, event_date, event_summary, key_points,
                source_feed_id, source_episode_id, source_episode_guid,
                source_name, perspective, relevance_score, extracted_at, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, story_arc_id, event_date, event_summary, key_points,
                      source_feed_id, source_episode_id, source_episode_guid,
                      source_name, perspective, relevance_score, extracted_at
        """

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(insert_query, (
                    story_arc_id, event_date, event_summary,
                    key_points or [], source_feed_id, source_episode_id,
                    source_episode_guid, source_name, perspective,
                    relevance_score, now, now
                ))
                event = dict(cur.fetchone())

                # Update story arc's last_updated_at, event_count, source_count
                update_arc_query = """
                    UPDATE story_arcs
                    SET last_updated_at = %s,
                        event_count = (
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
                """
                cur.execute(update_arc_query, (
                    event_date, story_arc_id, story_arc_id, now, story_arc_id
                ))

                # Prune old events if over limit
                prune_query = """
                    DELETE FROM story_arc_events
                    WHERE id IN (
                        SELECT id FROM story_arc_events
                        WHERE story_arc_id = %s
                        ORDER BY event_date ASC
                        LIMIT GREATEST(0, (
                            SELECT COUNT(*) FROM story_arc_events
                            WHERE story_arc_id = %s
                        ) - %s)
                    )
                """
                cur.execute(prune_query, (story_arc_id, story_arc_id, max_events))

                conn.commit()
                return event

    def get_or_create_story_arc(
        self,
        arc_name: str,
        digest_topic: str,
        functional_category: str = 'other',
        initial_event: Dict = None
    ) -> Dict[str, Any]:
        """
        Get existing story arc or create new one.

        Args:
            arc_name: Human-readable arc name
            digest_topic: Parent topic
            functional_category: Classification
            initial_event: Event to add if creating new arc

        Returns:
            Story arc dictionary
        """
        arc_slug = self._normalize_arc_slug(arc_name)
        existing = self.find_story_arc_by_slug(arc_slug, digest_topic)

        if existing:
            return existing

        return self.create_story_arc(
            arc_name=arc_name,
            digest_topic=digest_topic,
            functional_category=functional_category,
            initial_event=initial_event
        )

    def get_story_arcs_for_prompt(
        self,
        digest_topic: str,
        max_arcs: int = 15,
        max_events_per_arc: int = 5
    ) -> str:
        """
        Generate formatted story arcs for inclusion in extraction prompt.

        Args:
            digest_topic: Parent topic name
            max_arcs: Maximum arcs to include
            max_events_per_arc: Maximum events per arc to show

        Returns:
            Formatted string describing active story arcs
        """
        arcs = self.get_active_story_arcs(digest_topic)

        if not arcs:
            return ""

        lines = []
        for i, arc in enumerate(arcs[:max_arcs], 1):
            lines.append(f"\n--- STORY ARC {i}: {arc['arc_name']} ---")
            lines.append(f"Category: {arc['functional_category']}")
            lines.append(f"Started: {arc['started_at'].strftime('%Y-%m-%d') if arc['started_at'] else 'Unknown'}")
            lines.append(f"Last update: {arc['last_updated_at'].strftime('%Y-%m-%d') if arc['last_updated_at'] else 'Unknown'}")
            lines.append(f"Sources: {arc['source_count']} feeds")
            lines.append("Timeline:")

            events = arc.get('events', [])
            for event in events[-max_events_per_arc:]:  # Most recent events
                event_date = event['event_date']
                date_str = event_date.strftime('%b %d') if event_date else '???'
                lines.append(f"  - [{date_str}] {event['event_summary']}")
                if event.get('source_name'):
                    lines.append(f"    (Source: {event['source_name']})")

        return "\n".join(lines)

    def get_story_arcs_for_digest(
        self,
        digest_topic: str,
        min_events: int = 2,
        exclude_included: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get story arcs ready for digest/newsletter inclusion.

        Args:
            digest_topic: Parent topic name
            min_events: Minimum events to be considered for digest
            exclude_included: Exclude already-included arcs

        Returns:
            List of story arcs with events, sorted by relevance
        """
        arcs = self.get_active_story_arcs(digest_topic)

        # Filter by minimum events
        arcs = [a for a in arcs if a['event_count'] >= min_events]

        # Exclude already included
        if exclude_included:
            arcs = [a for a in arcs if a['included_in_digest_id'] is None]

        # Sort by event_count (more events = more significant story)
        arcs.sort(key=lambda a: (a['event_count'], a['source_count']), reverse=True)

        return arcs

    def mark_story_arc_included(
        self,
        story_arc_id: int,
        digest_id: int
    ) -> None:
        """
        Mark a story arc as included in a digest.

        Args:
            story_arc_id: Story arc ID
            digest_id: Digest ID
        """
        now = datetime.now(timezone.utc)

        query = """
            UPDATE story_arcs
            SET included_in_digest_id = %s, included_at = %s, updated_at = %s
            WHERE id = %s
        """

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (digest_id, now, now, story_arc_id))
                conn.commit()

    def cleanup_old_story_arcs(self, days: int = None) -> int:
        """
        Delete story arcs older than retention period.

        Args:
            days: Override retention days

        Returns:
            Number of arcs deleted
        """
        if days is None:
            days = self.get_setting('story_arcs', 'retention_days', 14)

        # Note: story_arc_events are deleted via CASCADE
        query = """
            DELETE FROM story_arcs
            WHERE last_updated_at < NOW() - INTERVAL '%s days'
            RETURNING id
        """

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (days,))
                deleted = cur.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old story arcs")
                return deleted
