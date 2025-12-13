"""
SemanticTopicMatcher: Finds semantically similar topics using embeddings.
Used both during extraction (to reuse existing topics) and in dedupe process.
"""

import os
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class TopicMatch:
    """Result of a semantic topic match."""
    topic_id: int
    topic_slug: str
    topic_name: str
    similarity_score: float
    key_points: List[str]


class SemanticTopicMatcher:
    """
    Finds semantically similar topics using embedding similarity.

    Used to:
    1. Check if a new topic matches existing ones before creation
    2. Find duplicate topics for consolidation in dedupe process
    """

    def __init__(self, similarity_threshold: float = 0.80, db_client=None):
        """
        Initialize SemanticTopicMatcher.

        Args:
            similarity_threshold: Minimum similarity (0.0-1.0) to consider topics as matching.
                                 Default 0.80 means 80% similar = same topic.
            db_client: Database client for fetching settings
        """
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key)
        self.similarity_threshold = similarity_threshold
        self._embedding_cache: Dict[str, np.ndarray] = {}

        # Load embedding model from web_settings
        if db_client:
            self.embedding_model = db_client.get_setting('topic_evolution', 'embedding_model', 'text-embedding-3-small')
        else:
            self.embedding_model = "text-embedding-3-small"

    def find_matching_topic(
        self,
        new_topic_name: str,
        new_key_points: List[str],
        existing_topics: List[Dict],
        digest_topic: str = None
    ) -> Optional[TopicMatch]:
        """
        Find the best matching existing topic for a new topic.

        Args:
            new_topic_name: Name of the new topic
            new_key_points: Key points from the new topic
            existing_topics: List of existing topic dicts with topic_name, topic_slug, key_points, id
            digest_topic: Optional filter to only match within same digest topic

        Returns:
            TopicMatch if a match found above threshold, None otherwise
        """
        if not existing_topics:
            return None

        # Create text representation for new topic
        new_text = self._topic_to_text(new_topic_name, new_key_points)

        try:
            new_embedding = self._get_embedding(new_text)
        except Exception as e:
            logger.error(f"Failed to get embedding for new topic '{new_topic_name}': {e}")
            return None

        best_match = None
        best_similarity = 0.0

        for existing in existing_topics:
            # Filter by digest_topic if specified
            if digest_topic and existing.get('digest_topic') != digest_topic:
                continue

            existing_text = self._topic_to_text(
                existing.get('topic_name', ''),
                existing.get('key_points', [])
            )

            if not existing_text.strip():
                continue

            try:
                existing_embedding = self._get_embedding(existing_text)
            except Exception as e:
                logger.warning(f"Failed to get embedding for existing topic {existing.get('id')}: {e}")
                continue

            similarity = self._cosine_similarity(new_embedding, existing_embedding)

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = existing

        if best_match and best_similarity >= self.similarity_threshold:
            logger.info(
                f"Found semantic match: '{new_topic_name}' -> '{best_match.get('topic_name')}' "
                f"(similarity: {best_similarity:.3f})"
            )
            return TopicMatch(
                topic_id=best_match.get('id'),
                topic_slug=best_match.get('topic_slug'),
                topic_name=best_match.get('topic_name'),
                similarity_score=best_similarity,
                key_points=best_match.get('key_points', [])
            )

        return None

    def find_duplicate_groups(
        self,
        topics: List[Dict],
        similarity_threshold: float = None
    ) -> List[List[Dict]]:
        """
        Find groups of duplicate topics that should be consolidated.

        Args:
            topics: List of topic dicts to check for duplicates
            similarity_threshold: Override instance threshold if needed

        Returns:
            List of groups, where each group contains duplicate topics
            First item in each group is the "canonical" (oldest/most mentioned)
        """
        threshold = similarity_threshold or self.similarity_threshold

        if not topics:
            return []

        # Get embeddings for all topics
        topic_embeddings = []
        for topic in topics:
            text = self._topic_to_text(
                topic.get('topic_name', ''),
                topic.get('key_points', [])
            )
            try:
                embedding = self._get_embedding(text)
                topic_embeddings.append((topic, embedding))
            except Exception as e:
                logger.warning(f"Failed to embed topic {topic.get('id')}: {e}")
                continue

        # Find groups using union-find approach
        n = len(topic_embeddings)
        parent = list(range(n))

        def find(x):
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Compare all pairs
        for i in range(n):
            for j in range(i + 1, n):
                similarity = self._cosine_similarity(
                    topic_embeddings[i][1],
                    topic_embeddings[j][1]
                )
                if similarity >= threshold:
                    union(i, j)

        # Group by root
        groups_dict: Dict[int, List[Dict]] = {}
        for i, (topic, _) in enumerate(topic_embeddings):
            root = find(i)
            if root not in groups_dict:
                groups_dict[root] = []
            groups_dict[root].append(topic)

        # Filter to groups with duplicates and sort by oldest first
        duplicate_groups = []
        for group in groups_dict.values():
            if len(group) > 1:
                # Sort by first_mentioned_at (oldest first) then by mention_count (highest first)
                group.sort(key=lambda t: (
                    t.get('first_mentioned_at') or t.get('created_at'),
                    -t.get('mention_count', 1)
                ))
                duplicate_groups.append(group)

        return duplicate_groups

    def get_topic_names_for_prompt(
        self,
        existing_topics: List[Dict],
        max_topics: int = 50
    ) -> str:
        """
        Generate a formatted list of existing topic names for GPT prompt.

        This helps GPT reuse existing topic names instead of creating slight variations.

        Args:
            existing_topics: List of existing topics
            max_topics: Maximum number to include

        Returns:
            Formatted string of topic names for prompt inclusion
        """
        if not existing_topics:
            return ""

        # Dedupe by slug and get most recent
        seen_slugs = set()
        unique_topics = []

        # Sort by recency
        sorted_topics = sorted(
            existing_topics,
            key=lambda t: t.get('last_mentioned_at') or t.get('created_at'),
            reverse=True
        )

        for topic in sorted_topics:
            slug = topic.get('topic_slug')
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                unique_topics.append(topic.get('topic_name'))
                if len(unique_topics) >= max_topics:
                    break

        return "\n".join(f"- {name}" for name in unique_topics)

    def _topic_to_text(self, name: str, key_points: List[str]) -> str:
        """Convert topic name and key points to searchable text."""
        parts = [name]
        if key_points:
            parts.extend(key_points)
        return " ".join(parts)

    def _get_embedding(self, text: str) -> np.ndarray:
        """Get embedding vector for text, using cache."""
        cache_key = text[:500]  # Truncate for cache key

        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=text[:8000]  # Truncate to model limit
        )
        embedding = np.array(response.data[0].embedding)

        # Cache (limit cache size)
        if len(self._embedding_cache) < 1000:
            self._embedding_cache[cache_key] = embedding

        return embedding

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def clear_cache(self):
        """Clear embedding cache."""
        self._embedding_cache.clear()
