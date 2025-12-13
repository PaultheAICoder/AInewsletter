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
